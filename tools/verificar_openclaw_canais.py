#!/usr/bin/env python3
"""Heartbeat com autorreparo para gateway, agentes e canais do OpenClaw.

Os probes sao somente leitura. O reparo limita-se a reiniciar servicos e a
restaurar a invariavel conhecida de respostas visiveis nos grupos. Nenhuma
mensagem e enviada aos grupos; a unica escrita externa possivel e um alerta
privado depois de o reparo automatico falhar.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
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
    processo: subprocess.Popen[str] | None = None
    try:
        processo = subprocess.Popen(
            comando,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=ambiente,
            start_new_session=True,
        )
        stdout, stderr = processo.communicate(timeout=timeout)
        codigo = int(processo.returncode or 0)
        # Alguns comandos OpenClaw encerram o pai e deixam workers no mesmo
        # grupo. O heartbeat nunca precisa preservar esses descendentes.
        try:
            os.killpg(processo.pid, signal.SIGTERM)
            time.sleep(0.2)
            os.killpg(processo.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return ResultadoComando(codigo, stdout, stderr)
    except subprocess.TimeoutExpired:
        if processo is not None:
            try:
                os.killpg(processo.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                processo.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            try:
                os.killpg(processo.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            if processo.poll() is None:
                processo.communicate()
        return ResultadoComando(124, "", "")
    except OSError:
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
    entradas = agentes.get("entries")
    if isinstance(entradas, dict):
        itens_agentes = [
            {"id": agente_id, **(item if isinstance(item, dict) else {})}
            for agente_id, item in entradas.items()
        ]
    elif isinstance(entradas, list):
        itens_agentes = entradas
    else:
        itens_agentes = agentes.get("list") or []
    vistos: set[tuple[str, tuple[str, ...]]] = set()
    configuracoes = []
    for item in itens_agentes:
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


def resultados_probe_modelos(payload: Any) -> list[Any] | None:
    """Extrai apenas a lista estruturalmente valida; ``None`` indica falha do probe."""
    if not isinstance(payload, dict):
        return None
    auth = payload.get("auth")
    probes = auth.get("probes") if isinstance(auth, dict) else None
    resultados = probes.get("results") if isinstance(probes, dict) else None
    return resultados if isinstance(resultados, list) else None


def validar_probe_modelos(
    payload: Any,
    *,
    primario: str,
    fallbacks: list[str],
) -> list[str]:
    if not isinstance(payload, dict):
        return ["probe_modelos_falhou"]
    resultados = resultados_probe_modelos(payload)
    if not resultados:
        return ["probe_modelos_sem_resposta"]
    por_modelo: dict[str, list[str]] = {}
    for item in resultados:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("model"), str)
            or not item["model"]
            or not isinstance(item.get("status"), str)
            or not item["status"]
        ):
            continue
        por_modelo.setdefault(item["model"], []).append(item["status"])

    falhas = []
    statuses_primario = por_modelo.get(primario, [])
    if not statuses_primario:
        falhas.append(f"modelo_primario_sem_resposta:{primario}")
    elif not any(status in STATUS_MODELO_OK for status in statuses_primario):
        falhas.append(f"modelo_primario_indisponivel:{primario}")
    for modelo in fallbacks:
        statuses_fallback = por_modelo.get(modelo, [])
        if not statuses_fallback:
            falhas.append(f"modelo_fallback_sem_resposta:{modelo}")
        elif not any(status in STATUS_MODELO_OK for status in statuses_fallback):
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
    modelos_com_resposta: set[str] = set()
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
        itens = resultados_probe_modelos(payload)
        if itens is None:
            falhas.append(f"probe_modelos_falhou:{item['agente']}")
            continue
        respostas_validas = 0
        for resultado in itens:
            if not isinstance(resultado, dict):
                continue
            resultados.append(resultado)
            if (
                isinstance(resultado.get("model"), str)
                and resultado["model"]
                and isinstance(resultado.get("status"), str)
                and resultado["status"]
            ):
                modelos_com_resposta.add(resultado["model"])
                respostas_validas += 1
        if respostas_validas == 0:
            falhas.append(f"probe_modelos_falhou:{item['agente']}")

    for item in configuracoes:
        modelos_esperados = {item["primario"], *item["fallbacks"]}
        if not modelos_esperados.issubset(modelos_com_resposta):
            falhas.append(f"probe_modelos_falhou:{item['agente']}")

    consolidado = {"auth": {"probes": {"results": resultados}}}
    primarios = sorted({item["primario"] for item in configuracoes})
    fallbacks = sorted({
        modelo
        for item in configuracoes
        for modelo in item["fallbacks"]
        if modelo not in primarios
    })
    for modelo in primarios:
        if modelo not in modelos_com_resposta:
            continue
        falhas.extend(validar_probe_modelos(
            consolidado, primario=modelo, fallbacks=[],
        ))
    if primarios:
        falhas.extend(
            falha for falha in validar_probe_modelos(
                consolidado,
                primario=primarios[0],
                fallbacks=[modelo for modelo in fallbacks if modelo in modelos_com_resposta],
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


def validar_indices_sessoes(
    *,
    cache: Path,
    intervalo: int,
    forcar: bool = False,
) -> list[str]:
    """Executa a manutenção preventiva pesada em cadência própria."""
    agora = int(time.time())
    if not forcar:
        try:
            anterior = json.loads(cache.read_text(encoding="utf-8"))
            if agora - int(anterior.get("timestamp") or 0) < intervalo:
                return []
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    falhas: list[str] = []
    for agente in AGENTES_OBRIGATORIOS:
        indice = json_comando([
            "openclaw", "sessions", "cleanup", "--agent", agente,
            "--dry-run", "--fix-missing", "--json",
        ], timeout=120)
        falhas.extend(validar_indice_sessoes(indice, agente))
    if not falhas:
        gravar_json_atomico(cache, {"timestamp": agora})
    return falhas


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


def resolver_segredo_op(valor: Any) -> str | None:
    if isinstance(valor, str):
        return valor or None
    if not isinstance(valor, dict):
        return None
    if valor.get("provider") != "op" or not valor.get("id"):
        return None
    if not os.environ.get("OP_SERVICE_ACCOUNT_TOKEN"):
        return None
    resultado = executar(["op", "read", str(valor["id"])], timeout=30)
    segredo = resultado.stdout.strip()
    return segredo if resultado.codigo == 0 and segredo else None


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


def validar_agentes_configuracao(configuracao: dict[str, Any]) -> list[str]:
    """Valida estrutura e arquivos sem iniciar outro runtime OpenClaw."""
    agentes = configuracao.get("agents") or {}
    entradas = agentes.get("entries")
    if isinstance(entradas, list):
        por_id = {
            str(item.get("id")): item
            for item in entradas if isinstance(item, dict) and item.get("id")
        }
    elif isinstance(entradas, dict):
        por_id = {
            str(agente_id): item if isinstance(item, dict) else {}
            for agente_id, item in entradas.items()
        }
    else:
        return ["agentes_configuracao_invalida"]
    bindings = configuracao.get("bindings") or []
    contagem = {
        agente: sum(
            1 for item in bindings
            if isinstance(item, dict) and str(item.get("agentId")) == agente
        )
        for agente in AGENTES_OBRIGATORIOS
    }
    falhas: list[str] = []
    for agente, minimo_vinculos in AGENTES_OBRIGATORIOS.items():
        item = por_id.get(agente)
        if item is None:
            falhas.append(f"agente_ausente:{agente}")
            continue
        if contagem.get(agente, 0) < minimo_vinculos:
            falhas.append(f"agente_sem_vinculo:{agente}")
        workspace = Path(str(item.get("workspace") or "")).expanduser()
        agent_dir = Path(str(
            item.get("agentDir") or f"/root/.openclaw/agents/{agente}/agent"
        )).expanduser()
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
    return falhas


def validar_status_gateway(payload: Any) -> list[str]:
    """Valida a projeção online sem disputar os bancos locais do Gateway."""
    if not isinstance(payload, dict):
        return ["probe_gateway_falhou"]
    if "ready" in payload:
        if payload.get("ready") is not True or payload.get("failing"):
            return ["gateway_health_indisponivel"]
        return []
    falhas = []
    # ``openclaw health`` já é a prova de alcance do Gateway e evita a
    # materialização pesada de ``status --deep``. Mantemos a leitura do
    # envelope antigo para compatibilidade com diagnósticos salvos.
    health = payload.get("health") if "health" in payload else payload
    health = health if isinstance(health, dict) else {}
    gateway = payload.get("gateway")
    if isinstance(gateway, dict) and gateway.get("reachable") is not True:
        falhas.append("gateway_indisponivel")
    if health.get("ok") is not True:
        falhas.append("gateway_health_indisponivel")
    runtime = health.get("modelRuntime") or {}
    if runtime.get("degraded") is True or runtime.get("pendingAgents"):
        falhas.append("runtime_modelos_degradado")
    return falhas


def consultar_readyz(url: str) -> Any:
    """Consulta o endpoint local barato usado pelo próprio systemd."""
    try:
        requisicao = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(requisicao, timeout=5) as resposta:
            if resposta.status != 200:
                return None
            return json.loads(resposta.read().decode("utf-8"))
    except (OSError, TimeoutError, json.JSONDecodeError, urllib.error.URLError):
        return None


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


def validar_whatsapp_por_logs(logs_gateway: str) -> list[str]:
    """Usa o estado mais recente do provedor sem carregar o CLI completo."""
    texto = logs_gateway.lower()
    pos_escuta = texto.rfind("listening for whatsapp inbound messages")
    pos_fechou = texto.rfind("web connection closed")
    pos_logout = max(
        texto.rfind("whatsapp session logged out"),
        texto.rfind("session logged out during setup"),
    )
    if pos_logout > pos_escuta:
        return ["whatsapp_openclaw_reautenticacao_necessaria"]
    if pos_escuta < 0 or pos_fechou > pos_escuta:
        return ["whatsapp_openclaw_indisponivel"]
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


def consultar_telegram_bot(
    token: str, metodo: str, parametros: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    dados = urllib.parse.urlencode(parametros or {}).encode()
    requisicao = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{metodo}",
        data=dados,
        method="POST",
        headers={"Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(requisicao, timeout=20) as resposta:
            payload = json.loads(resposta.read().decode("utf-8"))
            return payload if isinstance(payload, dict) else None
    except (OSError, TimeoutError, json.JSONDecodeError, urllib.error.URLError):
        return None


def validar_diretorio_grupos(
    configuracao: dict[str, Any],
    *,
    ambiente: dict[str, str],
    cache: Path,
    intervalo: int,
    forcar: bool = False,
) -> list[str]:
    """Confere a associação dos bots diretamente, sem iniciar outro OpenClaw."""
    agora = int(time.time())
    if not forcar:
        try:
            anterior = json.loads(cache.read_text(encoding="utf-8"))
            if agora - int(anterior.get("timestamp") or 0) < intervalo:
                return sorted(set(str(item) for item in anterior.get("falhas") or []))
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    telegram = ((configuracao.get("channels") or {}).get("telegram") or {})
    contas = telegram.get("accounts") or {}
    falhas: list[str] = []
    for conta in CONTAS_TELEGRAM:
        conta_cfg = contas.get(conta) if isinstance(contas, dict) else None
        conta_cfg = conta_cfg if isinstance(conta_cfg, dict) else {}
        token = resolver_segredo_op(conta_cfg.get("botToken"))
        esperados = grupos_configurados(configuracao, conta)
        if not token:
            falhas.append(f"diretorio_grupos_falhou:{conta}")
            continue
        identidade = consultar_telegram_bot(token, "getMe")
        usuario_id = ((identidade or {}).get("result") or {}).get("id")
        if (identidade or {}).get("ok") is not True or not usuario_id:
            falhas.append(f"diretorio_grupos_falhou:{conta}")
            continue
        inacessivel = False
        for grupo in sorted(esperados):
            membro = consultar_telegram_bot(token, "getChatMember", {
                "chat_id": grupo,
                "user_id": str(usuario_id),
            })
            status = ((membro or {}).get("result") or {}).get("status")
            if (membro or {}).get("ok") is not True or status in {None, "left", "kicked"}:
                inacessivel = True
        if inacessivel:
            falhas.append(f"grupo_telegram_inacessivel:{conta}")

    falhas = sorted(set(falhas))
    gravar_json_atomico(cache, {"timestamp": agora, "falhas": falhas})
    return falhas


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
    if not unidade_ativa(args.wey_health_timer):
        falhas.append("wey_health_timer_inativo")

    try:
        configuracao = json.loads(args.config.read_text())
    except (OSError, json.JSONDecodeError):
        return falhas + ["config_openclaw_invalida"]

    visiveis = (((configuracao.get("messages") or {}).get("groupChat") or {})
                .get("visibleReplies"))
    if visiveis != "automatic":
        falhas.append("respostas_visiveis_incorretas")

    token = resolver_token_gateway(configuracao)
    if not token:
        return falhas + ["token_gateway_indisponivel"]
    ambiente = dict(os.environ)
    ambiente["OPENCLAW_GATEWAY_TOKEN"] = token

    # Observabilidade online vem antes das verificações locais mais pesadas.
    # Assim, manutenção de índices nunca bloqueia nem falseia a saúde atual.
    status_gateway = consultar_readyz(args.gateway_ready_url)
    falhas.extend(validar_status_gateway(status_gateway))

    logs_gateway = executar([
        "journalctl", "--user", "-u", args.gateway_service,
        "--no-pager", "-n", "1000", "-o", "cat",
    ], timeout=30)
    falhas.extend(validar_whatsapp_por_logs(
        logs_gateway.stdout if logs_gateway.codigo == 0 else "",
    ))

    falhas.extend(validar_diretorio_grupos(
        configuracao,
        ambiente=ambiente,
        cache=args.diretorio_grupos_cache,
        intervalo=args.diretorio_grupos_intervalo,
        forcar=args.forcar_diretorio_grupos,
    ))

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

    falhas.extend(validar_agentes_configuracao(configuracao))
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
    if "wey_health_timer_inativo" in falhas and reiniciar(args.wey_health_timer):
        acoes.append("wey_health_timer_reiniciado")

    for agente in AGENTES_OBRIGATORIOS:
        if f"indice_sessoes_inconsistente:{agente}" in falhas:
            if reparar_indice_sessoes(agente):
                acoes.append(f"indice_sessoes_reparado:{agente}")

    # Falha de Bot API, diretório ou probe de canal deve alertar, não derrubar
    # um Gateway cuja RPC de saúde respondeu. Reinício fica reservado para a
    # indisponibilidade confirmada do próprio Gateway.
    falhas_gateway = [
        falha for falha in falhas
        if falha in {"gateway_indisponivel", "gateway_health_indisponivel"}
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
    parser.add_argument(
        "--gateway-ready-url", default="http://127.0.0.1:18789/readyz"
    )
    parser.add_argument("--ocr-service", default="juan-compra-ocr-worker.service")
    parser.add_argument(
        "--confinex-bridge-service",
        default="juan-confinex-db-bridge.service",
    )
    parser.add_argument("--dns-service", default="systemd-resolved.service")
    parser.add_argument("--chrome-service", default="openclaw-chrome.service")
    parser.add_argument("--wacli-service", default="wey-whatsapp-live-sync.service")
    parser.add_argument("--wacli-health-service", default="wey-whatsapp-live-health.service")
    parser.add_argument(
        "--wey-health-timer", default="wey-whatsapp-official-health.timer"
    )
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
        "--diretorio-grupos-cache", type=Path,
        default=Path("/root/.openclaw/state/group-directory-heartbeat.json"),
    )
    parser.add_argument("--diretorio-grupos-intervalo", type=int, default=4 * 60)
    parser.add_argument("--forcar-diretorio-grupos", action="store_true")
    parser.add_argument(
        "--indice-sessoes-cache", type=Path,
        default=Path("/root/.openclaw/state/session-index-heartbeat.json"),
    )
    parser.add_argument("--indice-sessoes-intervalo", type=int, default=6 * 60 * 60)
    parser.add_argument("--forcar-indice-sessoes", action="store_true")
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
