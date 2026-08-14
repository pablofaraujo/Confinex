#!/usr/bin/env python3
"""Orquestra a conciliação privada do Wey e recupera lacunas do cache.

O fluxo escreve somente no cache técnico do wacli e nos relatórios privados
explicitamente informados. Não envia mensagens e não acessa bases operacionais.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


IGNORADOS = {
    "araujo", "carlos", "comissao", "da", "das", "de", "do", "dos", "e",
    "ferreira", "pablo", "seu",
}

ALIASES_TOKEN = {
    "francisco": ["chico"],
    "manoel": ["manuel"],
}


def normalizar(valor: str) -> str:
    texto = unicodedata.normalize("NFKD", valor or "")
    return " ".join(re.findall(r"[a-z0-9]+", texto.encode("ascii", "ignore").decode().lower()))


def tokens_negocio(nome: str) -> list[str]:
    return [token for token in normalizar(nome).split() if len(token) >= 4 and token not in IGNORADOS]


def executar_json(comando: list[str], *, somente_leitura: bool = True, timeout: int = 90) -> dict[str, Any]:
    ambiente = os.environ.copy()
    if somente_leitura:
        ambiente["WACLI_READONLY"] = "1"
    processo = subprocess.run(
        comando,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=ambiente,
    )
    resposta = json.loads(processo.stdout)
    if resposta.get("success") is False:
        raise RuntimeError(str(resposta.get("error") or "Falha no wacli"))
    return resposta


def comando_base(binario: Path, store: Path, *, somente_leitura: bool = True) -> list[str]:
    comando = [str(binario), "--store", str(store)]
    if somente_leitura:
        comando.extend(["--read-only", "--json"])
    return comando


def listar_chats(binario: Path, store: Path, consulta: str) -> list[dict[str, Any]]:
    resposta = executar_json(
        comando_base(binario, store) + ["chats", "list", "--query", consulta, "--limit", "20"]
    )
    dados = resposta.get("data") or []
    return [item for item in dados if isinstance(item, dict)]


def cobertura_chat(binario: Path, store: Path, jid: str) -> dict[str, Any] | None:
    resposta = executar_json(
        comando_base(binario, store) + ["history", "coverage", "--chat", jid]
    )
    linhas = (resposta.get("data") or {}).get("coverage") or []
    return linhas[0] if linhas else None


def data_negocio(item: dict[str, Any]) -> datetime | None:
    valor = item.get("data")
    if not valor:
        return None
    try:
        return datetime.strptime(str(valor)[:10], "%d/%m/%Y")
    except ValueError:
        return None


def pontuar_chat(nome_chat: str, tokens: list[str]) -> int:
    nome = set(normalizar(nome_chat).split())
    pontos = 0
    for token in tokens:
        melhor = 0
        for palavra in nome:
            if palavra == token or palavra in ALIASES_TOKEN.get(token, []):
                melhor = 20
                break
            if len(token) >= 5 and len(palavra) >= 5 and SequenceMatcher(None, token, palavra).ratio() >= 0.82:
                melhor = max(melhor, 15)
        pontos += melhor
    if pontos == 20 and len(tokens) == 1:
        return 40
    return pontos


def descobrir_candidatos(
    duvida: dict[str, Any],
    binario: Path,
    store: Path,
) -> list[dict[str, Any]]:
    tokens = tokens_negocio(str(duvida.get("negocio") or ""))
    vistos: dict[str, dict[str, Any]] = {}
    consultas = []
    for token in tokens[:3]:
        consultas.append(token)
        consultas.extend(ALIASES_TOKEN.get(token, []))
    for consulta in consultas:
        for chat in listar_chats(binario, store, consulta):
            jid = str(chat.get("jid") or "")
            if not jid or not jid.endswith("@s.whatsapp.net"):
                continue
            pontos = pontuar_chat(str(chat.get("name") or ""), tokens)
            if pontos < 20:
                continue
            atual = vistos.get(jid)
            if atual is None or pontos > atual["pontuacao"]:
                vistos[jid] = {**chat, "pontuacao": pontos}
    return sorted(vistos.values(), key=lambda item: (-item["pontuacao"], str(item.get("name") or "")))[:4]


def cobertura_suficiente(cobertura: dict[str, Any] | None, limite: datetime | None) -> bool:
    if not cobertura or not limite:
        return False
    bruto = str(cobertura.get("oldest_ts") or "")
    try:
        mais_antiga = datetime.fromisoformat(bruto.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return False
    return mais_antiga.date() <= limite.date()


def montar_plano(
    duvidas: list[dict[str, Any]],
    conciliacao: dict[str, Any],
    binario: Path,
    store: Path,
) -> list[dict[str, Any]]:
    resultados = {item.get("codigo"): item for item in conciliacao.get("resultados", [])}
    plano: list[dict[str, Any]] = []
    for duvida in duvidas:
        resultado = resultados.get(duvida.get("codigo"), {})
        if resultado.get("status") not in {"nao_encontrado", "sem_valor_para_busca"}:
            continue
        candidatos = []
        for chat in descobrir_candidatos(duvida, binario, store):
            cobertura = cobertura_chat(binario, store, str(chat["jid"]))
            candidatos.append({
                "jid": chat["jid"],
                "nome": chat.get("name") or "conversa sem nome",
                "pontuacao": chat["pontuacao"],
                "cobertura": cobertura,
                "precisa_backfill": not cobertura_suficiente(cobertura, data_negocio(duvida)),
            })
        plano.append({
            "codigo": duvida.get("codigo"),
            "negocio": duvida.get("negocio"),
            "status_atual": resultado.get("status"),
            "data": duvida.get("data"),
            "valores": duvida.get("valores") or [],
            "campos_faltantes": duvida.get("campos_faltantes") or "",
            "divergencias": duvida.get("divergencias") or "",
            "candidatos": candidatos,
        })
    return plano


def executar_backfills(
    plano: list[dict[str, Any]],
    binario: Path,
    store: Path,
    *,
    maximo: int,
    requisicoes: int,
    quantidade: int,
    espera: str,
    estado: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    estado = estado if estado is not None else {}
    historico = estado.setdefault("backfills", {})
    execucoes: list[dict[str, Any]] = []
    alvos: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for item in plano:
        for candidato in item["candidatos"]:
            if not candidato["precisa_backfill"]:
                continue
            jid = candidato["jid"]
            atual = alvos.get(jid)
            if atual is None or int(candidato.get("pontuacao", 0)) > int(atual[1].get("pontuacao", 0)):
                alvos[jid] = (item, candidato)
    ordenados = sorted(
        alvos.values(),
        key=lambda par: (
            int(historico.get(par[1]["jid"], {}).get("tentativas", 0)),
            str(historico.get(par[1]["jid"], {}).get("ultima_tentativa", "")),
            -int(par[1].get("pontuacao", 0)),
            str(par[0].get("codigo") or ""),
        ),
    )
    for item, candidato in ordenados[:maximo]:
        comando = comando_base(binario, store, somente_leitura=False) + [
            "history", "backfill", "--chat", candidato["jid"],
            "--count", str(quantidade), "--requests", str(requisicoes),
            "--wait", espera, "--idle-exit", "5s",
        ]
        ambiente = os.environ.copy()
        ambiente.pop("WACLI_READONLY", None)
        try:
            subprocess.run(
                comando,
                check=True,
                capture_output=True,
                text=True,
                timeout=max(120, requisicoes * 75),
                env=ambiente,
            )
            status = "executado"
        except subprocess.CalledProcessError as erro:
            diagnostico = f"{erro.stdout or ''}\n{erro.stderr or ''}".lower()
            status = (
                "sem_resposta_aparelho"
                if "timed out waiting for on-demand history sync response" in diagnostico
                else "falhou:CalledProcessError"
            )
        except subprocess.TimeoutExpired:
            status = "sem_resposta_aparelho"
        registro = historico.setdefault(candidato["jid"], {})
        registro["tentativas"] = int(registro.get("tentativas", 0)) + 1
        registro["ultima_tentativa"] = datetime.now().astimezone().isoformat()
        registro["ultimo_status"] = status
        registro["codigo"] = item["codigo"]
        execucoes.append({"codigo": item["codigo"], "jid": candidato["jid"], "status": status})
    return execucoes


def pergunta_pendente(item: dict[str, Any]) -> str:
    campos = item.get("campos_faltantes") or item.get("divergencias") or "dados do negócio"
    valores = ", ".join(item.get("valores") or [])
    referencia = f" Os valores candidatos são {valores}." if valores else ""
    return f"Confirmar {campos} do negócio {item.get('negocio')}.{referencia}".strip()


def executar_conciliador(args: argparse.Namespace) -> dict[str, Any]:
    comando = [
        str(args.python), str(args.conciliador),
        "--duvidas", str(args.duvidas),
        "--sessions-dir", str(args.sessions_dir),
        "--wacli-bin", str(args.wacli_bin),
        "--wacli-store", str(args.wacli_store),
        "--saida-json", str(args.saida_conciliacao_json),
        "--saida-md", str(args.saida_conciliacao_md),
    ]
    if args.sessions_index:
        comando.extend(["--sessions-index", str(args.sessions_index)])
    ambiente = os.environ.copy()
    ambiente["WACLI_READONLY"] = "1"
    subprocess.run(comando, check=True, capture_output=True, text=True, env=ambiente, timeout=180)
    return json.loads(args.saida_conciliacao_json.read_text())


def main() -> None:
    parser = argparse.ArgumentParser(description="Orquestra cache e conciliação privada do WhatsApp do Wey.")
    parser.add_argument("--duvidas", required=True, type=Path)
    parser.add_argument("--sessions-dir", required=True, type=Path)
    parser.add_argument("--sessions-index", type=Path)
    parser.add_argument("--wacli-bin", required=True, type=Path)
    parser.add_argument("--wacli-store", required=True, type=Path)
    parser.add_argument("--conciliador", required=True, type=Path)
    parser.add_argument("--python", type=Path, default=Path("/usr/bin/python3"))
    parser.add_argument("--saida-conciliacao-json", required=True, type=Path)
    parser.add_argument("--saida-conciliacao-md", required=True, type=Path)
    parser.add_argument("--saida-orquestracao", required=True, type=Path)
    parser.add_argument("--estado", type=Path)
    parser.add_argument("--executar-backfill", action="store_true")
    parser.add_argument("--max-backfills", type=int, default=6)
    parser.add_argument("--requisicoes", type=int, default=2)
    parser.add_argument("--quantidade", type=int, default=50)
    parser.add_argument("--espera", default="45s")
    args = parser.parse_args()

    bruto = json.loads(args.duvidas.read_text())
    duvidas = bruto.get("duvidas", bruto) if isinstance(bruto, dict) else bruto
    conciliacao = executar_conciliador(args)
    plano = montar_plano(duvidas, conciliacao, args.wacli_bin, args.wacli_store)
    estado: dict[str, Any] = {}
    if args.estado and args.estado.exists():
        estado = json.loads(args.estado.read_text())
    execucoes = []
    if args.executar_backfill:
        execucoes = executar_backfills(
            plano, args.wacli_bin, args.wacli_store,
            maximo=max(0, args.max_backfills), requisicoes=max(1, args.requisicoes),
            quantidade=max(1, args.quantidade), espera=args.espera, estado=estado,
        )
        if execucoes:
            conciliacao = executar_conciliador(args)
            plano = montar_plano(duvidas, conciliacao, args.wacli_bin, args.wacli_store)

    pendencias = [{
        "codigo": item["codigo"],
        "negocio": item["negocio"],
        "motivo": (
            "cobertura_incompleta"
            if any(c["precisa_backfill"] for c in item["candidatos"])
            else "evidencia_nao_localizada_com_cobertura"
            if item["candidatos"]
            else "conversa_candidata_nao_localizada"
        ),
        "pergunta_pronta": pergunta_pendente(item),
        "candidatos": item["candidatos"],
    } for item in plano]
    saida = {
        "gerado_em": datetime.now().astimezone().isoformat(),
        "modo": "cache_tecnico_e_relatorios_privados",
        "resumo_conciliacao": conciliacao.get("contagens", {}),
        "backfills": execucoes,
        "pendencias": pendencias,
        "controles": {
            "mensagens_enviadas": 0,
            "escritas_supabase": 0,
            "registros_operacionais_alterados": 0,
            "promocoes_executadas": 0,
        },
    }
    args.saida_orquestracao.parent.mkdir(parents=True, exist_ok=True)
    args.saida_orquestracao.write_text(json.dumps(saida, ensure_ascii=False, indent=2) + "\n")
    if args.estado:
        args.estado.parent.mkdir(parents=True, exist_ok=True)
        args.estado.write_text(json.dumps(estado, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({
        "modo": saida["modo"],
        "contagens": saida["resumo_conciliacao"],
        "backfills": len(execucoes),
        "pendencias": len(pendencias),
        "controles": saida["controles"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
