#!/usr/bin/env python3
"""Consolida exportações HTML do Telegram sem executar escrita operacional."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


PADROES = {
    "compra": re.compile(r"\bcompr", re.I),
    "venda_abate": re.compile(r"\bvend|\babate|romaneio", re.I),
    "pesagem": re.compile(r"pesag|peso|balan[çc]a", re.I),
    "gta": re.compile(r"\bgta\b|guia de tr[aâ]nsito", re.I),
    "nf": re.compile(r"\bnf(?:-?e)?\b|nota fiscal", re.I),
    "pagamento": re.compile(r"pagamento|paguei|pago|pix|transfer[eê]ncia|boleto|parcela", re.I),
    "pendencia": re.compile(r"pend[eê]ncia|pendente|falta(?:m|ndo)?|n[aã]o confirmad|n[aã]o informad", re.I),
    "correcao": re.compile(r"corrig|corre[çc][aã]o|retific|na verdade|errad|erro", re.I),
    "rascunho": re.compile(r"rascunho", re.I),
}

CAMPOS_COMPRA = {
    "quantidade": "cabeças",
    "peso_total_kg": "peso total",
    "preco_arroba": "preço por arroba",
    "valor_total": "valor total",
    "data_negociacao": "data da negociação",
    "data_pesagem": "data da pesagem",
    "pagamento": "pagamento",
}


def normalizar_texto(valor: Any) -> str:
    texto = unicodedata.normalize("NFKD", str(valor or ""))
    texto = texto.encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", texto).strip()


def sha256_arquivo(caminho: Path) -> str:
    digest = hashlib.sha256()
    with caminho.open("rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(1024 * 1024), b""):
            digest.update(bloco)
    return digest.hexdigest()


def numero_decimal(texto: str | None) -> Decimal | None:
    if not texto:
        return None
    valor = texto.strip().replace("R$", "").replace(" ", "")
    if "." in valor and "," in valor:
        valor = valor.replace(".", "").replace(",", ".")
    elif "," in valor:
        valor = valor.replace(",", ".")
    try:
        return Decimal(valor)
    except InvalidOperation:
        return None


def serializar(objeto: Any) -> Any:
    if isinstance(objeto, Decimal):
        return str(objeto)
    raise TypeError(type(objeto).__name__)


def primeiro(padrao: str, texto: str) -> str | None:
    achado = re.search(padrao, texto, re.I)
    return achado.group(1).strip() if achado else None


def extrair_gtas(texto: str) -> list[str]:
    encontrados: set[str] = set()
    for padrao in (
        r"\bGTA\s*(?:[A-Z]{1,3}\s*)?[-–]?\s*(\d{5,9})\b",
        r"\bU\s*[-–]\s*(\d{5,9})\b",
    ):
        encontrados.update(re.findall(padrao, texto, re.I))
    return sorted(encontrados)


class ExportacaoTelegramParser(HTMLParser):
    VAZIAS = {"br", "img", "meta", "link", "input", "hr", "source", "wbr"}

    def __init__(self) -> None:
        super().__init__()
        self.pilha: list[tuple[str, set[str]]] = []
        self.mensagens: list[dict[str, Any]] = []
        self.atual: dict[str, Any] | None = None
        self.captura: str | None = None
        self.buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        atributos = {chave: valor or "" for chave, valor in attrs}
        classes = set(atributos.get("class", "").split())
        if tag == "br":
            if self.captura:
                self.buffer.append("\n")
            return
        if tag in self.VAZIAS:
            return
        self.pilha.append((tag, classes))
        if tag == "div" and "message" in classes and "default" in classes:
            self.atual = {
                "mensagem_id": atributos.get("id"),
                "data": "",
                "autor": "",
                "texto": "",
                "anexos": [],
            }
            self.mensagens.append(self.atual)
        if not self.atual:
            return
        if tag == "div" and "date" in classes and atributos.get("title"):
            self.atual["data"] = atributos["title"]
        if tag == "div" and "from_name" in classes:
            self.captura, self.buffer = "autor", []
        elif tag == "div" and classes == {"text"}:
            self.captura, self.buffer = "texto", []
        if tag == "a" and atributos.get("href", "").startswith(("files/", "photos/")):
            href = atributos["href"]
            if not href.endswith("_thumb.jpg") and href not in self.atual["anexos"]:
                self.atual["anexos"].append(href)

    def handle_endtag(self, tag: str) -> None:
        if self.captura and tag == "div":
            assert self.atual is not None
            self.atual[self.captura] = "".join(self.buffer).strip()
            self.captura, self.buffer = None, []
        if tag == "div" and self.pilha:
            self.pilha.pop()

    def handle_data(self, data: str) -> None:
        if self.captura:
            self.buffer.append(data)


def limpar_autor(autor: str) -> str:
    return re.sub(r"\s+\d{2}\.\d{2}\.\d{4}.*$", "", autor).strip()


def titulo_exportacao(conteudo: str, fallback: str) -> str:
    achado = re.search(r'<div class="text bold">\s*([^<]+)', conteudo)
    return achado.group(1).strip() if achado else fallback


def ler_exportacao(caminho: Path, contexto: str | None = None) -> dict[str, Any]:
    conteudo = caminho.read_text(encoding="utf-8", errors="replace")
    parser = ExportacaoTelegramParser()
    parser.feed(conteudo)
    nome = contexto or titulo_exportacao(conteudo, caminho.stem)
    ultimo_autor = ""
    base = caminho.parent
    anexos = []
    for ordem, mensagem in enumerate(parser.mensagens):
        if mensagem["autor"]:
            ultimo_autor = limpar_autor(mensagem["autor"])
        mensagem["autor"] = ultimo_autor
        mensagem["contexto"] = nome
        mensagem["ordem"] = ordem
        mensagem["categorias"] = [
            chave for chave, padrao in PADROES.items() if padrao.search(mensagem["texto"])
        ]
        mensagem["gtas"] = extrair_gtas(mensagem["texto"])
        mensagem["texto_sha256"] = (
            hashlib.sha256(normalizar_texto(mensagem["texto"]).encode()).hexdigest()
            if mensagem["texto"] else None
        )
        for href in mensagem["anexos"]:
            arquivo = base / href
            anexos.append({
                "contexto": nome,
                "mensagem_id": mensagem["mensagem_id"],
                "data": mensagem["data"],
                "arquivo": href,
                "existe": arquivo.is_file(),
                "tamanho": arquivo.stat().st_size if arquivo.is_file() else None,
                "sha256": sha256_arquivo(arquivo) if arquivo.is_file() else None,
            })
    datas = [mensagem["data"] for mensagem in parser.mensagens if mensagem["data"]]
    return {
        "contexto": nome,
        "arquivo": str(caminho),
        "arquivo_sha256": sha256_arquivo(caminho),
        "mensagens": parser.mensagens,
        "anexos": anexos,
        "anexos_omitidos": conteudo.count("Not included"),
        "primeira_data": datas[0] if datas else None,
        "ultima_data": datas[-1] if datas else None,
    }


def rotulo_valido(rotulo: str) -> bool:
    valor = normalizar_texto(rotulo)
    if not valor or re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", valor):
        return False
    invalidos = ("[vendedor]", "compra sem identificacao", "confirmar?", "voce lembra qual")
    return not any(item in valor for item in invalidos)


def extrair_compra(mensagem: dict[str, Any], aliases: dict[str, str]) -> dict[str, Any] | None:
    texto = mensagem["texto"]
    if not re.search(r"(?:🐄\s*)?Compra\s*[–—-]|COMPRA LIDA", texto, re.I):
        return None
    rotulo = primeiro(r"(?:🐄\s*)?Compra\s*[–—-]\s*([^\n]+)", texto)
    vendedor = primeiro(r"Vendedor:\s*([^\n]+)", texto)
    rotulo = rotulo or vendedor or "Compra sem identificação"
    if not rotulo_valido(rotulo):
        return None
    quantidade = numero_decimal(primeiro(r"Quantidade(?:\s+informada)?:\s*(\d+(?:[.,]\d+)?)", texto))
    peso_total = numero_decimal(primeiro(r"Peso (?:bruto|de balança) total:\s*([\d.,]+)", texto))
    if peso_total is None:
        peso_total = numero_decimal(primeiro(r"Peso total:\s*([\d.,]+)", texto))
    preco = numero_decimal(primeiro(r"Pre[çc]o:\s*R\$\s*([\d.,]+)\s*/?@", texto))
    if preco is None:
        preco = numero_decimal(primeiro(r"a\s*R\$\s*([\d.,]+)\s*/?@", texto))
    valor = numero_decimal(primeiro(r"Valor(?: total)?:\s*R\$\s*([\d.,]+)", texto))
    negociacao = primeiro(r"Negocia[çc][aã]o:\s*(\d{1,2}/\d{1,2}/\d{4})", texto)
    pesagem = primeiro(r"Pesagem:\s*(\d{1,2}/\d{1,2}/\d{4})", texto)
    pagamento = primeiro(r"Pagamento:\s*([^\n]+)", texto)
    canonico = aliases.get(normalizar_texto(rotulo), normalizar_texto(rotulo))
    eh_teste = bool(re.search(r"fict[ií]ci|homologa|\bteste\b|CF-\d+-999", texto, re.I))
    campos = {
        "quantidade": quantidade,
        "peso_total_kg": peso_total,
        "preco_arroba": preco,
        "valor_total": valor,
        "data_negociacao": negociacao,
        "data_pesagem": pesagem,
        "pagamento": pagamento,
    }
    return {
        "contexto": mensagem["contexto"],
        "rotulo": rotulo,
        "rotulo_canonico": canonico,
        "vendedor": vendedor,
        **campos,
        "campos_preenchidos": sum(valor not in (None, "") for valor in campos.values()),
        "eh_correcao_explicita": bool(PADROES["correcao"].search(texto)),
        "eh_teste": eh_teste,
        "confirmado": False,
        "mensagem_id": mensagem["mensagem_id"],
        "data_mensagem": mensagem["data"],
        "ordem": mensagem.get("ordem_global", mensagem["ordem"]),
        "texto_sha256": mensagem["texto_sha256"],
        "gtas": mensagem["gtas"],
    }


def dia_mensagem(valor: str | None) -> str | None:
    achado = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", valor or "")
    return f"{achado.group(3)}-{achado.group(2)}-{achado.group(1)}" if achado else None


def assinatura_campos(compra: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(compra.get(campo) for campo in CAMPOS_COMPRA)


def campos_divergentes(versoes: list[dict[str, Any]]) -> list[str]:
    return [
        campo for campo in CAMPOS_COMPRA
        if len({versao.get(campo) for versao in versoes}) > 1
    ]


def campos_ausentes_em_todas(versoes: list[dict[str, Any]]) -> list[str]:
    return [
        campo for campo in CAMPOS_COMPRA
        if all(versao.get(campo) in (None, "") for versao in versoes)
    ]


def classificar_revisao(classificacao: str, divergentes: list[str]) -> tuple[str, str, str]:
    if classificacao == "repeticao_deduplicavel":
        return "repetição sem conflito", "baixa", "manter uma evidência e preservar as referências"
    if classificacao == "correcao_explicita_mais_recente":
        return "conferir correção explícita", "alta", "comparar a correção com a fonte e confirmar"
    financeiros = {"quantidade", "preco_arroba", "valor_total", "pagamento"}
    prioridade = "alta" if financeiros.intersection(divergentes) else "média"
    return "escolher a versão correta", prioridade, "conferir as mensagens e escolher sem combinar valores"


def agrupar_compras(compras: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grupos: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for compra in compras:
        data_base = compra["data_negociacao"] or dia_mensagem(compra["data_mensagem"]) or "sem_data"
        chave = f"{normalizar_texto(compra['contexto'])}|{compra['rotulo_canonico']}|{data_base}"
        grupos[chave].append(compra)
    saida = []
    for chave, versoes in sorted(grupos.items()):
        versoes.sort(key=lambda item: item["ordem"])
        assinaturas = {assinatura_campos(item) for item in versoes}
        ultima = versoes[-1]
        if len(assinaturas) == 1:
            classificacao = "repeticao_deduplicavel"
            preferida = ultima
        elif ultima["eh_correcao_explicita"]:
            classificacao = "correcao_explicita_mais_recente"
            preferida = ultima
        else:
            classificacao = "ambiguo_multiplas_versoes"
            preferida = None
        divergentes = campos_divergentes(versoes)
        ausentes = campos_ausentes_em_todas(versoes)
        situacao, prioridade, acao = classificar_revisao(classificacao, divergentes)
        saida.append({
            "chave_provisoria": chave,
            "contexto": versoes[0]["contexto"],
            "negocio": versoes[0]["rotulo"],
            "data_base": versoes[0]["data_negociacao"] or dia_mensagem(versoes[0]["data_mensagem"]),
            "classificacao": classificacao,
            "situacao_revisao": situacao,
            "prioridade_revisao": prioridade,
            "acao_recomendada": acao,
            "versoes": len(versoes),
            "campos_distintos": len(assinaturas),
            "campos_divergentes": divergentes,
            "campos_divergentes_humanos": [CAMPOS_COMPRA[campo] for campo in divergentes],
            "campos_ausentes_em_todas": ausentes,
            "campos_ausentes_humanos": [CAMPOS_COMPRA[campo] for campo in ausentes],
            "confirmado": False,
            "requer_revisao": classificacao != "repeticao_deduplicavel",
            "versao_preferida": preferida,
            "versoes_revisao": [{
                "mensagem_id": item["mensagem_id"],
                "data_mensagem": item["data_mensagem"],
                "eh_correcao_explicita": item["eh_correcao_explicita"],
                "dados": {campo: item.get(campo) for campo in CAMPOS_COMPRA},
            } for item in versoes],
            "mensagens": [item["mensagem_id"] for item in versoes],
        })
    return saida


def carregar_aliases(caminho: Path | None) -> dict[str, str]:
    if not caminho:
        return {}
    bruto = json.loads(caminho.read_text(encoding="utf-8"))
    pares = bruto.get("aliases", bruto)
    return {normalizar_texto(chave): normalizar_texto(valor) for chave, valor in pares.items()}


def cruzar_gtas(mensagens: list[dict[str, Any]], documentos: dict[str, Any] | None) -> dict[str, Any]:
    referencias: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for mensagem in mensagens:
        for gta in mensagem["gtas"]:
            referencias[gta].append({
                "contexto": mensagem["contexto"],
                "mensagem_id": mensagem["mensagem_id"],
                "data": mensagem["data"],
            })
    linhas = (documentos or {}).get("vinculos_nf_gta") or []
    por_gta = {
        re.sub(r"\D", "", str(item.get("gta") or "")): item
        for item in linhas if re.sub(r"\D", "", str(item.get("gta") or ""))
    }
    comuns = sorted(set(referencias) & set(por_gta))
    return {
        "gtas_distintas_telegram": len(referencias),
        "gtas_documentos": len(por_gta),
        "vinculos_exatos": len(comuns),
        "somente_telegram": len(set(referencias) - set(por_gta)),
        "somente_documentos": len(set(por_gta) - set(referencias)),
        "criterio": "numero_gta_exato",
        "classificacao": "candidato_forte_documental_nao_confirmado",
        "confirmado": False,
        "vinculos": [{
            "gta": gta,
            "nf": por_gta[gta].get("nf"),
            "linha_documento": por_gta[gta].get("linha_agronotas"),
            "referencias_telegram": referencias[gta],
        } for gta in comuns],
    }


def cortes_fontes(documentos: dict[str, Any] | None, complemento_ima: dict[str, Any] | None) -> dict[str, Any]:
    fontes = (documentos or {}).get("fontes") or {}
    ima = fontes.get("ima") or {}
    corte = {
        "agronotas": (fontes.get("agronotas") or {}).get("data_final"),
        "banco": (fontes.get("banco") or {}).get("data_final"),
        "ima_detalhado": ima.get("periodo_final"),
    }
    if complemento_ima:
        saldo_anterior = ima.get("saldo_rebanho")
        saldo_atual = complemento_ima.get("saldo_rebanho")
        corte["ima_sintetico"] = complemento_ima.get("data")
        corte["saldo_ima_anterior"] = saldo_anterior
        corte["saldo_ima_sintetico"] = saldo_atual
        corte["variacao_ima_sem_detalhamento"] = (
            saldo_atual - saldo_anterior
            if isinstance(saldo_atual, (int, float)) and isinstance(saldo_anterior, (int, float)) else None
        )
    return corte


def gerar_plano(exportacoes: list[dict[str, Any]], aliases: dict[str, str],
                documentos: dict[str, Any] | None = None,
                complemento_ima: dict[str, Any] | None = None) -> dict[str, Any]:
    mensagens = [mensagem for exportacao in exportacoes for mensagem in exportacao["mensagens"]]
    for ordem_global, mensagem in enumerate(mensagens):
        mensagem["ordem_global"] = ordem_global
    anexos = [anexo for exportacao in exportacoes for anexo in exportacao["anexos"]]
    compras_brutas = [compra for mensagem in mensagens if (compra := extrair_compra(mensagem, aliases))]
    vistos: set[tuple[str, str]] = set()
    compras = []
    for compra in compras_brutas:
        chave_deduplicacao = (compra["contexto"], compra["texto_sha256"])
        if chave_deduplicacao in vistos:
            continue
        vistos.add(chave_deduplicacao)
        if not compra["eh_teste"]:
            compras.append(compra)
    hashes_anexos = Counter(item["sha256"] for item in anexos if item["sha256"])
    categorias = Counter(categoria for mensagem in mensagens for categoria in mensagem["categorias"])
    plano = {
        "gerado_em": datetime.now().astimezone().isoformat(),
        "modo": "dry_run_somente_leitura",
        "plano_gera_escrita": False,
        "escritas_executadas": 0,
        "tabelas_operacionais_alteradas": 0,
        "fontes": [{
            "contexto": item["contexto"],
            "arquivo": item["arquivo"],
            "arquivo_sha256": item["arquivo_sha256"],
            "mensagens": len(item["mensagens"]),
            "primeira_data": item["primeira_data"],
            "ultima_data": item["ultima_data"],
            "anexos_referenciados": len(item["anexos"]),
            "anexos_omitidos": item["anexos_omitidos"],
        } for item in exportacoes],
        "cortes": cortes_fontes(documentos, complemento_ima),
        "resumo": {
            "mensagens": len(mensagens),
            "mensagens_texto_unicas": len({item["texto_sha256"] for item in mensagens if item["texto_sha256"]}),
            "categorias": dict(sorted(categorias.items())),
            "anexos_referenciados": len(anexos),
            "anexos_existentes": sum(item["existe"] for item in anexos),
            "anexos_omitidos": sum(item["anexos_omitidos"] for item in exportacoes),
            "conteudos_anexos_unicos": len(hashes_anexos),
            "duplicatas_anexos": sum(quantidade - 1 for quantidade in hashes_anexos.values()),
            "blocos_compra_brutos": len(compras_brutas),
            "blocos_compra_reais_deduplicados": len(compras),
        },
        "grupos_compras": agrupar_compras(compras),
        "cruzamento_gta": cruzar_gtas(mensagens, documentos),
        "anexos": anexos,
        "pendencias": [],
    }
    ambiguos = sum(grupo["classificacao"] == "ambiguo_multiplas_versoes" for grupo in plano["grupos_compras"])
    correcoes = sum(grupo["classificacao"] == "correcao_explicita_mais_recente" for grupo in plano["grupos_compras"])
    plano["resumo"]["grupos_compras"] = len(plano["grupos_compras"])
    plano["resumo"]["grupos_ambiguos"] = ambiguos
    plano["resumo"]["correcoes_explicitas_preferidas"] = correcoes
    fila_revisao = [grupo for grupo in plano["grupos_compras"] if grupo["requer_revisao"]]
    plano["resumo_revisao"] = {
        "negocios_para_revisar": len(fila_revisao),
        "prioridade_alta": sum(grupo["prioridade_revisao"] == "alta" for grupo in fila_revisao),
        "prioridade_media": sum(grupo["prioridade_revisao"] == "média" for grupo in fila_revisao),
        "correcoes_explicitas": sum(
            grupo["classificacao"] == "correcao_explicita_mais_recente" for grupo in fila_revisao
        ),
        "ambiguidades_sem_preferencia": sum(
            grupo["classificacao"] == "ambiguo_multiplas_versoes" for grupo in fila_revisao
        ),
    }
    if ambiguos:
        plano["pendencias"].append("compras_com_multiplas_versoes_exigem_revisao")
    if plano["resumo"]["anexos_omitidos"]:
        plano["pendencias"].append("exportacoes_com_anexos_omitidos")
    variacao = plano["cortes"].get("variacao_ima_sem_detalhamento")
    if variacao not in (None, 0):
        plano["pendencias"].append("saldo_ima_variou_sem_ficha_detalhada_correspondente")
    if plano["cruzamento_gta"]["vinculos_exatos"]:
        plano["pendencias"].append("vinculos_gta_documentais_exigem_conferencia")
    assinavel = {chave: valor for chave, valor in plano.items() if chave != "gerado_em"}
    plano["plano_id"] = hashlib.sha256(
        json.dumps(assinavel, ensure_ascii=False, sort_keys=True, default=serializar).encode()
    ).hexdigest()[:12]
    return plano


def relatorio_markdown(plano: dict[str, Any]) -> str:
    resumo, cortes, gta = plano["resumo"], plano["cortes"], plano["cruzamento_gta"]
    fontes = "\n".join(
        f"- {item['contexto']}: {item['mensagens']} mensagens, "
        f"{item['anexos_referenciados']} anexos, de {item['primeira_data']} a {item['ultima_data']}."
        for item in plano["fontes"]
    )
    pendencias = "\n".join(f"- {item}" for item in plano["pendencias"]) or "- nenhuma"
    fila = [grupo for grupo in plano["grupos_compras"] if grupo["requer_revisao"]]
    linhas_revisao = []
    for grupo in sorted(
        fila,
        key=lambda item: ({"alta": 0, "média": 1, "baixa": 2}[item["prioridade_revisao"]], item["chave_provisoria"]),
    ):
        divergentes = ", ".join(grupo["campos_divergentes_humanos"]) or "nenhum"
        ausentes = ", ".join(grupo["campos_ausentes_humanos"]) or "nenhum"
        linhas_revisao.append(
            f"| {grupo['prioridade_revisao']} | {grupo['situacao_revisao']} | "
            f"{grupo['contexto']} | {grupo['negocio']} | "
            f"{grupo['data_base'] or 'sem data'} | {grupo['versoes']} | {divergentes} | {ausentes} | "
            f"{grupo['acao_recomendada']} |"
        )
    tabela_revisao = (
        "\n".join(linhas_revisao)
        or "| — | — | — | — | — | 0 | — | — | nenhuma revisão |"
    )
    return f"""# Consolidação privada do histórico Telegram

