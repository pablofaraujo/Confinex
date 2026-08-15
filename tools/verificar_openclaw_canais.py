#!/usr/bin/env python3
"""Heartbeat com autorreparo para gateway, agentes e canais do OpenClaw.

Os probes sao somente leitura. O reparo limita-se a reiniciar servicos e a
restaurar a invariavel conhecida de respostas visiveis nos grupos. Nenhuma
mensagem e enviada aos grupos; a unica escrita externa possivel e um alerta
privado depois de o reparo automatico falhar.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import socket
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


AGENTES_OBRIGATORIOS = {"juan": 1, "ceci": 1, "wey": 1, "zeus": 0}
CONTAS_TELEGRAM = ("default", "ceci")


@dataclass
class ResultadoComando:
    codigo: int
    stdout: str
    stderr: str


def executar(comando: list[str], *, timeout: int = 60,
             ambiente: dict[str, str] | None = None) -> ResultadoComando:
    try:
        resultado = subprocess.run(
            comando,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=ambiente,
        )
        return ResultadoComando(resultado.returncode, resultado.stdout, resultado.stderr)
    except (subprocess.TimeoutExpired, OSError):
        return ResultadoComando(124, "", "")


def json_comando(comando: list[str], *, timeout: int = 60,
                 ambiente: dict[str, str] | None = None) -> dict[str, Any] | list[Any] | None:
    resultado = executar(comando, timeout=timeout, ambiente=ambiente)
    if resultado.codigo != 0:
        return None
    try:
        return json.loads(resultado.stdout)
    except json.JSONDecodeError:
        return None


def unidade_ativa(unidade: str, *, usuario: bool = False) -> bool:
    comando = ["systemctl"] + (["--user"] if usuario else [])
    comando += ["is-active", "--quiet", unidade]
    return executar(comando, timeout=20).codigo == 0


def resolver_token_gateway(configuracao: dict[str, Any]) -> str | None:
    referencia = (((configuracao.get("gateway") or {}).get("auth") or {}).get("token") or {})
    if not isinstance(referencia, dict):
        return None
    identificador = referencia.get("id")
    if not identificador or not os.environ.get("OP_SERVICE_ACCOUNT_TOKEN"):
        return None
    resultado = executar(["op", "read", str(identificador)], timeout=30)
    token = resultado.stdout.strip()
    return token if resultado.codigo == 0 and token else None


def validar_agentes(payload: Any) -> list[str]:
    falhas: list[str] = []
    if not isinstance(payload, list):
        return ["agentes_listagem_falhou"]
    por_id = {item.get("id"): item for item in payload if isinstance(item, dict)}
    for agente, minimo_vinculos in AGENTES_OBRIGATORIOS.items():
        item = por_id.get(agente)
        if not item:
            falhas.append(f"agente_ausente:{agente}")
            continue
        if int(item.get("bindings") or 0) < minimo_vinculos:
            falhas.append(f"agente_sem_vinculo:{agente}")
        workspace = Path(str(item.get("workspace") or ""))
        agent_dir = Path(str(item.get("agentDir") or ""))
        if not workspace.is_dir():
            falhas.append(f"workspace_ausente:{agente}")
        if not (agent_dir / "AGENT.md").is_file():
            falhas.append(f"agent_md_ausente:{agente}")
    return falhas


def validar_canais(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return ["probe_canais_falhou"]
    falhas: list[str] = []
    contas = payload.get("channelAccounts") or {}
    telegram = {
        item.get("accountId"): item
        for item in contas.get("telegram", [])
        if isinstance(item, dict)
    }
    for conta in CONTAS_TELEGRAM:
        item = telegram.get(conta) or {}
        if not all((item.get("configured"), item.get("running"),
                    (item.get("probe") or {}).get("ok"))):
            falhas.append(f"telegram_indisponivel:{conta}")

    whatsapp = (payload.get("channels") or {}).get("whatsapp") or {}
    if not all((whatsapp.get("configured"), whatsapp.get("linked"),
                whatsapp.get("running"), whatsapp.get("connected"),
                whatsapp.get("healthState") == "healthy")):
        falhas.append("whatsapp_openclaw_indisponivel")
    evento = payload.get("eventLoop") or {}
    if evento.get("degraded") is True:
        falhas.append("gateway_event_loop_degradado")
    return falhas


def validar_confinex(ambiente: dict[str, str] | None = None) -> list[str]:
    """Confirma DNS, TLS e uma leitura autenticada mínima no Supabase."""
    env = ambiente or os.environ
    base_url = str(env.get("CONFINEX_DB_URL") or "").rstrip("/")
    chave = str(env.get("CONFINEX_DB_KEY") or "")
    if not base_url or not chave:
        return ["confinex_config_indisponivel"]
    host = urllib.parse.urlparse(base_url).hostname
    if not host:
        return ["confinex_url_invalida"]
    try:
        socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return ["confinex_dns_indisponivel"]

    requisicao = urllib.request.Request(
        f"{base_url}/rest/v1/operacoes?select=id&limit=1",
        headers={
            "apikey": chave,
            "Authorization": f"Bearer {chave}",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(requisicao, timeout=20) as resposta:
            if 200 <= resposta.status < 300:
                return []
            return [f"confinex_rest_http_{resposta.status}"]
    except urllib.error.HTTPError as exc:
        return [f"confinex_rest_http_{exc.code}"]
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, socket.gaierror):
            return ["confinex_dns_indisponivel"]
        return ["confinex_rest_indisponivel"]
    except (TimeoutError, OSError):
        return ["confinex_rest_indisponivel"]


def ids_grupos(payload: Any) -> set[str] | None:
    if isinstance(payload, list):
        itens = payload
    elif isinstance(payload, dict):
        itens = payload.get("groups", payload.get("items"))
    else:
        return None
    if not isinstance(itens, list):
        return None
    return {str(item.get("id")) for item in itens if isinstance(item, dict) and item.get("id")}


def grupos_configurados(configuracao: dict[str, Any], conta: str) -> set[str]:
    telegram = ((configuracao.get("channels") or {}).get("telegram") or {})
    conta_cfg = ((telegram.get("accounts") or {}).get(conta) or {})
    grupos = conta_cfg.get("groups") or {}
    return {str(chave) for chave in grupos.keys()} if isinstance(grupos, dict) else set()


def diagnosticar(args: argparse.Namespace) -> list[str]:
    falhas: list[str] = []
    if not unidade_ativa(args.gateway_service, usuario=True):
        falhas.append("gateway_servico_inativo")
    if not unidade_ativa(args.ocr_service, usuario=True):
        falhas.append("ocr_worker_inativo")
    if not unidade_ativa(args.confinex_bridge_service, usuario=True):
        falhas.append("confinex_bridge_inativa")
    if not unidade_ativa(args.chrome_service):
        falhas.append("chrome_openclaw_inativo")
    if not unidade_ativa(args.wacli_service):
        falhas.append("wacli_continuo_inativo")

    try:
        configuracao = json.loads(args.config.read_text())
    except (OSError, json.JSONDecodeError):
        return falhas + ["config_openclaw_invalida"]

    visiveis = (((configuracao.get("messages") or {}).get("groupChat") or {})
                .get("visibleReplies"))
    if visiveis != "automatic":
        falhas.append("respostas_visiveis_incorretas")

    if executar(["openclaw", "config", "validate"], timeout=60).codigo != 0:
        falhas.append("validacao_config_falhou")

    falhas.extend(validar_confinex())

    agentes = json_comando(["openclaw", "agents", "list", "--bindings", "--json"])
    falhas.extend(validar_agentes(agentes))

    token = resolver_token_gateway(configuracao)
    if not token:
        return falhas + ["token_gateway_indisponivel"]
    ambiente = dict(os.environ)
    ambiente["OPENCLAW_GATEWAY_TOKEN"] = token

    canais = json_comando(
        ["openclaw", "channels", "status", "--probe", "--json"],
        timeout=60,
        ambiente=ambiente,
    )
    falhas.extend(validar_canais(canais))

    def listar(conta: str) -> tuple[str, Any]:
        payload = json_comando([
            "openclaw", "directory", "groups", "list", "--channel", "telegram",
            "--account", conta, "--limit", "500", "--json",
        ], timeout=75, ambiente=ambiente)
        return conta, payload

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        resultados = dict(executor.map(listar, CONTAS_TELEGRAM))
    for conta in CONTAS_TELEGRAM:
        encontrados = ids_grupos(resultados.get(conta))
        esperados = grupos_configurados(configuracao, conta)
        if encontrados is None:
            falhas.append(f"diretorio_grupos_falhou:{conta}")
        elif not esperados.issubset(encontrados):
            falhas.append(f"grupo_telegram_inacessivel:{conta}")
    return sorted(set(falhas))


def reiniciar(unidade: str, *, usuario: bool = False) -> bool:
    base = ["systemctl"] + (["--user"] if usuario else [])
    executar(base + ["reset-failed", unidade], timeout=20)
    return executar(base + ["restart", unidade], timeout=60).codigo == 0


def reparar(args: argparse.Namespace, falhas: list[str]) -> list[str]:
    acoes: list[str] = []
    if "respostas_visiveis_incorretas" in falhas:
        backup = args.backup_dir / f"openclaw.json.{int(time.time())}"
        args.backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copy2(args.config, backup)
        if executar([
            "openclaw", "config", "set", "messages.groupChat.visibleReplies", "automatic"
        ], timeout=60).codigo == 0:
            acoes.append("respostas_visiveis_restauradas")

    if "ocr_worker_inativo" in falhas and reiniciar(args.ocr_service, usuario=True):
        acoes.append("ocr_worker_reiniciado")
    if "confinex_bridge_inativa" in falhas and reiniciar(
        args.confinex_bridge_service, usuario=True
    ):
        acoes.append("confinex_bridge_reiniciada")
    if "chrome_openclaw_inativo" in falhas and reiniciar(args.chrome_service):
        acoes.append("chrome_reiniciado")
    if "wacli_continuo_inativo" in falhas:
        if executar(["systemctl", "start", args.wacli_health_service], timeout=90).codigo == 0:
            acoes.append("wacli_reparado")

    gatilhos_gateway = (
        "gateway_", "telegram_", "whatsapp_openclaw_", "probe_canais_",
        "diretorio_grupos_", "grupo_telegram_", "token_gateway_", "confinex_",
    )
    if any(falha.startswith(gatilhos_gateway) for falha in falhas):
        if reiniciar(args.gateway_service, usuario=True):
            acoes.append("gateway_reiniciado")
    return acoes


def notificar(args: argparse.Namespace, falhas: list[str]) -> bool:
    token = os.environ.get("TELEGRAM_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT", "")
    if not token or not chat or args.sem_alerta:
        return False
    agora = int(time.time())
    try:
        ultimo = int(args.alert_state.read_text())
    except (OSError, ValueError):
        ultimo = 0
    if agora - ultimo < args.cooldown_alerta:
        return False
    args.alert_state.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    dados = urllib.parse.urlencode({
        "chat_id": chat,
        "text": "OpenClaw: autorreparo nao recuperou todos os canais/agentes. "
                f"Falhas tecnicas: {', '.join(falhas)}",
    }).encode()
    requisicao = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=dados, method="POST"
    )
    try:
        with urllib.request.urlopen(requisicao, timeout=20) as resposta:
            sucesso = 200 <= resposta.status < 300
    except Exception:
        sucesso = False
    if sucesso:
        args.alert_state.write_text(str(agora))
        os.chmod(args.alert_state, 0o600)
    return sucesso


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("/root/.openclaw/openclaw.json"))
    parser.add_argument("--gateway-service", default="openclaw-gateway.service")
    parser.add_argument("--ocr-service", default="juan-compra-ocr-worker.service")
    parser.add_argument(
        "--confinex-bridge-service",
        default="juan-confinex-db-bridge.service",
    )
    parser.add_argument("--chrome-service", default="openclaw-chrome.service")
    parser.add_argument("--wacli-service", default="wey-whatsapp-live-sync.service")
    parser.add_argument("--wacli-health-service", default="wey-whatsapp-live-health.service")
    parser.add_argument("--backup-dir", type=Path,
                        default=Path("/root/.openclaw/state/heartbeat-backups"))
    parser.add_argument("--alert-state", type=Path,
                        default=Path("/root/.openclaw/state/agent-heartbeat-last-alert"))
    parser.add_argument("--cooldown-alerta", type=int, default=1800)
    parser.add_argument("--espera-reparo", type=float, default=15)
    parser.add_argument("--reparar", action="store_true")
    parser.add_argument("--sem-alerta", action="store_true")
    args = parser.parse_args()

    falhas = diagnosticar(args)
    acoes: list[str] = []
    if falhas and args.reparar:
        acoes = reparar(args, falhas)
        time.sleep(max(0, args.espera_reparo))
        falhas = diagnosticar(args)
    alerta = notificar(args, falhas) if falhas else False
    saida = {
        "saudavel": not falhas,
        "falhas": falhas,
        "acoes_reparo": acoes,
        "alerta_enviado": alerta,
    }
    print(json.dumps(saida, ensure_ascii=False, sort_keys=True))
    return 0 if not falhas else 1


if __name__ == "__main__":
    raise SystemExit(main())
