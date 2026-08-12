#!/usr/bin/env python3
"""Consolida exportações HTML do Telegram sem executar escrita operacional."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta
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

# O histórico só pode ser considerado consolidado quando contém os elementos
# necessários para conferir a compra e seu efeito financeiro. Uma única versão
# sem conflito não equivale a um negócio completo.
CAMPOS_MINIMOS_CONSOLIDACAO = (
    "quantidade",
    "peso_total_kg",
    "preco_arroba",
    "valor_total",
    "data_negociacao",
    "pagamento",
)

IDENTIDADE_COMPRA = {
    "sexo": "sexo",
    "categoria": "categoria",
    "destino": "destino",
}

NAO_INFORMADO = "não informado"


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
    elif "." in valor:
        partes = valor.split(".")
        if len(partes) > 2 or (len(partes) == 2 and len(partes[1]) == 3):
            valor = "".join(partes)
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


def normalizar_pagamento(valor: str | None, data_negociacao: str | None) -> str | None:
    if not valor:
        return None
    texto = re.sub(r"^(?:data|pagamento)\s*:\s*", "", valor.strip(), flags=re.I)
    simples = normalizar_texto(texto)
    if simples in {"a vista", "avista"}:
        return "à vista"
    meses = {
        "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6,
        "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
    }
    data_extenso = re.fullmatch(r"(\d{1,2})\s+de\s+([a-zç]+)\s+de\s+(\d{4})", texto, re.I)
    if data_extenso:
        mes = meses.get(normalizar_texto(data_extenso.group(2)))
        if mes:
            return f"{int(data_extenso.group(1)):02d}/{mes:02d}/{data_extenso.group(3)}"
    data_numerica = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", texto)
    if data_numerica:
        return f"{int(data_numerica.group(1)):02d}/{int(data_numerica.group(2)):02d}/{data_numerica.group(3)}"
    prazo = re.fullmatch(r"(\d+)\s+dias?", simples)
    if prazo and data_negociacao:
        try:
            base = datetime.strptime(data_negociacao, "%d/%m/%Y")
            return (base + timedelta(days=int(prazo.group(1)))).strftime("%d/%m/%Y")
        except ValueError:
            pass
    return texto


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


def separar_configuracao(configuracao: dict[str, Any]) -> tuple[dict[str, str], dict[str, Any]]:
    if "aliases" not in configuracao and "regras_mensagens" not in configuracao:
        return configuracao, {}
    return configuracao.get("aliases", {}), configuracao.get("regras_mensagens", {})


def inferir_sexo_categoria(texto: str) -> tuple[str, str]:
    valor = normalizar_texto(texto)
    regras = (
        (r"\bnovilh[ao]s?\b", "fêmea", "novilha"),
        (r"\bvacas?\b", "fêmea", "vaca"),
        (r"\bbezerras?\b", "fêmea", "bezerro"),
        (r"\bgarrotas?\b", "fêmea", "garrote"),
        (r"\bgarrotes?\b", "macho", "garrote"),
        (r"\bbezerros?\b", "macho", "bezerro"),
        (r"\bbois?\b", "macho", "boi"),
        (r"\btouros?\b", "macho", "touro"),
    )
    for padrao, sexo, categoria in regras:
        if re.search(padrao, valor):
            return sexo, categoria
    return NAO_INFORMADO, NAO_INFORMADO


def inferir_destino(texto: str) -> str:
    valor = normalizar_texto(texto)
    if re.search(r"\b(?:para|destino|vai para|destinado ao?)\s+(?:o\s+)?(?:abate|boi balanca|frigorifico)\b", valor):
        return "abate / boi balança"
    if re.search(r"\b(?:para|destino|vai para|destinado a)\s+(?:a\s+)?fazenda\b", valor):
        return "fazenda"
    if re.search(r"\b(?:para|destino|vai para|destinado ao?)\s+(?:o\s+)?confinamento\b", valor):
        return "confinamento"
    return NAO_INFORMADO


def aplicar_regra_privada(compra: dict[str, Any], regra: dict[str, Any]) -> dict[str, Any]:
    permitidos = {
        "rotulo_canonico", "negocio_origem", "sexo", "categoria", "destino",
        "tipo_evidencia", "observacao_classificacao",
    }
    for campo in permitidos:
        if regra.get(campo) not in (None, ""):
            compra[campo] = regra[campo]
    return compra


def extrair_compra(mensagem: dict[str, Any], configuracao: dict[str, Any]) -> dict[str, Any] | None:
    texto = mensagem["texto"]
    if not re.search(r"(?:🐄\s*)?Compra\s*[–—-]|COMPRA LIDA", texto, re.I):
        return None
    rotulo = primeiro(r"(?:🐄\s*)?Compra\s*[–—-]\s*([^\n]+)", texto)
    vendedor = primeiro(r"Vendedor:\s*([^\n]+)", texto)
    rotulo = rotulo or vendedor or "Compra sem identificação"
    if not rotulo_valido(rotulo):
        return None
    quantidade = numero_decimal(primeiro(r"Quantidade(?:\s+informada)?:\s*(\d+(?:[.,]\d+)?)", texto))
    if quantidade is None:
        quantidade = numero_decimal(primeiro(r"Fechamento:\s*(?:•\s*)?(\d+(?:[.,]\d+)?)\s+(?:novilh\w*|vacas?|garrot\w*|bezerr\w*|bois?)", texto))
    peso_total = numero_decimal(primeiro(r"Peso (?:bruto|de balança) total:\s*([\d.,]+)", texto))
    if peso_total is None:
        peso_total = numero_decimal(primeiro(r"Peso total:\s*([\d.,]+)", texto))
    if peso_total is None:
        peso_total = numero_decimal(primeiro(r"(?:•\s*)?([\d.,]+)\s*kg\s+bruto", texto))
    preco = numero_decimal(primeiro(r"Pre[çc]o:\s*R\$\s*([\d.,]+)\s*/?@", texto))
    if preco is None:
        preco = numero_decimal(primeiro(r"a\s*R\$\s*([\d.,]+)\s*/?@", texto))
    valor = numero_decimal(primeiro(r"Valor(?: total)?:\s*R\$\s*([\d.,]+)", texto))
    if valor is None:
        valor = numero_decimal(primeiro(r"Valor(?: total)?:[^\n]*?=\s*R\$\s*([\d.,]+)", texto))
    negociacao = primeiro(r"Negocia[çc][aã]o:\s*(\d{1,2}/\d{1,2}/\d{4})", texto)
    pesagem = primeiro(r"Pesagem:\s*(\d{1,2}/\d{1,2}/\d{4})", texto)
    pagamento = primeiro(r"Pagamento:\s*([^\n]+)", texto)
    if pagamento is None:
        pagamento = primeiro(r"Pagamento\s*(?:\n|•)+\s*(?:•\s*)?([^\n]+)", texto)
    if pagamento:
        pagamento = re.sub(r"^[•\-–—]\s*", "", pagamento).strip()
    pagamento = normalizar_pagamento(pagamento, negociacao)
    aliases, regras_mensagens = separar_configuracao(configuracao)
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
    sexo, categoria = inferir_sexo_categoria(texto)
    compra = {
        "contexto": mensagem["contexto"],
        "rotulo": rotulo,
        "rotulo_canonico": canonico,
        "negocio_origem": None,
        "sexo": sexo,
        "categoria": categoria,
        "destino": inferir_destino(texto),
        "tipo_evidencia": "negocio",
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
    return aplicar_regra_privada(compra, regras_mensagens.get(mensagem["mensagem_id"], {}))


def dia_mensagem(valor: str | None) -> str | None:
    achado = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", valor or "")
    return f"{achado.group(1)}/{achado.group(2)}/{achado.group(3)}" if achado else None


def data_humana(valor: str | None) -> str | None:
    achado = re.fullmatch(r"(20\d{2})-(\d{2})-(\d{2})", valor or "")
    return f"{achado.group(3)}/{achado.group(2)}/{achado.group(1)}" if achado else valor


def assinatura_campos(compra: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(compra.get(campo) for campo in (*IDENTIDADE_COMPRA, *CAMPOS_COMPRA))


def versoes_compativeis(primeira: dict[str, Any], segunda: dict[str, Any]) -> bool:
    for campo in CAMPOS_COMPRA:
        a, b = primeira.get(campo), segunda.get(campo)
        if a not in (None, "") and b not in (None, "") and a != b:
            return False
    return True


def consolidar_versoes_semanticas(versoes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Une repetições iguais ou parciais compatíveis sem combinar dados."""
    ordenadas = sorted(
        versoes,
        key=lambda item: (item["campos_preenchidos"], item["ordem"]),
        reverse=True,
    )
    grupos: list[dict[str, Any]] = []
    for item in ordenadas:
        compativeis = [grupo for grupo in grupos if versoes_compativeis(grupo["representante"], item)]
        if len(compativeis) == 1:
            grupo = compativeis[0]
            grupo["evidencias"].append(item)
            if (item["campos_preenchidos"], item["ordem"]) > (
                grupo["representante"]["campos_preenchidos"], grupo["representante"]["ordem"]
            ):
                grupo["representante"] = item
        else:
            grupos.append({"representante": item, "evidencias": [item]})
    return sorted(grupos, key=lambda grupo: grupo["representante"]["ordem"])


