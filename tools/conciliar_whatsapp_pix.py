#!/usr/bin/env python3
"""Localiza evidências de PIX nos históricos do Wey sem executar ações externas.

A ferramenta é deliberadamente somente leitura: lê JSON/JSONL locais e grava
apenas os relatórios explicitamente informados na linha de comando. Não chama
WhatsApp, OpenClaw, Supabase ou qualquer API.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable


UUID_INICIO = re.compile(r"^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I)
PALAVRAS_PAGAMENTO = re.compile(r"\b(?:pix|comprovante|paguei|pago|pagamento|transfer[eê]ncia)\b", re.I)
PALAVRAS_TOKEN = re.compile(r"[a-zà-ÿ0-9]+", re.I)
ARQUIVOS_IGNORADOS = (".trajectory.jsonl", ".trajectory-path.json", ".codex-app-server.json")


@dataclass(frozen=True)
class Mensagem:
    id: str
    sessao_id: str
    conversa: str
    timestamp: str
    texto: str
    remetente: str | None
    arquivo: str
    linha: int


def carregar_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def centavos(valor: Any) -> int | None:
    if valor is None or valor == "":
        return None
    if isinstance(valor, (int, float, Decimal)):
        numero = Decimal(str(valor))
    else:
        bruto = str(valor).strip()
        bruto = re.sub(r"(?i)r\$", "", bruto).replace(" ", "")
        if not bruto:
            return None
        if "," in bruto:
            bruto = bruto.replace(".", "").replace(",", ".")
        elif bruto.count(".") > 1:
            bruto = bruto.replace(".", "")
        elif bruto.count(".") == 1:
            esquerda, direita = bruto.split(".")
            if len(direita) == 3:
                bruto = esquerda + direita
        try:
            numero = Decimal(bruto)
        except InvalidOperation:
            return None
    if numero <= 0:
        return None
    return int((numero * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def formatar_brl(valor_centavos: int) -> str:
    inteiro, resto = divmod(valor_centavos, 100)
    grupos = f"{inteiro:,}".replace(",", ".")
    return f"R$ {grupos},{resto:02d}"


def variantes_valor(valor_centavos: int) -> list[str]:
    inteiro, resto = divmod(valor_centavos, 100)
    agrupado_br = f"{inteiro:,}".replace(",", ".")
    agrupado_us = f"{inteiro:,}"
    candidatos = {
        f"{agrupado_br},{resto:02d}",
        f"{inteiro},{resto:02d}",
        f"{agrupado_us}.{resto:02d}",
        f"{inteiro}.{resto:02d}",
    }
    if resto == 0:
        candidatos.update({agrupado_br, str(inteiro)})
    return sorted(candidatos, key=lambda item: (-len(item), item))


def regex_valor(valor_centavos: int) -> re.Pattern[str]:
    alternativas = "|".join(re.escape(item) for item in variantes_valor(valor_centavos))
    return re.compile(rf"(?<!\d)(?:R\$\s*)?(?:{alternativas})(?!\d)", re.I)


def texto_conteudo(conteudo: Any) -> str:
    if isinstance(conteudo, str):
        return conteudo
    if not isinstance(conteudo, list):
        return ""
    partes: list[str] = []
    for item in conteudo:
        if isinstance(item, str):
            partes.append(item)
        elif isinstance(item, dict):
            texto = item.get("text") or item.get("input_text") or item.get("output_text")
            if texto:
                partes.append(str(texto))
    return "\n".join(partes)


def canal_mensagem(mensagem: dict[str, Any]) -> str | None:
    if mensagem.get("sourceChannel"):
        return str(mensagem["sourceChannel"])
    proveniencia = mensagem.get("provenance") or {}
    if isinstance(proveniencia, dict) and proveniencia.get("sourceChannel"):
        return str(proveniencia["sourceChannel"])
    interno = mensagem.get("__openclaw") or {}
    if isinstance(interno, dict):
        for chave in ("sourceChannel", "channel"):
            if interno.get(chave):
                return str(interno[chave])
    return None


def extrair_remetente(texto: str) -> str | None:
    padroes = (
        re.compile(r"\[WhatsApp[^\]]*\]\s*([^\n:]{2,100}?)(?:\s+\([^\n)]*\))?:", re.I),
        re.compile(r"(?:^|\n)(?:Remetente|Contato|Sender):\s*([^\n]{2,100})", re.I),
    )
    for padrao in padroes:
        achado = padrao.search(texto)
        if achado:
            nome = re.sub(r"\s+", " ", achado.group(1)).strip()
            return nome or None
    return None


def sessao_id_do_arquivo(path: Path) -> str:
    achado = UUID_INICIO.match(path.name)
    return achado.group(1) if achado else path.stem


def carregar_indice_sessoes(path: Path | None) -> dict[str, str]:
    if not path:
        return {}
    dados = carregar_json(path)
    sessoes = dados.get("sessions", []) if isinstance(dados, dict) else dados
    indice: dict[str, str] = {}
    for item in sessoes or []:
        if not isinstance(item, dict):
            continue
        sessao_id = str(item.get("sessionId") or "").strip()
        chave = str(item.get("key") or "").strip()
        if sessao_id and chave:
            indice[sessao_id] = chave
    return indice


def carregar_indice_trajetorias(diretorio: Path) -> dict[str, str]:
    """Recupera chaves de sessões arquivadas sem ler prompts ou saídas do agente."""
    indice: dict[str, str] = {}
    for path in sorted(diretorio.glob("*.trajectory.jsonl")):
        with path.open(encoding="utf-8", errors="ignore") as arquivo:
            for linha in arquivo:
                try:
                    registro = json.loads(linha)
                except json.JSONDecodeError:
                    continue
                sessao_id = str(registro.get("sessionId") or "").strip()
                chave = str(registro.get("sessionKey") or "").strip()
                if sessao_id and chave:
                    indice.setdefault(sessao_id, chave)
                    break
    return indice


def mascarar_conversa(chave: str) -> str:
    if not chave:
        return "WhatsApp — conversa não identificada"
    grupo = re.search(r"group:([^:]+)$", chave)
    if grupo:
        digest = hashlib.sha256(grupo.group(1).encode()).hexdigest()[:10]
        return f"WhatsApp — grupo {digest}"
    direto = re.search(r"direct:([^:]+)$", chave)
    if direto:
        sufixo = re.sub(r"\D", "", direto.group(1))[-4:]
        return f"WhatsApp — contato final {sufixo or 'não identificado'}"
    return "WhatsApp — conversa interna"


def iterar_arquivos(diretorio: Path) -> Iterable[Path]:
    for path in sorted(diretorio.iterdir()):
        if not path.is_file() or any(trecho in path.name for trecho in ARQUIVOS_IGNORADOS):
            continue
        if ".jsonl" not in path.name:
            continue
        yield path


def ler_mensagens(diretorio: Path, indice_sessoes: dict[str, str]) -> list[Mensagem]:
    indice_sessoes = {**carregar_indice_trajetorias(diretorio), **indice_sessoes}
    mensagens: list[Mensagem] = []
    vistos: set[str] = set()
    for path in iterar_arquivos(diretorio):
        sessao_id = sessao_id_do_arquivo(path)
        chave_sessao = indice_sessoes.get(sessao_id, "")
        sessao_whatsapp = ":whatsapp:" in chave_sessao
        with path.open(encoding="utf-8", errors="ignore") as arquivo:
            for numero_linha, linha in enumerate(arquivo, start=1):
                try:
                    registro = json.loads(linha)
                except json.JSONDecodeError:
                    continue
                mensagem = registro.get("message")
                if not isinstance(mensagem, dict) or mensagem.get("role") != "user":
                    continue
                texto = texto_conteudo(mensagem.get("content")).strip()
                if not texto:
                    continue
                canal = (canal_mensagem(mensagem) or "").lower()
                if canal != "whatsapp" and not sessao_whatsapp and "[whatsapp" not in texto.lower():
                    continue
                identificador = str(registro.get("id") or "").strip()
                chave_deduplicacao = (
                    f"{sessao_id}:{identificador}"
                    if identificador
                    else hashlib.sha256(
                        f"{sessao_id}\n{registro.get('timestamp')}\n{texto}".encode()
                    ).hexdigest()
                )
                if chave_deduplicacao in vistos:
                    continue
                vistos.add(chave_deduplicacao)
                timestamp = str(registro.get("timestamp") or mensagem.get("timestamp") or "")
                mensagens.append(
                    Mensagem(
                        id=identificador or chave_deduplicacao[:16],
                        sessao_id=sessao_id,
                        conversa=mascarar_conversa(chave_sessao),
                        timestamp=timestamp,
                        texto=texto,
                        remetente=extrair_remetente(texto),
                        arquivo=path.name,
                        linha=numero_linha,
                    )
                )
    return sorted(mensagens, key=lambda item: (item.sessao_id, item.timestamp, item.linha))


def campo(item: dict[str, Any], *nomes: str) -> Any:
    for nome in nomes:
        if nome in item:
            return item[nome]
    return None


def executar_busca_wacli(
    binario: Path,
    store: Path,
    consulta: str,
    limite: int = 200,
) -> list[dict[str, Any]]:
    """Pesquisa somente o banco local do wacli, bloqueando qualquer escrita."""
    ambiente = os.environ.copy()
    ambiente["WACLI_READONLY"] = "1"
    processo = subprocess.run(
        [
            str(binario),
            "--store",
            str(store),
            "--read-only",
            "--json",
            "messages",
            "search",
            consulta,
            "--limit",
            str(limite),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=ambiente,
    )
    resposta = json.loads(processo.stdout)
    if resposta.get("success") is False:
        raise RuntimeError(str(resposta.get("error") or "Falha na pesquisa local do wacli"))
    dados = resposta.get("data") or {}
    mensagens = dados.get("messages") or []
    if not isinstance(mensagens, list):
        raise ValueError("Resposta inesperada do wacli: 'messages' não é uma lista.")
    return [item for item in mensagens if isinstance(item, dict)]


def ler_mensagens_wacli(
    binario: Path,
    store: Path,
    valores: Iterable[int],
) -> list[Mensagem]:
    mensagens: list[Mensagem] = []
    vistos: set[tuple[str, str]] = set()
    consultas = sorted(
        {
            variante
            for valor in valores
            for variante in variantes_valor(valor)
            if len(re.sub(r"\D", "", variante)) >= 4
        }
    )
    for consulta in consultas:
        for item in executar_busca_wacli(binario, store, consulta):
            conversa_jid = str(campo(item, "ChatJID", "chat_jid") or "")
            mensagem_id = str(campo(item, "MsgID", "msg_id") or "")
            chave = (conversa_jid, mensagem_id)
            if mensagem_id and chave in vistos:
                continue
            vistos.add(chave)
            texto = str(
                campo(item, "Text", "text", "DisplayText", "display_text", "MediaCaption", "media_caption")
                or ""
            ).strip()
            if not texto:
                continue
            timestamp = str(campo(item, "Timestamp", "timestamp") or "")
            remetente = str(campo(item, "SenderName", "sender_name") or "").strip() or None
            from_me = bool(campo(item, "FromMe", "from_me"))
            if from_me and not remetente:
                remetente = "mensagem enviada pelo titular"
            mensagens.append(
                Mensagem(
                    id=mensagem_id or hashlib.sha256(
                        f"{conversa_jid}\n{timestamp}\n{texto}".encode()
                    ).hexdigest()[:16],
                    sessao_id="wacli",
                    conversa=mascarar_conversa(f"direct:{conversa_jid}"),
                    timestamp=timestamp,
                    texto=texto,
                    remetente=remetente,
                    arquivo="wacli.db",
                    linha=0,
                )
            )
    return sorted(mensagens, key=lambda item: (item.timestamp, item.id))


def tokens_nome(nome: str) -> set[str]:
    ignorados = {"da", "de", "do", "dos", "das", "e", "pablo", "ferreira", "araujo"}
    return {token.lower() for token in PALAVRAS_TOKEN.findall(nome or "") if len(token) >= 3 and token.lower() not in ignorados}


def data_iso(valor: Any) -> datetime | None:
    if not valor:
        return None
    bruto = str(valor).strip()
    for formato in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(bruto[:10], formato)
        except ValueError:
            pass
    return None


def timestamp_data(valor: str) -> datetime | None:
    if not valor:
        return None
    try:
        return datetime.fromisoformat(valor.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def excerto(texto: str, inicio: int, fim: int, limite: int = 420) -> str:
    margem = max(80, (limite - (fim - inicio)) // 2)
    comeco = max(0, inicio - margem)
    final = min(len(texto), fim + margem)
    resultado = re.sub(r"\s+", " ", texto[comeco:final]).strip()
    if comeco:
        resultado = "…" + resultado
    if final < len(texto):
        resultado += "…"
    return resultado


def analisar_duvida(duvida: dict[str, Any], mensagens: list[Mensagem]) -> dict[str, Any]:
    valores_brutos = duvida.get("valores") or []
    valores = sorted({item for item in (centavos(v) for v in valores_brutos) if item})
    codigo = str(duvida.get("codigo") or "sem-codigo")
    negocio = str(duvida.get("negocio") or "")
    if not valores:
        return {
            "codigo": codigo,
            "negocio": negocio,
            "status": "sem_valor_para_busca",
            "valores": [],
            "candidatos": [],
        }

    data_referencia = data_iso(duvida.get("data"))
    nome_tokens = tokens_nome(negocio)
    candidatos: list[dict[str, Any]] = []
    for mensagem in mensagens:
        for valor in valores:
            achado = regex_valor(valor).search(mensagem.texto)
            if not achado:
                continue
            texto_minusculo = mensagem.texto.lower()
            tokens_encontrados = sorted(token for token in nome_tokens if token in texto_minusculo)
            pontuacao = 100
            if PALAVRAS_PAGAMENTO.search(mensagem.texto):
                pontuacao += 20
            pontuacao += min(len(tokens_encontrados), 2) * 15
            data_mensagem = timestamp_data(mensagem.timestamp)
            distancia_dias = None
            if data_referencia and data_mensagem:
                distancia_dias = abs((data_mensagem.date() - data_referencia.date()).days)
                if distancia_dias <= 3:
                    pontuacao += 15
                elif distancia_dias <= 30:
                    pontuacao += 5
            candidatos.append(
                {
                    "valor": formatar_brl(valor),
                    "conversa": mensagem.conversa,
                    "remetente": mensagem.remetente,
                    "timestamp": mensagem.timestamp,
                    "mensagem_id": mensagem.id,
                    "arquivo": mensagem.arquivo,
                    "linha": mensagem.linha,
                    "pontuacao": pontuacao,
                    "tokens_negocio": tokens_encontrados,
                    "distancia_dias": distancia_dias,
                    "excerto": excerto(mensagem.texto, achado.start(), achado.end()),
                }
            )

    candidatos.sort(key=lambda item: (-item["pontuacao"], item["timestamp"], item["mensagem_id"]))
    conversas = {item["conversa"] for item in candidatos}
    if not candidatos:
        status = "nao_encontrado"
    elif len(conversas) == 1:
        status = "encontrado_unico"
    else:
        melhor = candidatos[0]["pontuacao"]
        segundo = candidatos[1]["pontuacao"] if len(candidatos) > 1 else 0
        status = "encontrado_unico" if melhor - segundo >= 25 else "ambiguo"
    return {
        "codigo": codigo,
        "negocio": negocio,
        "status": status,
        "valores": [formatar_brl(item) for item in valores],
        "candidatos": candidatos[:20],
    }


def gerar_plano(duvidas: list[dict[str, Any]], mensagens: list[Mensagem]) -> dict[str, Any]:
    resultados = [analisar_duvida(item, mensagens) for item in duvidas]
    contagens: dict[str, int] = {}
    for item in resultados:
        contagens[item["status"]] = contagens.get(item["status"], 0) + 1
    return {
        "modo": "somente_leitura",
        "regra_primaria": "buscar primeiro pelo valor do PIX",
        "mensagens_whatsapp_indexadas": len(mensagens),
        "duvidas_analisadas": len(resultados),
        "contagens": contagens,
        "resultados": resultados,
        "controles": {
            "mensagens_enviadas": 0,
            "escritas_supabase": 0,
            "registros_operacionais_alterados": 0,
            "promocoes_executadas": 0,
        },
    }


def relatorio_markdown(plano: dict[str, Any]) -> str:
    linhas = [
        "# Conciliação privada WhatsApp × PIX",
        "",
        "Modo: **somente leitura**. Nenhuma mensagem foi enviada e nenhuma tabela operacional foi alterada.",
        "",
        f"- Mensagens indexadas: {plano['mensagens_whatsapp_indexadas']}",
        f"- Dúvidas analisadas: {plano['duvidas_analisadas']}",
        "",
        "| Código | Negócio | Situação | Valores | Candidatos |",
        "|---|---|---|---|---:|",
    ]
    for item in plano["resultados"]:
        valores = ", ".join(item["valores"]) or "—"
        linhas.append(
            f"| {item['codigo']} | {item['negocio']} | {item['status']} | {valores} | {len(item['candidatos'])} |"
        )
    linhas.extend(["", "## Evidências", ""])
    for item in plano["resultados"]:
        linhas.append(f"### {item['codigo']} — {item['negocio']}")
        if not item["candidatos"]:
            linhas.append(f"- {item['status']}")
            linhas.append("")
            continue
        for candidato in item["candidatos"][:5]:
            remetente = candidato.get("remetente") or candidato["conversa"]
            linhas.append(
                f"- {candidato['valor']} · {candidato['timestamp'] or 'sem horário'} · "
                f"{remetente} · confiança {candidato['pontuacao']} · mensagem `{candidato['mensagem_id']}`"
            )
            linhas.append(f"  - {candidato['excerto']}")
        linhas.append("")
    return "\n".join(linhas).rstrip() + "\n"


def validar_duvidas(dados: Any) -> list[dict[str, Any]]:
    duvidas = dados.get("duvidas") if isinstance(dados, dict) else dados
    if not isinstance(duvidas, list):
        raise ValueError("O arquivo de dúvidas deve conter uma lista ou a chave 'duvidas'.")
    return [item for item in duvidas if isinstance(item, dict)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duvidas", required=True, type=Path)
    parser.add_argument("--sessions-dir", required=True, type=Path)
    parser.add_argument("--sessions-index", type=Path)
    parser.add_argument("--wacli-bin", type=Path)
    parser.add_argument("--wacli-store", type=Path)
    parser.add_argument("--saida-json", required=True, type=Path)
    parser.add_argument("--saida-md", required=True, type=Path)
    args = parser.parse_args()

    duvidas = validar_duvidas(carregar_json(args.duvidas))
    indice = carregar_indice_sessoes(args.sessions_index)
    mensagens = ler_mensagens(args.sessions_dir, indice)
    if bool(args.wacli_bin) != bool(args.wacli_store):
        parser.error("--wacli-bin e --wacli-store devem ser usados juntos")
    if args.wacli_bin and args.wacli_store:
        valores = {
            valor
            for duvida in duvidas
            for valor in (centavos(item) for item in (duvida.get("valores") or []))
            if valor
        }
        mensagens.extend(ler_mensagens_wacli(args.wacli_bin, args.wacli_store, valores))
        mensagens = list({(item.sessao_id, item.id): item for item in mensagens}.values())
    plano = gerar_plano(duvidas, mensagens)
    args.saida_json.write_text(json.dumps(plano, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.saida_md.write_text(relatorio_markdown(plano), encoding="utf-8")
    print(json.dumps({
        "modo": plano["modo"],
        "mensagens_indexadas": plano["mensagens_whatsapp_indexadas"],
        "duvidas": plano["duvidas_analisadas"],
        "contagens": plano["contagens"],
        "controles": plano["controles"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
