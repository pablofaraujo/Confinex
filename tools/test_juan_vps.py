#!/usr/bin/env python3
"""Verificador efêmero do Juan executado pelo test_ecossistema.py na VPS."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid
import urllib.request
from pathlib import Path
from typing import Any


CONFIG = globals().get("CONFIG_TESTE", {})
BASE = Path("/root/juan-severino")
HANDLERS = BASE / "handlers"
WORKSPACE = Path("/root/.openclaw/workspace")
SESSIONS = Path("/root/.openclaw/agents/juan/sessions")
ENV_FILE = Path("/root/.openclaw/gateway.systemd.env")
TABLES = (
    "abates",
    "compras",
    "contexto_handoff",
    "eventos",
    "memorias_agentes",
    "operation_drafts",
    "pending_actions",
    "pesagens_caderno",
    "vendas",
)
STATUS_EVENTO_VALIDOS = {"cancelado", "corrigido", "pendente", "registrado"}


def carregar_env() -> dict[str, str]:
    env = dict(os.environ)
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                env.setdefault(key, value)
    return env


ENV = carregar_env()


def run(
    command: list[str],
    *,
    timeout: int = 600,
    cwd: Path = BASE,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        command,
        cwd=cwd,
        env=ENV,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if process.returncode:
        raise RuntimeError(
            f"{' '.join(command)} falhou: "
            f"{(process.stderr or process.stdout).strip()[:1600]}"
        )
    return process


def json_output(command: list[str], *, timeout: int = 600) -> dict[str, Any]:
    process = run(command, timeout=timeout)
    try:
        value = json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"saída não JSON de {' '.join(command)}: {process.stdout[:1000]}"
        ) from exc
    if not isinstance(value, dict):
        raise RuntimeError("saída JSON inesperada")
    return value


def snapshot() -> dict[str, dict[str, Any]]:
    base = ENV.get("CONFINEX_DB_URL", "").rstrip("/")
    key = ENV.get("CONFINEX_DB_KEY", "")
    if not base or not key:
        raise RuntimeError("credenciais de leitura do Confinex não disponíveis")
    output = {}
    for table in TABLES:
        rows = supabase_rows(table, "id")
        ids = [str(row["id"]) for row in rows]
        output[table] = {
            "quantidade": len(ids),
            "assinatura": hashlib.sha256("\n".join(ids).encode()).hexdigest(),
        }
    return output


def supabase_rows(table: str, select: str) -> list[dict[str, Any]]:
    base = ENV["CONFINEX_DB_URL"].rstrip("/")
    key = ENV["CONFINEX_DB_KEY"]
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        url = (
            f"{base}/rest/v1/{table}?select={select}&order=id.asc"
            f"&limit=1000&offset={offset}"
        )
        request = urllib.request.Request(
            url,
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            lote = json.load(response)
        rows.extend(lote)
        if len(lote) < 1000:
            return rows
        offset += len(lote)


def validar_eventos() -> list[str]:
    rows = supabase_rows("eventos", "id,status")
    statuses = sorted({str(row.get("status")) for row in rows})
    invalidos = set(statuses) - STATUS_EVENTO_VALIDOS
    if invalidos:
        raise RuntimeError("status de evento inválido: " + ", ".join(invalidos))
    return statuses


def validar_prompt() -> None:
    effective = (WORKSPACE / "AGENTS.md").read_text(encoding="utf-8")
    skill = (WORKSPACE / "skills/pesagem-ocr/SKILL.md").read_text(encoding="utf-8")
    combined = effective + "\n" + skill
    for required in ("MediaPath", "MediaPaths", "arquivo_grupo_router.py"):
        if required not in combined:
            raise RuntimeError(f"instrução efetiva não contém {required}")
    texto = combined.lower()
    bloqueios_escrita = (
        "não salve",
        "nao salve",
        "nada foi salvo",
        "nenhuma leitura gera escrita",
        "nunca use `--criar-rascunho`",
    )
    if not any(regra in texto for regra in bloqueios_escrita):
        raise RuntimeError("instrução efetiva não bloqueia gravação automática")


def validar_arquivo(path_text: str, legenda: str, grupo_id: str) -> dict[str, Any]:
    path = Path(path_text)
    if not path.is_file():
        raise RuntimeError(f"arquivo real não encontrado: {path}")
    if path.suffix.lower() not in {".pdf", ".jpg", ".jpeg", ".png", ".webp"}:
        raise RuntimeError(f"formato real não aceito: {path.suffix}")

    ocr = json_output(
        [sys.executable, str(HANDLERS / "compra_documento_ocr.py"), str(path)]
    )
    if ocr.get("ocr_origem") != "openclaw_openai":
        raise RuntimeError(f"OCR externo não foi usado para {path.suffix}")
    if ocr.get("eh_compra"):
        for field in ("vendedor", "quantidade"):
            if ocr.get(field) in (None, ""):
                raise RuntimeError(f"compra lida sem {field}")

    route = json_output(
        [
            sys.executable,
            str(HANDLERS / "arquivo_grupo_router.py"),
            str(path),
            "--grupo-id",
            grupo_id,
            "--mensagem-id",
            f"teste-ecossistema-{uuid.uuid4().hex}",
            "--texto",
            legenda,
            "--dry-run",
        ]
    )
    if route.get("dry_run") is not True or "rascunho" in route:
        raise RuntimeError("roteador real não permaneceu em dry-run")
    routed = route.get("routed") or {}
    if routed.get("classe") == "compra_extraida":
        dados = routed.get("dados") or {}
        if not isinstance(dados.get("campos_pendentes"), list):
            raise RuntimeError("compra não informou lista objetiva de pendências")
        if dados.get("valor_calculado") is None:
            faltantes = " ".join(dados.get("campos_pendentes") or [])
            if not any(
                item in faltantes
                for item in ("peso", "preco", "quantidade", "desconto")
            ):
                raise RuntimeError(
                    "compra ficou sem cálculo e sem explicar o dado necessário"
                )
    return {
        "formato": path.suffix.lower(),
        "classe": routed.get("classe"),
        "ocr": ocr.get("ocr_origem"),
    }


def calls_from_trajectory(path: Path) -> list[dict[str, Any]]:
    calls = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "tool.call":
            calls.append(event.get("data") or {})
    return calls


def limpar_sessao(marker: str) -> None:
    for path in SESSIONS.glob("*.jsonl"):
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if marker in path.name or marker in content:
            path.unlink(missing_ok=True)


def validar_agente(path_text: str, legenda: str, grupo_id: str) -> None:
    marker = f"teste-ecossistema-{uuid.uuid4().hex}"
    message = (
        f"[Telegram grupo {grupo_id} mensagem {marker}] {legenda} "
        f"MediaPath: {path_text} MediaPaths: {path_text}"
    )
    try:
        run(
            [
                "openclaw",
                "agent",
                "--agent",
                "juan",
                "--session-id",
                marker,
                "--message",
                message,
                "--thinking",
                "low",
                "--timeout",
                "600",
                "--json",
            ],
            timeout=700,
        )
        trajectory = SESSIONS / f"{marker}.trajectory.jsonl"
        if not trajectory.exists():
            matches = [
                p
                for p in SESSIONS.glob("*.trajectory.jsonl")
                if marker in p.read_text(encoding="utf-8", errors="ignore")
            ]
            if not matches:
                raise RuntimeError("trajetória do teste do agente não foi encontrada")
            trajectory = matches[0]
        calls = calls_from_trajectory(trajectory)
        media_index = None
        for index, call in enumerate(calls):
            name = str(call.get("name") or "")
            args = json.dumps(call.get("arguments") or {}, ensure_ascii=False)
            if path_text in args or name in {"pdf", "image"}:
                media_index = index
                if (
                    name != "bash"
                    or "arquivo_grupo_router.py" not in args
                    or "--dry-run" not in args
                ):
                    raise RuntimeError(
                        "a primeira ferramenta de mídia não foi o roteador em dry-run"
                    )
                break
        if media_index is None:
            raise RuntimeError("o Juan não processou MediaPath/MediaPaths")
        before = json.dumps(calls[:media_index], ensure_ascii=False).lower()
        if any(token in before for token in ("pdftoppm", '"name": "pdf"', '"name": "image"')):
            raise RuntimeError("ferramenta interna foi usada antes do roteador")
    finally:
        limpar_sessao(marker)


def limpar_temporarios() -> None:
    for cache in (
        WORKSPACE / ".juan-ocr-jobs/cache",
        HANDLERS / "__pycache__",
    ):
        if cache.exists():
            if cache.name == "cache":
                for path in cache.glob("*.json"):
                    path.unlink(missing_ok=True)
            else:
                shutil.rmtree(cache)
    run(
        [
            "openclaw",
            "sessions",
            "cleanup",
            "--agent",
            "juan",
            "--fix-missing",
            "--enforce",
            "--json",
        ],
        timeout=120,
    )


def main() -> int:
    before = snapshot()
    evidencias: list[dict[str, Any]] = []
    try:
        py_files = sorted(str(path) for path in HANDLERS.glob("*.py"))
        run([sys.executable, "-m", "py_compile", *py_files])
        run(
            [
                sys.executable,
                "-m",
                "unittest",
                "discover",
                "-s",
                str(HANDLERS),
                "-p",
                "test_*.py",
                "-v",
            ]
        )
        run(["openclaw", "config", "validate"])
        for service in (
            "openclaw-gateway.service",
            "juan-compra-ocr-worker.service",
        ):
            state = run(["systemctl", "--user", "is-active", service]).stdout.strip()
            if state != "active":
                raise RuntimeError(f"{service} não está ativo")
        validar_prompt()
        statuses = validar_eventos()

        grupo_id = str(CONFIG.get("grupo_id") or "")
        arquivos = [
            (CONFIG.get("pdf"), CONFIG.get("legenda_pdf") or ""),
            (CONFIG.get("foto"), CONFIG.get("legenda_foto") or ""),
        ]
        if any(path for path, _ in arquivos) and not grupo_id:
            raise RuntimeError("grupo_id é obrigatório para testar arquivos reais")
        for path, legenda in arquivos:
            if not path:
                continue
            evidencias.append(validar_arquivo(str(path), str(legenda), grupo_id))
            if CONFIG.get("testar_agente"):
                validar_agente(str(path), str(legenda), grupo_id)

        after = snapshot()
        if before != after:
            raise RuntimeError("assinaturas do Supabase mudaram durante os testes")
        print(
            json.dumps(
                {
                    "ok": True,
                    "py_compile": True,
                    "testes_handlers": True,
                    "openclaw_config": True,
                    "gateway": "active",
                    "trabalhador_ocr": "active",
                    "status_eventos": statuses,
                    "arquivos_reais": evidencias,
                    "agente_simulado": bool(
                        CONFIG.get("testar_agente") and evidencias
                    ),
                    "supabase_inalterado": True,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        limpar_temporarios()


if __name__ == "__main__":
    raise SystemExit(main())
