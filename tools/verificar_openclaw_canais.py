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
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


AGENTES_OBRIGATORIOS = {"juan": 1, "ceci": 1, "wey": 1, "zeus": 0}
CONTAS_TELEGRAM = ("default", "ceci")
STATUS_MODELO_OK = {"ok"}


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


def gravar_json_atomico(caminho: Path, payload: dict[str, Any]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporario = caminho.with_suffix(f"{caminho.suffix}.{os.getpid()}.tmp")
    temporario.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    os.chmod(temporario, 0o600)
    os.replace(temporario, caminho)


def contrato_modelo(valor: Any) -> tuple[str, tuple[str, ...]]:
    if isinstance(valor, str):
        return valor, ()
    if not isinstance(valor, dict):
        return "", ()
    primario = str(valor.get("primary") or "")
    fallbacks = tuple(
        str(item) for item in (valor.get("fallbacks") or []) if item
    )
    return primario, fallbacks


def configuracoes_modelos(configuracao: dict[str, Any]) -> list[dict[str, Any]]:
    agentes = (configuracao.get("agents") or {})
    padrao = contrato_modelo((agentes.get("defaults") or {}).get("model"))
    vistos: set[tuple[str, tuple[str, ...]]] = set()
    configuracoes = []
    for item in agentes.get("list") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        modelo = contrato_modelo(item.get("model"))
        if not modelo[0]:
            modelo = padrao
        if not modelo[0] or modelo in vistos:
            continue
        vistos.add(modelo)
        configuracoes.append({
            "agente": str(item["id"]),
            "primario": modelo[0],
            "fallbacks": list(modelo[1]),
        })
    return configuracoes


def validar_probe_modelos(
    payload: Any,
    *,
    primario: str,
    fallbacks: list[str],
) -> list[str]:
    if not isinstance(payload, dict):
        return ["probe_modelos_falhou"]
    resultados = ((((payload.get("auth") or {}).get("probes") or {})
                   .get("results")) or [])
    por_modelo: dict[str, list[str]] = {}
    for item in resultados:
        if not isinstance(item, dict) or not item.get("model"):
            continue
        por_modelo.setdefault(str(item["model"]), []).append(
            str(item.get("status") or "desconhecido")
        )

    falhas = []
    if not any(status in STATUS_MODELO_OK for status in por_modelo.get(primario, [])):
        falhas.append(f"modelo_primario_indisponivel:{primario}")
    for modelo in fallbacks:
        if not any(status in STATUS_MODELO_OK for status in por_modelo.get(modelo, [])):
            falhas.append(f"modelo_fallback_indisponivel:{modelo}")
    return falhas


def validar_modelos(
    configuracao: dict[str, Any],
    *,
    ambiente: dict[str, str],
    cache: Path,
    intervalo: int,
    forcar: bool = False,
) -> list[str]:
    agora = int(time.time())
    if not forcar:
        try:
            anterior = json.loads(cache.read_text(encoding="utf-8"))
            if agora - int(anterior.get("timestamp") or 0) < intervalo:
                return sorted(set(str(item) for item in anterior.get("falhas") or []))
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    configuracoes = configuracoes_modelos(configuracao)
    resultados: list[dict[str, Any]] = []
    falhas = []
    for item in configuracoes:
        payload = json_comando([
            "openclaw", "models", "--agent", item["agente"], "status",
            "--probe", "--json", "--probe-concurrency", "2",
            "--probe-max-tokens", "1", "--probe-timeout", "15000",
        ], timeout=60, ambiente=ambiente)
        if not isinstance(payload, dict):
            falhas.append(f"probe_modelos_falhou:{item['agente']}")
            continue
        itens = ((((payload.get("auth") or {}).get("probes") or {})
                 .get("results")) or [])
        resultados.extend(item for item in itens if isinstance(item, dict))

    consolidado = {"auth": {"probes": {"results": resultados}}}
    primarios = sorted({item["primario"] for item in configuracoes})
    fallbacks = sorted({
        modelo
        for item in configuracoes
        for modelo in item["fallbacks"]
        if modelo not in primarios
    })
    for modelo in primarios:
        falhas.extend(validar_probe_modelos(
            consolidado, primario=modelo, fallbacks=[],
        ))
    if primarios:
        falhas.extend(
            falha for falha in validar_probe_modelos(
                consolidado, primario=primarios[0], fallbacks=fallbacks,
            )
            if falha.startswith("modelo_fallback_indisponivel:")
        )
    falhas = sorted(set(falhas))
    gravar_json_atomico(cache, {"timestamp": agora, "falhas": falhas})
    return falhas


def criar_xlsx_minimo(caminho: Path) -> None:
    arquivos = {
        "[Content_Types].xml": """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>""",
        "_rels/.rels": """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""",
        "xl/workbook.xml": """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="Heartbeat" sheetId="1" r:id="rId1"/></sheets></workbook>""",
        "xl/_rels/workbook.xml.rels": """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>""",
        "xl/worksheets/sheet1.xml": """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Teste</t></is></c></row></sheetData></worksheet>""",
    }
    with zipfile.ZipFile(caminho, "w", compression=zipfile.ZIP_DEFLATED) as pacote:
        for nome, conteudo in arquivos.items():
            pacote.writestr(nome, conteudo)


def validar_roteador_xlsx(roteador: Path) -> list[str]:
    if not roteador.is_file():
        return ["roteador_arquivo_ausente"]
    with tempfile.TemporaryDirectory(prefix="openclaw-heartbeat-") as pasta:
        caminho = Path(pasta) / "heartbeat.xlsx"
        criar_xlsx_minimo(caminho)
        resultado = executar([
            "python3", str(roteador), str(caminho),
            "--grupo-id", "1", "--mensagem-id", "heartbeat-xlsx",
            "--dry-run", "--no-ocr",
        ], timeout=30)
    if resultado.codigo != 0:
        return ["roteador_xlsx_indisponivel"]
    try:
        payload = json.loads(resultado.stdout)
        routed = payload.get("routed") or {}
        if (
            payload.get("dry_run") is True
            and routed.get("classe") == "planilha_xlsx"
            and (routed.get("dados") or {}).get("importado") is False
        ):
            return []
    except json.JSONDecodeError:
        pass
    return ["roteador_xlsx_indisponivel"]


def validar_indice_sessoes(payload: Any, agente: str) -> list[str]:
    """Detecta referencias de sessao cujo arquivo ja nao existe.

    O ``cleanup --dry-run --fix-missing`` nao aplica retencao nem remove
    arquivos. Mantemos a falha por agente para que o reparo posterior seja
    estritamente localizado.
    """
    if not isinstance(payload, dict):
        return [f"probe_indice_sessoes_falhou:{agente}"]
    try:
        ausentes = int(payload.get("missing") or 0)
    except (TypeError, ValueError):
        return [f"probe_indice_sessoes_falhou:{agente}"]
    if ausentes > 0:
        return [f"indice_sessoes_inconsistente:{agente}"]
    return []


def reparar_indice_sessoes(agente: str) -> bool:
    """Remove somente referencias ausentes, nunca sessoes ou artefatos validos."""
    comando_base = [
        "openclaw", "sessions", "cleanup", "--agent", agente,
        "--fix-missing", "--json",
    ]
    previa = json_comando(comando_base[:-1] + ["--dry-run", "--json"], timeout=120)
    if not isinstance(previa, dict) or int(previa.get("missing") or 0) <= 0:
        return False
    if any(previa.get(campo) for campo in ("dmScopeRetired", "pruned", "capped")):
        return False
    artefatos = previa.get("unreferencedArtifacts") or {}
    if artefatos.get("removedFiles"):
        return False
    aplicado = json_comando(comando_base, timeout=120)
    if not isinstance(aplicado, dict):
        return False
    conferencia = json_comando(
        comando_base[:-1] + ["--dry-run", "--json"], timeout=120,
    )
    return isinstance(conferencia, dict) and int(conferencia.get("missing") or 0) == 0


def unidade_ativa(unidade: str, *, usuario: bool = False) -> bool:
    comando = ["systemctl"] + (["--user"] if usuario else [])
    comando += ["is-active", "--quiet", unidade]
    return executar(comando, timeout=20).codigo == 0


def validar_autenticacao_wacli(
    binario: Path, store: Path, unidade: str = "wey-whatsapp-live-sync.service",
) -> list[str]:
    payload = json_comando([
        str(binario), "--store", str(store), "--read-only", "--json", "doctor",
    ], timeout=30)
    if not isinstance(payload, dict):
        return ["wacli_diagnostico_indisponivel"]
    dados = payload.get("data") or {}
    if not isinstance(dados, dict):
        return ["wacli_diagnostico_indisponivel"]
    if dados.get("authenticated") is not True:
        return ["wacli_reautenticacao_necessaria"]
    try:
        desde = int((store / "session.db").stat().st_mtime)
    except OSError:
        return ["wacli_reautenticacao_necessaria"]
    logs = executar([
        "journalctl", "-u", unidade, "--since", f"@{desde}",
        "--no-pager", "-o", "cat",
    ], timeout=30)
    texto = logs.stdout.lower() if logs.codigo == 0 else ""
    if any(marcador in texto for marcador in (
        "401: logged out from another device",
        "not authenticated; run `wacli auth`",
    )):
        return ["wacli_reautenticacao_necessaria"]
    return []


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


def validar_reautenticacao_whatsapp_openclaw(
    payload: Any, logs_gateway: str,
) -> list[str]:
    """Distingue indisponibilidade reparavel de sessao revogada.

    Logs antigos so bloqueiam o reparo enquanto o probe atual continuar
    indisponivel. Depois de um novo pareamento saudavel, nenhum marcador
    historico interfere no heartbeat.
    """
    if not isinstance(payload, dict):
        return []
    whatsapp = (payload.get("channels") or {}).get("whatsapp") or {}
    saudavel = all((
        whatsapp.get("configured"),
        whatsapp.get("linked"),
        whatsapp.get("running"),
        whatsapp.get("connected"),
        whatsapp.get("healthState") == "healthy",
    ))
    if saudavel:
        return []
    texto = logs_gateway.lower()
    if any(marcador in texto for marcador in (
        "whatsapp session logged out",
        "session logged out during setup",
    )):
        return ["whatsapp_openclaw_reautenticacao_necessaria"]
    return []


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


def validar_monitor_agronota(
    *,
    cron: str,
    log: Path,
    arquivos: tuple[Path, ...],
    agora: float | None = None,
    idade_maxima_segundos: int = 11 * 60 * 60,
) -> list[str]:
    """Prova que a busca fiscal proativa está instalada e executando.

    O heartbeat não chama a API do AgroNota a cada cinco minutos. Ele fiscaliza
    o agendamento das consultas controladas e a atualização do último log.
    """
    falhas: list[str] = []
    if any(not arquivo.is_file() for arquivo in arquivos):
        falhas.append("agronota_monitor_ausente")
    linhas_ativas = [
        linha.strip() for linha in cron.splitlines()
        if linha.strip() and not linha.lstrip().startswith("#")
    ]
    linhas_agronota = [linha for linha in linhas_ativas if "agronota_pipeline" in linha]
    if not any("30 4 * * *" in linha for linha in linhas_agronota):
        falhas.append("agronota_varredura_diaria_ausente")
    if not any("15 11,15,19 * * *" in linha for linha in linhas_agronota):
        falhas.append("agronota_incremental_ausente")
    try:
        idade = (time.time() if agora is None else agora) - log.stat().st_mtime
        if idade < 0 or idade > idade_maxima_segundos:
            falhas.append("agronota_monitor_atrasado")
    except OSError:
        falhas.append("agronota_log_ausente")
    return sorted(set(falhas))


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
    falhas.extend(validar_autenticacao_wacli(
        args.wacli_bin, args.wacli_store, args.wacli_service,
    ))

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
    falhas.extend(validar_roteador_xlsx(args.arquivo_router))

    cron = executar(["crontab", "-l"], timeout=20)
    if cron.codigo != 0:
        falhas.append("agronota_agendamento_indisponivel")
    else:
        falhas.extend(validar_monitor_agronota(
            cron=cron.stdout,
            log=args.agronota_log,
            arquivos=tuple(args.agronota_arquivos),
            idade_maxima_segundos=args.agronota_idade_maxima,
        ))

    agentes = json_comando(["openclaw", "agents", "list", "--bindings", "--json"])
    falhas.extend(validar_agentes(agentes))
    for agente in AGENTES_OBRIGATORIOS:
        indice = json_comando([
            "openclaw", "sessions", "cleanup", "--agent", agente,
            "--dry-run", "--fix-missing", "--json",
        ], timeout=120)
        falhas.extend(validar_indice_sessoes(indice, agente))

    token = resolver_token_gateway(configuracao)
    if not token:
        return falhas + ["token_gateway_indisponivel"]
    ambiente = dict(os.environ)
    ambiente["OPENCLAW_GATEWAY_TOKEN"] = token
    falhas.extend(validar_modelos(
        configuracao,
        ambiente=ambiente,
        cache=args.modelo_probe_cache,
        intervalo=args.modelo_probe_intervalo,
        forcar=args.forcar_probe_modelos,
    ))

    canais = json_comando(
        ["openclaw", "channels", "status", "--probe", "--json"],
        timeout=60,
        ambiente=ambiente,
    )
    falhas.extend(validar_canais(canais))
    logs_gateway = executar([
        "journalctl", "--user", "-u", args.gateway_service,
        "--since", "2 hours ago", "--no-pager", "-o", "cat",
    ], timeout=30)
    falhas.extend(validar_reautenticacao_whatsapp_openclaw(
        canais,
        logs_gateway.stdout if logs_gateway.codigo == 0 else "",
    ))

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
    if "confinex_dns_indisponivel" in falhas and reiniciar(args.dns_service):
        acoes.append("dns_resolver_reiniciado")
    if "chrome_openclaw_inativo" in falhas and reiniciar(args.chrome_service):
        acoes.append("chrome_reiniciado")
    if (
        "wacli_continuo_inativo" in falhas
        and "wacli_reautenticacao_necessaria" not in falhas
    ):
        if executar(["systemctl", "start", args.wacli_health_service], timeout=90).codigo == 0:
            acoes.append("wacli_reparado")

    for agente in AGENTES_OBRIGATORIOS:
        if f"indice_sessoes_inconsistente:{agente}" in falhas:
            if reparar_indice_sessoes(agente):
                acoes.append(f"indice_sessoes_reparado:{agente}")

    gatilhos_gateway = (
        "gateway_", "telegram_", "whatsapp_openclaw_", "probe_canais_",
        "diretorio_grupos_", "grupo_telegram_",
    )
    falhas_gateway = [
        falha for falha in falhas if falha.startswith(gatilhos_gateway)
    ]
    if "whatsapp_openclaw_reautenticacao_necessaria" in falhas:
        falhas_gateway = [
            falha for falha in falhas_gateway
            if falha not in {
                "whatsapp_openclaw_indisponivel",
                "whatsapp_openclaw_reautenticacao_necessaria",
                "gateway_event_loop_degradado",
            }
        ]
    if falhas_gateway:
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
    parser.add_argument("--dns-service", default="systemd-resolved.service")
    parser.add_argument("--chrome-service", default="openclaw-chrome.service")
    parser.add_argument("--wacli-service", default="wey-whatsapp-live-sync.service")
    parser.add_argument("--wacli-health-service", default="wey-whatsapp-live-health.service")
    parser.add_argument("--wacli-bin", type=Path, default=Path("/usr/local/bin/wacli"))
    parser.add_argument(
        "--wacli-store", type=Path,
        default=Path("/root/.local/state/wacli-confinex"),
    )
    parser.add_argument(
        "--arquivo-router", type=Path,
        default=Path("/root/juan-severino/handlers/arquivo_grupo_router.py"),
    )
    parser.add_argument(
        "--modelo-probe-cache", type=Path,
        default=Path("/root/.openclaw/state/model-probe-heartbeat.json"),
    )
    parser.add_argument("--modelo-probe-intervalo", type=int, default=30 * 60)
    parser.add_argument("--forcar-probe-modelos", action="store_true")
    parser.add_argument(
        "--agronota-log", type=Path,
        default=Path("/var/log/cfagro/agronota_pipeline.log"),
    )
    parser.add_argument(
        "--agronota-arquivos", type=Path, nargs="+",
        default=[
            Path("/root/ponte/tools/agronota_nf.py"),
            Path("/root/ponte/tools/monitorar_agronota.py"),
            Path("/root/.openclaw/workspace/skills/agronota/bin/download_new_nfs.py"),
        ],
    )
    parser.add_argument("--agronota-idade-maxima", type=int, default=11 * 60 * 60)
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