Plano `{plano['plano_id']}`. Modo somente leitura; nenhuma escrita foi executada.

## Fontes

{fontes}

## Cortes assumidos

- Agronotas: {cortes.get('agronotas') or 'não informado'};
- banco: {cortes.get('banco') or 'não informado'};
- IMA detalhado: {cortes.get('ima_detalhado') or 'não informado'};
- IMA sintético: {cortes.get('ima_sintetico') or 'não informado'}.

## Resultado

- {resumo['mensagens']} mensagens processadas;
- {resumo['anexos_existentes']} anexos encontrados e {resumo['anexos_omitidos']} omitidos;
- {resumo['blocos_compra_reais_deduplicados']} blocos reais de compra deduplicados;
- {resumo['grupos_compras']} grupos provisórios;
- {resumo['grupos_ambiguos']} grupos permanecem ambíguos;
- {resumo['correcoes_explicitas_preferidas']} grupos têm correção posterior explicitamente indicada;
- {gta['vinculos_exatos']} vínculos GTA exatos com a fonte documental.

## Fila privada de conferência por negócio

| Prioridade | Situação | Contexto | Negócio | Data-base | Versões | O que diverge | Ausente em todas | Próxima ação |
|---|---|---|---|---|---:|---|---|---|
{tabela_revisao}