def campos_divergentes(versoes: list[dict[str, Any]]) -> list[str]:
    return [
        campo for campo in CAMPOS_COMPRA
        if len({versao.get(campo) for versao in versoes if versao.get(campo) not in (None, "")}) > 1
    ]


def campos_ausentes_em_todas(versoes: list[dict[str, Any]]) -> list[str]:
    return [
        campo for campo in CAMPOS_COMPRA
        if all(versao.get(campo) in (None, "") for versao in versoes)
    ]


def classificar_revisao(
    classificacao: str,
    divergentes: list[str],
    ausentes_minimos: list[str],
) -> tuple[str, str, str]:
    if classificacao == "repeticao_deduplicavel":
        return "repetição sem conflito", "baixa", "manter uma evidência e preservar as referências"
    if classificacao == "incompleto_campos_obrigatorios":
        financeiros = {"quantidade", "preco_arroba", "valor_total", "pagamento"}
        prioridade = "alta" if financeiros.intersection(ausentes_minimos) else "média"
        return (
            "completar dados do negócio",
            prioridade,
            "localizar os campos ausentes nas fontes sem preencher por aproximação",
        )
    if classificacao == "correcao_explicita_mais_recente":
        return "conferir correção explícita", "alta", "comparar a correção com a fonte e confirmar"
    financeiros = {"quantidade", "preco_arroba", "valor_total", "pagamento"}
    prioridade = "alta" if financeiros.intersection(divergentes) else "média"
    return "escolher a versão correta", prioridade, "conferir as mensagens e escolher sem combinar valores"


