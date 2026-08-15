#!/usr/bin/env python3
"""Ponte local entre o sandbox do Juan e a API REST do Confinex.

O agente grava pedidos numa fila privada. O worker supervisionado do host faz
a chamada de rede. Leituras podem ser repetidas; escritas nunca são repetidas.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any


QUEUE_DIR = Path(
    os.environ.get(
        "JUAN_CONFINEX_QUEUE_DIR",
        "/root/.openclaw/workspace/.juan-confinex-jobs",
    )
)
MAX_REQUEST_BYTES = 128 * 1024
NETWORK_TIMEOUT = 20
READ_ATTEMPTS = 5
RUNNING = True

READ_RESOURCES = {
    "abates", "acertos", "compras", "confinamento_contatos", "confinamentos",
    "confinex_avaliacoes", "confinex_consolidacoes", "confinex_desvios",
    "confinex_estimativas", "confinex_testes", "contatos", "contexto_handoff",
    "contextos_canais", "crm_followups", "custos_operacao", "documentos",
    "entradas_confinamento", "eventos", "fluxo_caixa", "gtas", "interacoes_crm",
    "memorias_agentes", "negociacoes_gado", "negocios_boi_balanca", "ofertas_gado",
    "operacoes", "operation_drafts", "pending_actions", "pendencias_documentos",
    "pesagens", "pesagens_caderno", "vendas", "v_estoque_atual",
    "v_exposicao_hedge",
}
REVIEW_WRITE_RESOURCES = {
    "contexto_handoff", "contextos_canais", "crm_followups", "eventos",
    "interacoes_crm", "memorias_agentes", "negociacoes_gado", "ofertas_gado",
    "operation_drafts", "pending_actions",
}
READ_ACTIONS = {
    "get_read", "get_last_codigo", "get_contato", "get_confinamento",
    "get_pendencias", "get_operacao",
}
WRITE_ACTIONS = {
    "post_contato", "post_confinamento", "post_operacao", "post_compra",
    "post_pendencia", "patch_status", "post_review", "patch_review",
}
ALL_ACTIONS = READ_ACTIONS | WRITE_ACTIONS


class BridgeError(RuntimeError):
    """Erro seguro, sem credenciais ou payload."""


def stop(_signum: int, _frame: Any) -> None:
    global RUNNING
    RUNNING = False


def require_args(action: str, args: list[str], count: int) -> None:
    if len(args) != count:
        raise BridgeError(f"argumentos_invalidos:{action}")


def json_body(action: str, value: str) -> bytes:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise BridgeError(f"json_invalido:{action}") from exc
    if not isinstance(parsed, dict):
        raise BridgeError(f"json_deve_ser_objeto:{action}")
    return json.dumps(parsed, ensure_ascii=False, separators=(",", ":")).encode()


def validate_route(route: str, *, action: str) -> str:
    if (
        len(route) > 4096
        or "\r" in route
        or "\n" in route
        or "://" in route
        or route.startswith("/")
        or ".." in route
    ):
        raise BridgeError(f"rota_invalida:{action}")
    return route


def action_request(action: str, args: list[str]) -> tuple[str, str, bytes | None]:
    if action not in ALL_ACTIONS:
        raise BridgeError(f"acao_nao_permitida:{action}")
    if action == "get_last_codigo":
        require_args(action, args, 0)
        return "GET", "operacoes?select=codigo&order=codigo.desc&limit=1", None
    if action == "get_read":
        require_args(action, args, 1)
        route = validate_route(args[0], action=action)
        resource = route.split("?", 1)[0]
        if resource not in READ_RESOURCES:
            raise BridgeError(f"recurso_leitura_nao_permitido:{resource}")
        return "GET", route, None
    if action == "post_review":
        require_args(action, args, 2)
        table = args[0]
        if table not in REVIEW_WRITE_RESOURCES:
            raise BridgeError(f"recurso_revisao_nao_permitido:{table}")
        return "POST", table, json_body(action, args[1])
    if action == "patch_review":
        require_args(action, args, 3)
        table, query = args[0], validate_route(args[1], action=action)
        if table not in REVIEW_WRITE_RESOURCES:
            raise BridgeError(f"recurso_revisao_nao_permitido:{table}")
        if not query or query.startswith("?") or query.split("?", 1)[0] in READ_RESOURCES:
            raise BridgeError("filtro_revisao_invalido")
        return "PATCH", f"{table}?{query}", json_body(action, args[2])
    if action == "get_contato":
        require_args(action, args, 1)
        from urllib.parse import quote
        return "GET", f"contatos?nome=ilike.*{quote(args[0], safe='')}*&select=id,nome,tipos", None
    if action == "get_confinamento":
        require_args(action, args, 1)
        from urllib.parse import quote
        return "GET", f"confinamentos?nome=eq.{quote(args[0], safe='')}&select=id,nome", None
    if action == "get_pendencias":
        require_args(action, args, 1)
        from urllib.parse import quote
        return "GET", f"pendencias_documentos?operacao_id=eq.{quote(args[0], safe='')}&select=tipo,status", None
    if action == "get_operacao":
        require_args(action, args, 1)
        from urllib.parse import quote
        return "GET", f"operacoes?codigo=eq.{quote(args[0], safe='')}&select=*,compras(*),pendencias_documentos(tipo,status)", None
    if action == "patch_status":
        require_args(action, args, 2)
        from urllib.parse import quote
        return "PATCH", f"operacoes?codigo=eq.{quote(args[0], safe='')}", json_body(action, json.dumps({"status": args[1]}))

    require_args(action, args, 1)
    table = {
        "post_contato": "contatos",
        "post_confinamento": "confinamentos",
        "post_operacao": "operacoes",
        "post_compra": "compras",
        "post_pendencia": "pendencias_documentos",
    }[action]
    return "POST", table, json_body(action, args[0])


def execute_action(
    action: str,
    args: list[str],
    *,
    url: str,
    key: str,
    opener: Any = urllib.request.urlopen,
) -> dict[str, Any]:
    if not url or not key:
        raise BridgeError("credenciais_confinex_indisponiveis")
    method, route, body = action_request(action, args)
    request = urllib.request.Request(
        f"{url.rstrip('/')}/rest/v1/{route}",
        data=body,
        method=method,
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        },
    )
    attempts = READ_ATTEMPTS if method == "GET" else 1
    for attempt in range(1, attempts + 1):
        try:
            with opener(request, timeout=NETWORK_TIMEOUT) as response:
                return {
                    "body": response.read().decode("utf-8"),
                    "http_status": int(response.status),
                }
        except urllib.error.HTTPError as exc:
            return {
                "body": exc.read().decode("utf-8", errors="replace"),
                "http_status": int(exc.code),
            }
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt == attempts:
                raise BridgeError(
                    f"rede_indisponivel:{method}:{type(exc).__name__}:tentativas={attempts}"
                ) from exc
            time.sleep(min(0.5 * (2 ** (attempt - 1)), 4.0))
    raise BridgeError("falha_interna_ponte")


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def process_request(path: Path, *, url: str, key: str) -> None:
    job_id = path.name.removesuffix(".request.json")
    result_path = QUEUE_DIR / f"{job_id}.result.json"
    error_path = QUEUE_DIR / f"{job_id}.error.json"
    try:
        if path.stat().st_size > MAX_REQUEST_BYTES:
            raise BridgeError("pedido_excede_limite")
        request = json.loads(path.read_text(encoding="utf-8"))
        action, args = request.get("action"), request.get("args")
        if not isinstance(action, str) or not isinstance(args, list) or not all(isinstance(value, str) for value in args):
            raise BridgeError("pedido_invalido")
        atomic_json(result_path, execute_action(action, args, url=url, key=key))
    except (BridgeError, json.JSONDecodeError, OSError) as exc:
        atomic_json(error_path, {"error": str(exc)[:240]})
    finally:
        path.unlink(missing_ok=True)


def run_worker() -> int:
    url = os.environ.get("CONFINEX_DB_URL") or os.environ.get("SUPABASE_URL") or ""
    key = os.environ.get("CONFINEX_DB_KEY") or os.environ.get("SUPABASE_SERVICE_KEY") or ""
    if not url or not key:
        raise BridgeError("credenciais_confinex_indisponiveis")
    QUEUE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(QUEUE_DIR, 0o700)
    ready = QUEUE_DIR / ".ready"
    ready.write_text(str(os.getpid()), encoding="utf-8")
    os.chmod(ready, 0o600)
    try:
        while RUNNING:
            for path in sorted(QUEUE_DIR.glob("*.request.json")):
                process_request(path, url=url, key=key)
            time.sleep(0.1)
    finally:
        ready.unlink(missing_ok=True)
    return 0


def run_client(action: str, args: list[str], timeout: float) -> int:
    if action not in ALL_ACTIONS:
        raise BridgeError(f"acao_nao_permitida:{action}")
    QUEUE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not (QUEUE_DIR / ".ready").exists():
        raise BridgeError("ponte_confinex_indisponivel")
    job_id = uuid.uuid4().hex
    request_path = QUEUE_DIR / f"{job_id}.request.json"
    result_path = QUEUE_DIR / f"{job_id}.result.json"
    error_path = QUEUE_DIR / f"{job_id}.error.json"
    atomic_json(request_path, {"action": action, "args": args})
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if result_path.exists():
                result = json.loads(result_path.read_text(encoding="utf-8"))
                print(result.get("body", ""))
                print(f"HTTP_STATUS:{int(result['http_status'])}")
                return 0 if 200 <= int(result["http_status"]) < 300 else 1
            if error_path.exists():
                error = json.loads(error_path.read_text(encoding="utf-8"))
                raise BridgeError(str(error.get("error") or "falha_na_ponte"))
            time.sleep(0.1)
        raise BridgeError("tempo_excedido_na_ponte")
    finally:
        request_path.unlink(missing_ok=True)
        result_path.unlink(missing_ok=True)
        error_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--timeout", type=float, default=35.0)
    parser.add_argument("action", nargs="?")
    parser.add_argument("args", nargs="*")
    options = parser.parse_args()
    try:
        if options.worker:
            return run_worker()
        if not options.action:
            parser.error("ação obrigatória")
        return run_client(options.action, options.args, max(1.0, min(options.timeout, 60.0)))
    except BridgeError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    raise SystemExit(main())