Esta fila não combina campos de versões diferentes. A versão indicada por uma
correção explícita continua pendente de confirmação na fonte.

## Regras validadas

- correção explícita posterior pode ser preferida, mas nunca confirmada automaticamente;
- mesmo fornecedor e mesma data com campos diferentes permanece ambíguo;
- GTA exata forma candidato documental forte, ainda não confirmado;
- valor/data isolados não são usados por este importador;
- testes e exemplos são excluídos dos negócios reais;
- nenhum resultado cria compra, venda, GTA, pagamento, rascunho ou evento.

## Pendências

{pendencias}
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--telegram-html", action="append", required=True, type=Path)
    parser.add_argument("--contexto", action="append")
    parser.add_argument("--aliases", type=Path)
    parser.add_argument("--documentos-plano", type=Path)
    parser.add_argument("--complemento-ima", type=Path)
    parser.add_argument("--saida-json", required=True, type=Path)
    parser.add_argument("--saida-md", required=True, type=Path)
    args = parser.parse_args()
    if args.contexto and len(args.contexto) != len(args.telegram_html):
        parser.error("--contexto deve ser repetido uma vez para cada --telegram-html")
    contextos = args.contexto or [None] * len(args.telegram_html)
    exportacoes = [ler_exportacao(caminho, contexto) for caminho, contexto in zip(args.telegram_html, contextos)]
    documentos = json.loads(args.documentos_plano.read_text()) if args.documentos_plano else None
    complemento = json.loads(args.complemento_ima.read_text()) if args.complemento_ima else None
    plano = gerar_plano(exportacoes, carregar_aliases(args.aliases), documentos, complemento)
    args.saida_json.parent.mkdir(parents=True, exist_ok=True)
    args.saida_md.parent.mkdir(parents=True, exist_ok=True)
    args.saida_json.write_text(
        json.dumps(plano, ensure_ascii=False, indent=2, default=serializar) + "\n",
        encoding="utf-8",
    )
    args.saida_md.write_text(relatorio_markdown(plano), encoding="utf-8")
    print(json.dumps({
        "plano_id": plano["plano_id"],
        "modo": plano["modo"],
        "escritas_executadas": plano["escritas_executadas"],
        "tabelas_operacionais_alteradas": plano["tabelas_operacionais_alteradas"],
        "resumo": plano["resumo"],
        "pendencias": plano["pendencias"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