def atribuir_codigos_negocios(grupos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Atribui códigos anuais estáveis sem renumerar a conferência já aberta."""
    contadores_ano: Counter[str] = Counter()
    classes_historicas = {"ambiguo_multiplas_versoes", "correcao_explicita_mais_recente"}
    ordem_codigo = [
        *[
            grupo for grupo in grupos
            if grupo.get("negocio_origem") or grupo.get("classificacao") in classes_historicas
        ],
        *[
            grupo for grupo in grupos
            if not grupo.get("negocio_origem")
            and grupo.get("classificacao") == "incompleto_campos_obrigatorios"
        ],
        *[
            grupo for grupo in grupos
            if not grupo.get("negocio_origem")
            and grupo.get("classificacao") == "repeticao_deduplicavel"
        ],
    ]
    for grupo in ordem_codigo:
        referencia = grupo.get("negocio_origem") or grupo.get("data_base") or ""
        ano = primeiro(r"NEG-(\d{2})-", str(referencia))
        if not ano:
            ano_completo = primeiro(
                r"(?:^|\D)(20\d{2})(?:\D|$)", str(grupo.get("data_base") or "")
            )
            ano = ano_completo[-2:] if ano_completo else "00"
        contadores_ano[ano] += 1
        grupo["codigo_negocio"] = f"NEG-{ano}-{contadores_ano[ano]:03d}"
    return grupos


def atualizar_grupos_existentes(grupos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Atualiza um plano privado antigo sem reler ou combinar suas evidências."""
    for grupo in grupos:
        grupo["data_base"] = data_humana(grupo.get("data_base"))
        ausentes = grupo.get("campos_ausentes_em_todas") or []
        ausentes_minimos = [campo for campo in CAMPOS_MINIMOS_CONSOLIDACAO if campo in ausentes]
        grupo["campos_minimos_ausentes"] = ausentes_minimos
        grupo["campos_minimos_ausentes_humanos"] = [
            CAMPOS_COMPRA[campo] for campo in ausentes_minimos
        ]
        if grupo.get("classificacao") == "repeticao_deduplicavel" and ausentes_minimos:
            grupo["classificacao"] = "incompleto_campos_obrigatorios"
            situacao, prioridade, acao = classificar_revisao(
                grupo["classificacao"], grupo.get("campos_divergentes") or [], ausentes_minimos
            )
            grupo["situacao_revisao"] = situacao
            grupo["prioridade_revisao"] = prioridade
            grupo["acao_recomendada"] = acao
            grupo["requer_revisao"] = True
    return atribuir_codigos_negocios(grupos)


def agrupar_compras(compras: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grupos: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for compra in compras:
        data_base = compra["data_negociacao"] or dia_mensagem(compra["data_mensagem"]) or "sem_data"
        origem = compra.get("negocio_origem") or f"{compra['rotulo_canonico']}|{data_base}"
        identidade = "|".join(compra.get(campo) or NAO_INFORMADO for campo in IDENTIDADE_COMPRA)
        chave = f"{normalizar_texto(compra['contexto'])}|{origem}|{identidade}"
        grupos[chave].append(compra)
    saida = []
    for chave, versoes in sorted(grupos.items()):
        versoes.sort(key=lambda item: item["ordem"])
        versoes_semanticas = consolidar_versoes_semanticas(versoes)
        representantes = [grupo["representante"] for grupo in versoes_semanticas]
        ultima = representantes[-1]
        ausentes = campos_ausentes_em_todas(representantes)
        ausentes_minimos = [campo for campo in CAMPOS_MINIMOS_CONSOLIDACAO if campo in ausentes]
        if len(versoes_semanticas) == 1:
            classificacao = (
                "incompleto_campos_obrigatorios"
                if ausentes_minimos else "repeticao_deduplicavel"
            )
            preferida = ultima
        elif ultima["eh_correcao_explicita"]:
            classificacao = "correcao_explicita_mais_recente"
            preferida = ultima
        else:
            classificacao = "ambiguo_multiplas_versoes"
            preferida = None
        divergentes = campos_divergentes(representantes)
        situacao, prioridade, acao = classificar_revisao(
            classificacao, divergentes, ausentes_minimos
        )
        saida.append({
            "chave_provisoria": chave,
            "contexto": versoes[0]["contexto"],
            "negocio": versoes[0]["rotulo"],
            "negocio_origem": versoes[0].get("negocio_origem"),
            "sexo": versoes[0]["sexo"],
            "categoria": versoes[0]["categoria"],
            "destino": versoes[0]["destino"],
            "data_base": versoes[0]["data_negociacao"] or dia_mensagem(versoes[0]["data_mensagem"]),
            "classificacao": classificacao,
            "situacao_revisao": situacao,
            "prioridade_revisao": prioridade,
            "acao_recomendada": acao,
            "versoes": len(versoes_semanticas),
            "evidencias": len(versoes),
            "repeticoes_consolidadas": len(versoes) - len(versoes_semanticas),
            "campos_distintos": len(versoes_semanticas),
            "campos_divergentes": divergentes,
            "campos_divergentes_humanos": [CAMPOS_COMPRA[campo] for campo in divergentes],
            "campos_ausentes_em_todas": ausentes,
            "campos_ausentes_humanos": [CAMPOS_COMPRA[campo] for campo in ausentes],
            "campos_minimos_ausentes": ausentes_minimos,
            "campos_minimos_ausentes_humanos": [
                CAMPOS_COMPRA[campo] for campo in ausentes_minimos
            ],
            "confirmado": False,
            "requer_revisao": classificacao != "repeticao_deduplicavel",
            "versao_preferida": preferida,
            "versoes_revisao": [{
                "mensagem_id": grupo["representante"]["mensagem_id"],
                "mensagens": [
                    item["mensagem_id"]
                    for item in sorted(grupo["evidencias"], key=lambda evidencia: evidencia["ordem"])
                ],
                "ocorrencias": len(grupo["evidencias"]),
                "data_mensagem": grupo["representante"]["data_mensagem"],
                "eh_correcao_explicita": any(item["eh_correcao_explicita"] for item in grupo["evidencias"]),
                "dados": {campo: grupo["representante"].get(campo) for campo in CAMPOS_COMPRA},
            } for grupo in versoes_semanticas],
            "mensagens": [item["mensagem_id"] for item in versoes],
        })
    return atribuir_codigos_negocios(saida)


def carregar_aliases(caminho: Path | None) -> dict[str, Any]:
    if not caminho:
        return {}
    bruto = json.loads(caminho.read_text(encoding="utf-8"))
    pares = bruto.get("aliases", bruto)
    aliases = {normalizar_texto(chave): normalizar_texto(valor) for chave, valor in pares.items()}
    if "aliases" not in bruto and "regras_mensagens" not in bruto:
        return aliases
    return {"aliases": aliases, "regras_mensagens": bruto.get("regras_mensagens", {})}


def validar_plano_documental(documentos: dict[str, Any] | None) -> dict[str, Any]:
    if not documentos:
        return {
            "fornecido": False,
            "somente_leitura": True,
            "plano_id": None,
            "assinatura_sha256": None,
        }
    somente_leitura = (
        documentos.get("plano_gera_escrita") is False
        and documentos.get("escritas_executadas") == 0
        and documentos.get("tabelas_operacionais_alteradas") == 0
    )
    if not somente_leitura:
        raise ValueError("o plano documental precisa comprovar zero escrita operacional")
    assinavel = {chave: valor for chave, valor in documentos.items() if chave != "gerado_em"}
    fontes = documentos.get("fontes") or {}
    return {
        "fornecido": True,
        "somente_leitura": True,
        "plano_id": documentos.get("plano_id"),
        "assinatura_sha256": hashlib.sha256(
            json.dumps(assinavel, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest(),
        "vinculos_nf_gta": len(documentos.get("vinculos_nf_gta") or []),
        "candidatos_banco": len(documentos.get("candidatos_banco") or []),
        "candidatos_negocio": len(documentos.get("candidatos_negocio") or []),
        "transacoes_banco": (fontes.get("banco") or {}).get("transacoes"),
        "notas_agronotas": (fontes.get("agronotas") or {}).get("notas"),
        "notas_com_gta": (fontes.get("agronotas") or {}).get("com_gta"),
        "movimentos_ima": (fontes.get("ima") or {}).get("movimentos"),
        "negocios_fonte": (fontes.get("negocios") or {}).get("registros"),
    }


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


def finalizar_plano(plano: dict[str, Any]) -> dict[str, Any]:
    """Recalcula a fila e a assinatura sem criar ou alterar dado operacional."""
    grupos = atualizar_grupos_existentes(plano["grupos_compras"])
    ambiguos = sum(grupo["classificacao"] == "ambiguo_multiplas_versoes" for grupo in grupos)
    correcoes = sum(
        grupo["classificacao"] == "correcao_explicita_mais_recente" for grupo in grupos
    )
    incompletos = sum(
        grupo["classificacao"] == "incompleto_campos_obrigatorios" for grupo in grupos
    )
    plano["resumo"]["grupos_compras"] = len(grupos)
    plano["resumo"]["grupos_ambiguos"] = ambiguos
    plano["resumo"]["correcoes_explicitas_preferidas"] = correcoes
    plano["resumo"]["grupos_incompletos"] = incompletos
    fila_revisao = [grupo for grupo in grupos if grupo["requer_revisao"]]
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
        "campos_obrigatorios_faltantes": incompletos,
    }
    pendencias = []
    if ambiguos:
        pendencias.append("compras_com_multiplas_versoes_exigem_revisao")
    if incompletos:
        pendencias.append("compras_incompletas_exigem_complemento_documental")
    if plano["resumo"]["anexos_omitidos"]:
        pendencias.append("exportacoes_com_anexos_omitidos")
    variacao = plano["cortes"].get("variacao_ima_sem_detalhamento")
    if variacao not in (None, 0):
        pendencias.append("saldo_ima_variou_sem_ficha_detalhada_correspondente")
    if plano["cruzamento_gta"]["vinculos_exatos"]:
        pendencias.append("vinculos_gta_documentais_exigem_conferencia")
    plano["pendencias"] = pendencias
    assinavel = {
        chave: valor
        for chave, valor in plano.items()
        if chave not in {"gerado_em", "plano_id"}
    }
    plano["plano_id"] = hashlib.sha256(
        json.dumps(assinavel, ensure_ascii=False, sort_keys=True, default=serializar).encode()
    ).hexdigest()[:12]
    return plano


def gerar_plano(exportacoes: list[dict[str, Any]], aliases: dict[str, Any],
                documentos: dict[str, Any] | None = None,
                complemento_ima: dict[str, Any] | None = None) -> dict[str, Any]:
    validacao_documental = validar_plano_documental(documentos)
    mensagens = [mensagem for exportacao in exportacoes for mensagem in exportacao["mensagens"]]
    for ordem_global, mensagem in enumerate(mensagens):
        mensagem["ordem_global"] = ordem_global
    anexos = [anexo for exportacao in exportacoes for anexo in exportacao["anexos"]]
    compras_brutas = [compra for mensagem in mensagens if (compra := extrair_compra(mensagem, aliases))]
    vistos: set[tuple[str, str]] = set()
    compras = []
    evidencias_agregadas = []
    for compra in compras_brutas:
        chave_deduplicacao = (compra["contexto"], compra["texto_sha256"])
        if chave_deduplicacao in vistos:
            continue
        vistos.add(chave_deduplicacao)
        if compra["tipo_evidencia"] == "resumo_agregado":
            evidencias_agregadas.append(compra)
        elif not compra["eh_teste"]:
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
        "validacao_documental": validacao_documental,
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
            "resumos_agregados_preservados": len(evidencias_agregadas),
        },
        "grupos_compras": agrupar_compras(compras),
        "evidencias_agregadas": evidencias_agregadas,
        "cruzamento_gta": cruzar_gtas(mensagens, documentos),
        "anexos": anexos,
        "pendencias": [],
    }
    return finalizar_plano(plano)


def relatorio_markdown(plano: dict[str, Any]) -> str:
    resumo, cortes, gta = plano["resumo"], plano["cortes"], plano["cruzamento_gta"]
    documental = plano["validacao_documental"]
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
            f"| {grupo['codigo_negocio']} | {grupo.get('negocio_origem') or '—'} | "
            f"{grupo['prioridade_revisao']} | {grupo['situacao_revisao']} | "
            f"{grupo['contexto']} | {grupo['negocio']} | {grupo['sexo']} | {grupo['categoria']} | "
            f"{grupo['destino']} | {grupo['data_base'] or 'sem data'} | {grupo['versoes']} | "
            f"{grupo['evidencias']} | {divergentes} | {ausentes} | "
            f"{grupo['acao_recomendada']} |"
        )
    tabela_revisao = (
        "\n".join(linhas_revisao)
        or "| — | — | — | — | — | — | — | — | — | 0 | 0 | — | — | nenhuma revisão |"
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
- {resumo['resumos_agregados_preservados']} resumos agregados preservados apenas como evidência;
- {resumo['grupos_compras']} grupos provisórios;
- {resumo['grupos_ambiguos']} grupos permanecem ambíguos;
- {resumo['grupos_incompletos']} grupos sem conflito ainda têm campos mínimos ausentes;
- {resumo['correcoes_explicitas_preferidas']} grupos têm correção posterior explicitamente indicada;
- {gta['vinculos_exatos']} vínculos GTA exatos com a fonte documental.

## Conferência das fontes documentais

- plano documental fornecido: {'sim' if documental['fornecido'] else 'não'};
- plano documental somente leitura: {'sim' if documental['somente_leitura'] else 'não'};
- vínculos NF/GTA na fonte: {documental.get('vinculos_nf_gta') or 0};
- notas do Agronotas: {documental.get('notas_agronotas') or 0}, das quais {documental.get('notas_com_gta') or 0} com GTA;
- transações no extrato: {documental.get('transacoes_banco') or 0};
- candidatos bancários automáticos: {documental.get('candidatos_banco') or 0};
- negócios na fonte de referência: {documental.get('negocios_fonte') or 0};
- candidatos de negócio automáticos: {documental.get('candidatos_negocio') or 0};
- movimentos na ficha detalhada do IMA: {documental.get('movimentos_ima') or 0}.

A ausência de candidatos automáticos não é preenchida por aproximação de valor
ou data; ela permanece como pendência de conciliação.

## Fila privada de conferência por negócio

| Código | Vínculo de origem | Prioridade | Situação | Contexto | Negócio | Sexo | Categoria | Destino | Data-base | Versões | Evidências | O que diverge | Ausente em todas | Próxima ação |
|---|---|---|---|---|---|---|---|---|---|---:|---:|---|---|---|
{tabela_revisao}

Esta fila não combina campos de versões diferentes. A versão indicada por uma
correção explícita continua pendente de confirmação na fonte.

## Regras validadas

- correção explícita posterior pode ser preferida, mas nunca confirmada automaticamente;
- mesmo fornecedor e mesma data com campos diferentes permanece ambíguo;
- versões iguais ou parciais compatíveis viram uma única alternativa, preservando todas as mensagens;
- versão única com campo mínimo ausente continua na fila para complemento documental;
- códigos anuais `NEG-AA-NNN` são determinísticos e não dependem da planilha;
- sexo, categoria e destino fazem parte da identidade do negócio;
- resumo agregado não vira uma avaliação adicional;
- GTA exata forma candidato documental forte, ainda não confirmado;
- valor/data isolados não são usados por este importador;
- testes e exemplos são excluídos dos negócios reais;
- nenhum resultado cria compra, venda, GTA, pagamento, rascunho ou evento.

## Pendências

{pendencias}
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    origem = parser.add_mutually_exclusive_group(required=True)
    origem.add_argument("--telegram-html", action="append", type=Path)
    origem.add_argument("--plano-existente", type=Path)
    parser.add_argument("--contexto", action="append")
    parser.add_argument("--aliases", type=Path)
    parser.add_argument("--documentos-plano", type=Path)
    parser.add_argument("--complemento-ima", type=Path)
    parser.add_argument("--saida-json", required=True, type=Path)
    parser.add_argument("--saida-md", required=True, type=Path)
    args = parser.parse_args()
    if args.plano_existente:
        if args.contexto or args.aliases or args.documentos_plano or args.complemento_ima:
            parser.error("--plano-existente não aceita fontes adicionais")
        plano = json.loads(args.plano_existente.read_text(encoding="utf-8"))
        if not (
            plano.get("plano_gera_escrita") is False
            and plano.get("escritas_executadas") == 0
            and plano.get("tabelas_operacionais_alteradas") == 0
        ):
            parser.error("--plano-existente exige comprovação de zero escrita operacional")
        plano["gerado_em"] = datetime.now().astimezone().isoformat()
        plano = finalizar_plano(plano)
    else:
        if args.contexto and len(args.contexto) != len(args.telegram_html):
            parser.error("--contexto deve ser repetido uma vez para cada --telegram-html")
        contextos = args.contexto or [None] * len(args.telegram_html)
        exportacoes = [
            ler_exportacao(caminho, contexto)
            for caminho, contexto in zip(args.telegram_html, contextos)
        ]
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
