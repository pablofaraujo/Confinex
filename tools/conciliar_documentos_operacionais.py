#!/usr/bin/env python3
"""Cruza NF do Agronotas, GTA do IMA, OFX e negócios sem executar escrita."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import posixpath
import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET
from zipfile import ZipFile

try:
    from analisar_extrato_ofx import campo_ofx, data_ofx
    from analisar_ficha_ima import extrair_texto_pdf, ler_ficha
except ModuleNotFoundError:
    from tools.analisar_extrato_ofx import campo_ofx, data_ofx
    from tools.analisar_ficha_ima import extrair_texto_pdf, ler_ficha


NS_PLANILHA = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
ALIASES = {
    "nf": ("nf", "nfe", "nota", "nota fiscal", "numero", "numero nf", "numero nota fiscal", "numero_documento", "nf venda", "numero_nf", "n nota fiscal"),
    "gta": ("gta", "numero gta", "numero_gta", "gta numero", "gta_numero"),
    "data": ("data", "data emissao", "data_emissao", "dataemissao", "data movimento", "data movimentacao", "data_movimentacao", "data negocio", "data_negocio"),
    "valor": ("valor", "valor total", "valor_total", "valortotal", "valor documento", "valor_documento", "valor nf", "valor_nf", "valor compra", "valor_compra"),
    "quantidade": ("quantidade", "cabecas", "quantidade cabecas", "quantidade_cabecas", "qtde", "qtde gta", "qtde_gta"),
    "observacao": ("observacao", "observacoes", "comentario", "comentarios", "historico", "informacoes complementares", "dados adicionais", "descricao"),
    "codigo": ("codigo", "negocio", "negocio id", "negocio_id",
               "numero negocio compra", "numero_negocio_compra",
               "operacao", "operacao id", "operacao_id", "codigo operacao",
               "codigo_operacao"),
}


def normalizar_texto(valor: Any) -> str:
    texto = unicodedata.normalize("NFKD", str(valor or ""))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-zA-Z0-9]+", " ", texto).strip().lower())


ALIASES_NORMALIZADOS = {
    campo: {normalizar_texto(alias) for alias in aliases}
    for campo, aliases in ALIASES.items()
}


def sha256_arquivo(caminho: Path) -> str:
    digest = hashlib.sha256()
    with caminho.open("rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(1024 * 1024), b""):
            digest.update(bloco)
    return digest.hexdigest()


def normalizar_numero(valor: Any) -> str:
    return re.sub(r"\D", "", str(valor or ""))


def extrair_gtas(texto: Any) -> list[str]:
    padroes = (
        r"\bGTA\d{1,2}[\s_./-]+(\d{5,12})\b",
        r"\bGTA(?:\s+(?:N(?:UMERO|[ºO°.])))?\s*[:#.-]?\s*(?:[A-Z]{2}\s+)?(?:[A-Z]\s+)?(\d{5,12})\b",
    )
    localizados: list[tuple[int, str]] = []
    for padrao in padroes:
        for correspondencia in re.finditer(padrao, str(texto or ""), flags=re.I):
            numero = normalizar_numero(correspondencia.group(1))
            if numero:
                localizados.append((correspondencia.start(), numero))
    encontrados: list[str] = []
    for _, numero in sorted(localizados):
        if numero not in encontrados:
            encontrados.append(numero)
    return encontrados


def extrair_gtas_campo(valor: Any) -> list[str]:
    if valor in (None, ""):
        return []
    encontrados = extrair_gtas(valor)
    for numero in re.findall(r"(?<!\d)(\d{5,12})(?!\d)", str(valor)):
        numero = normalizar_numero(numero)
        if numero not in encontrados:
            encontrados.append(numero)
    return encontrados


def extrair_nfs(texto: Any) -> list[str]:
    encontrados: list[str] = []
    for numero in re.findall(
        r"\bNF(?:-?E)?(?:\s+(?:N(?:UMERO|[ºO°.])))?\s*[:#.-]?\s*(\d{3,12})\b",
        str(texto or ""), flags=re.I,
    ):
        numero = normalizar_numero(numero)
        if numero and numero not in encontrados:
            encontrados.append(numero)
    return encontrados


def decimal_valor(valor: Any) -> Decimal | None:
    if valor in (None, ""):
        return None
    if isinstance(valor, (int, float, Decimal)):
        return Decimal(str(valor)).quantize(Decimal("0.01"))
    texto = str(valor).strip().replace("R$", "").replace(" ", "")
    negativo = texto.startswith("(") and texto.endswith(")")
    texto = texto.strip("()")
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        numero = Decimal(texto).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None
    return -numero if negativo else numero


def data_valor(valor: Any) -> str | None:
    if valor in (None, ""):
        return None
    if isinstance(valor, datetime):
        return valor.date().isoformat()
    if isinstance(valor, date):
        return valor.isoformat()
    if isinstance(valor, (int, float)) and 20_000 < float(valor) < 80_000:
        return (date(1899, 12, 30) + timedelta(days=int(float(valor)))).isoformat()
    texto = str(valor).strip()
    for formato in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(texto[:10], formato).date().isoformat()
        except ValueError:
            pass
    encontrado = re.search(r"(\d{4}-\d{2}-\d{2})", texto)
    return encontrado.group(1) if encontrado else None


def valor_campo(registro: dict[str, Any], campo: str) -> Any:
    por_nome = {normalizar_texto(chave): valor for chave, valor in registro.items()}
    for alias in ALIASES_NORMALIZADOS[campo]:
        if alias in por_nome and por_nome[alias] not in (None, ""):
            return por_nome[alias]
    return None


def textos_campo(registro: dict[str, Any], campo: str) -> list[str]:
    aliases = ALIASES_NORMALIZADOS[campo]
    return [str(valor) for chave, valor in registro.items()
            if normalizar_texto(chave) in aliases and valor not in (None, "")]


def valor_por_nomes(registro: dict[str, Any], *nomes: str) -> Any:
    procurados = {normalizar_texto(nome) for nome in nomes}
    for chave, valor in registro.items():
        if normalizar_texto(chave) in procurados and valor not in (None, ""):
            return valor
    return None


def indice_coluna(referencia: str) -> int:
    letras = re.match(r"[A-Z]+", referencia.upper())
    if not letras:
        return 0
    indice = 0
    for letra in letras.group(0):
        indice = indice * 26 + ord(letra) - 64
    return indice - 1


def ler_matrizes_xlsx(caminho: Path) -> dict[str, list[list[Any]]]:
    with ZipFile(caminho) as pacote:
        compartilhadas: list[str] = []
        if "xl/sharedStrings.xml" in pacote.namelist():
            raiz = ET.fromstring(pacote.read("xl/sharedStrings.xml"))
            compartilhadas = ["".join(item.itertext()) for item in raiz.findall("m:si", NS_PLANILHA)]
        workbook = ET.fromstring(pacote.read("xl/workbook.xml"))
        rels = ET.fromstring(pacote.read("xl/_rels/workbook.xml.rels"))
        destinos = {item.attrib["Id"]: item.attrib["Target"] for item in rels}
        resultado: dict[str, list[list[Any]]] = {}
        abas = workbook.find("m:sheets", NS_PLANILHA)
        for aba in abas if abas is not None else []:
            nome = aba.attrib["name"]
            destino = destinos[aba.attrib[f"{{{NS_REL}}}id"]]
            xml = posixpath.normpath(posixpath.join("xl", destino.lstrip("/")))
            if xml not in pacote.namelist() and destino.startswith("/"):
                xml = destino.lstrip("/")
            raiz = ET.fromstring(pacote.read(xml))
            linhas: list[list[Any]] = []
            for linha in raiz.findall(".//m:sheetData/m:row", NS_PLANILHA):
                valores: dict[int, Any] = {}
                for celula in linha.findall("m:c", NS_PLANILHA):
                    coluna = indice_coluna(celula.attrib.get("r", "A1"))
                    tipo = celula.attrib.get("t")
                    valor_xml = celula.find("m:v", NS_PLANILHA)
                    if tipo == "inlineStr":
                        valor = "".join(celula.itertext())
                    elif valor_xml is None:
                        valor = None
                    elif tipo == "s" and (valor_xml.text or "").isdigit():
                        valor = compartilhadas[int(valor_xml.text or "0")]
                    elif tipo == "b":
                        valor = valor_xml.text == "1"
                    else:
                        bruto = valor_xml.text or ""
                        try:
                            valor = float(bruto) if "." in bruto else int(bruto)
                        except ValueError:
                            valor = bruto
                    valores[coluna] = valor
                if valores:
                    linhas.append([valores.get(i) for i in range(max(valores) + 1)])
            resultado[nome] = linhas
    return resultado


def pontuar_cabecalho(linha: Iterable[Any]) -> int:
    nomes = {normalizar_texto(valor) for valor in linha if valor not in (None, "")}
    return sum(bool(nomes & aliases) for aliases in ALIASES_NORMALIZADOS.values())


def matriz_para_registros(linhas: list[list[Any]]) -> list[dict[str, Any]]:
    if not linhas:
        return []
    limite = min(len(linhas), 40)
    indice = max(range(limite), key=lambda posicao: pontuar_cabecalho(linhas[posicao]))
    if pontuar_cabecalho(linhas[indice]) < 2:
        raise ValueError("cabeçalho operacional não identificado")
    cabecalho = [str(valor or "").strip() for valor in linhas[indice]]
    registros = []
    for numero_linha, linha in enumerate(linhas[indice + 1:], start=indice + 2):
        completa = linha + [None] * (len(cabecalho) - len(linha))
        registro = {(nome or f"coluna_{coluna + 1}"): completa[coluna]
                    for coluna, nome in enumerate(cabecalho)}
        if any(valor not in (None, "") for valor in registro.values()):
            registro["__linha__"] = numero_linha
            registros.append(registro)
    return registros


def escolher_aba(matrizes: dict[str, list[list[Any]]], nome: str | None) -> str:
    if nome:
        if nome not in matrizes:
            raise ValueError(f"aba não encontrada: {nome}")
        return nome
    pontuadas = [(max((pontuar_cabecalho(l) for l in linhas[:40]), default=0), aba)
                 for aba, linhas in matrizes.items()]
    pontuacao, aba = max(pontuadas, default=(0, ""))
    if pontuacao < 2:
        raise ValueError("nenhuma aba operacional identificada")
    return aba


def carregar_registros(caminho: Path, aba: str | None = None) -> list[dict[str, Any]]:
    extensao = caminho.suffix.lower()
    if extensao == ".xlsx":
        matrizes = ler_matrizes_xlsx(caminho)
        return matriz_para_registros(matrizes[escolher_aba(matrizes, aba)])
    if extensao == ".csv":
        conteudo = caminho.read_text(encoding="utf-8-sig", errors="replace")
        dialecto = csv.Sniffer().sniff(conteudo[:8192], delimiters=",;\t")
        return [dict(linha, __linha__=i) for i, linha in enumerate(
            csv.DictReader(conteudo.splitlines(), dialect=dialecto), start=2)]
    if extensao == ".json":
        payload = json.loads(caminho.read_text(encoding="utf-8"))
        if isinstance(payload, list) and all(isinstance(item, dict) for item in payload):
            return [dict(item, __linha__=i) for i, item in enumerate(payload, start=1)]
        if isinstance(payload, dict) and aba and isinstance(payload.get(aba), list):
            matriz = payload[aba]
            if matriz and all(isinstance(item, dict) for item in matriz):
                return [dict(item, __linha__=i) for i, item in enumerate(matriz, start=1)]
            return matriz_para_registros(matriz)
        raise ValueError("JSON deve conter lista de registros ou matriz na aba informada")
    raise ValueError(f"formato não aceito: {extensao or 'sem extensão'}")


def ler_agronotas(caminho: Path, aba: str | None = None) -> dict[str, Any]:
    registros, vistos, duplicados, ignorados = [], set(), 0, 0
    for bruto in carregar_registros(caminho, aba):
        observacoes = " | ".join(textos_campo(bruto, "observacao"))
        texto_completo = " | ".join(str(valor) for valor in bruto.values()
                                    if valor not in (None, ""))
        tipo = normalizar_texto(valor_por_nomes(bruto, "tipo_documento", "tipo documento"))
        numero_documento = normalizar_numero(valor_por_nomes(
            bruto, "numero_documento", "número documento"))
        nf = "" if "gta" in tipo else normalizar_numero(valor_campo(bruto, "nf"))
        gtas: list[str] = []
        for gta in extrair_gtas_campo(valor_campo(bruto, "gta")):
            if gta not in gtas:
                gtas.append(gta)
        if "gta" in tipo and numero_documento and numero_documento not in gtas:
            gtas.append(numero_documento)
        for gta in extrair_gtas(texto_completo):
            if gta not in gtas:
                gtas.append(gta)
        possui_vinculo = any(valor_por_nomes(bruto, nome) not in (None, "") for nome in (
            "compra_id", "confinamento_id", "venda_id", "abate_id"))
        texto_normalizado = normalizar_texto(texto_completo)
        relacionado_a_gado = bool(re.search(
            r"\b(bovinos?|bubalinos?|bezerros?|garrotes?|novilh[oa]s?|vacas?|animais? vivos?)\b",
            texto_normalizado,
        ))
        if not gtas and not possui_vinculo and not relacionado_a_gado:
            ignorados += 1
            continue
        if not nf and not gtas:
            continue
        item = {
            "linha": bruto.get("__linha__"), "nf": nf or None, "gtas": gtas,
            "data": data_valor(valor_campo(bruto, "data")),
            "valor": decimal_valor(valor_campo(bruto, "valor")),
            "quantidade": decimal_valor(valor_campo(bruto, "quantidade")),
        }
        chave = (item["nf"], tuple(gtas), item["data"], str(item["valor"]))
        if chave in vistos:
            duplicados += 1
        else:
            vistos.add(chave)
            registros.append(item)
    return {"arquivo": caminho.name, "sha256": sha256_arquivo(caminho),
            "registros": registros, "duplicados": duplicados,
            "ignorados_nao_pecuarios": ignorados}


def combinar_agronotas(fontes: list[dict[str, Any]]) -> dict[str, Any]:
    """Combina exportações históricas/incrementais sem duplicar documentos."""
    registros: list[dict[str, Any]] = []
    vistos: set[tuple[Any, ...]] = set()
    duplicados = 0
    for fonte in fontes:
        duplicados += int(fonte.get("duplicados") or 0)
        for item in fonte.get("registros") or []:
            if item.get("nf"):
                chave = ("nf", item.get("nf"), item.get("data"), str(item.get("valor")))
            else:
                chave = ("gta", tuple(item.get("gtas") or []), item.get("data"),
                         str(item.get("quantidade")))
            if chave in vistos:
                duplicados += 1
                continue
            vistos.add(chave)
            registros.append(item)
    arquivos = [fonte["arquivo"] for fonte in fontes]
    hashes = [fonte["sha256"] for fonte in fontes]
    return {
        "arquivo": ", ".join(arquivos),
        "arquivos": arquivos,
        "sha256": hashlib.sha256("|".join(hashes).encode()).hexdigest(),
        "registros": registros,
        "duplicados": duplicados,
        "ignorados_nao_pecuarios": sum(
            int(fonte.get("ignorados_nao_pecuarios") or 0) for fonte in fontes
        ),
    }


def ler_negocios(caminho: Path, abas: list[str] | None = None) -> dict[str, Any]:
    matrizes = ler_matrizes_xlsx(caminho) if caminho.suffix.lower() == ".xlsx" else None
    nomes = abas or ([escolher_aba(matrizes, None)] if matrizes else [None])
    negocios = []
    for aba in nomes:
        registros = matriz_para_registros(matrizes[aba]) if matrizes else carregar_registros(caminho, aba)
        for bruto in registros:
            texto = " | ".join(str(v) for v in bruto.values() if v not in (None, ""))
            gtas = extrair_gtas(texto)
            for gta in reversed(extrair_gtas_campo(valor_campo(bruto, "gta"))):
                if gta not in gtas:
                    gtas.insert(0, gta)
            nfs = extrair_nfs(texto)
            nf = normalizar_numero(valor_campo(bruto, "nf"))
            if nf and nf not in nfs:
                nfs.insert(0, nf)
            codigo = str(valor_campo(bruto, "codigo") or "").strip() or f"{aba or 'dados'}:linha-{bruto.get('__linha__')}"
            negocios.append({
                "codigo": codigo, "aba": aba, "linha": bruto.get("__linha__"),
                "gtas": gtas, "nfs": nfs,
                "data": data_valor(valor_campo(bruto, "data")),
                "valor": decimal_valor(valor_campo(bruto, "valor")),
            })
    codigos = {str(item["codigo"]) for item in negocios if item.get("codigo")}
    padrao_operacional = re.compile(r"^(?:CF|NEG)-\d{2}-\d{3}$", re.I)
    return {
        "arquivo": caminho.name,
        "sha256": sha256_arquivo(caminho),
        "registros": negocios,
        "codigos_unicos": len(codigos),
        "codigos_operacionais": sum(bool(padrao_operacional.fullmatch(c)) for c in codigos),
        "contextos_agregadores": sum(not padrao_operacional.fullmatch(c) for c in codigos),
    }


def ler_ofx_detalhado(caminho: Path) -> dict[str, Any]:
    bytes_ = caminho.read_bytes()
    conteudo = bytes_.decode("latin-1", errors="replace")
    if "<OFX>" not in conteudo.upper():
        raise ValueError("arquivo sem estrutura OFX reconhecível")
    transacoes = []
    for bloco in re.findall(r"<STMTTRN>(.*?)(?=<STMTTRN>|</BANKTRANLIST>)", conteudo, re.S | re.I):
        fitid = campo_ofx(bloco, "FITID")
        data = data_ofx(campo_ofx(bloco, "DTPOSTED"))
        valor = decimal_valor(campo_ofx(bloco, "TRNAMT"))
        if fitid and data and valor is not None:
            transacoes.append({"fitid_hash": hashlib.sha256(fitid.encode()).hexdigest()[:12],
                               "data": data, "valor": valor})
    return {"arquivo": caminho.name, "sha256": hashlib.sha256(bytes_).hexdigest(),
            "transacoes": transacoes}


def combinar_extratos(fontes: list[dict[str, Any]]) -> dict[str, Any]:
    """Combina contas/períodos OFX e remove sobreposições pelo FITID."""
    transacoes: list[dict[str, Any]] = []
    vistos: set[str] = set()
    duplicados = 0
    for fonte in fontes:
        for item in fonte.get("transacoes") or []:
            chave = str(item.get("fitid_hash") or "")
            if chave and chave in vistos:
                duplicados += 1
                continue
            if chave:
                vistos.add(chave)
            transacoes.append(item)
    arquivos = [fonte["arquivo"] for fonte in fontes]
    hashes = [fonte["sha256"] for fonte in fontes]
    return {
        "arquivo": ", ".join(arquivos),
        "arquivos": arquivos,
        "sha256": hashlib.sha256("|".join(hashes).encode()).hexdigest(),
        "transacoes": transacoes,
        "duplicados_ignorados": duplicados,
    }


def distancia_dias(data_a: str | None, data_b: str | None) -> int | None:
    if not data_a or not data_b:
        return None
    return abs((date.fromisoformat(data_a) - date.fromisoformat(data_b)).days)


def indice_lista(registros: list[dict[str, Any]], campo: str) -> dict[str, list[dict[str, Any]]]:
    indice: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for registro in registros:
        for valor in registro.get(campo) or []:
            indice[str(valor)].append(registro)
    return indice


def serializar(objeto: Any) -> Any:
    if isinstance(objeto, Decimal):
        return str(objeto)
    raise TypeError(type(objeto).__name__)


def gerar_plano(agronotas: dict[str, Any], ficha: dict[str, Any], banco: dict[str, Any],
                negocios: dict[str, Any] | None, referencia: date) -> dict[str, Any]:
    por_gta_ima: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for movimento in ficha["movimentos"]:
        por_gta_ima[normalizar_numero(movimento.get("gta"))].append(movimento)

    vinculos_nf_gta = []
    for nota in agronotas["registros"]:
        data_nf = nota.get("data")
        fora_periodo_ima = bool(
            data_nf and (
                data_nf < ficha["periodo_inicial"]
                or data_nf > ficha["periodo_final"]
            )
        )
        if not nota["gtas"]:
            vinculos_nf_gta.append({
                "nf": nota["nf"], "linha_agronotas": nota["linha"],
                "data_nf": data_nf,
                "classificacao": "fora_periodo_ima" if fora_periodo_ima else "pendente",
                "criterio": (
                    "documento_fora_do_periodo_ima"
                    if fora_periodo_ima else "gta_ausente_na_nf"
                ),
            })
        for gta in nota["gtas"]:
            correspondencias = por_gta_ima.get(gta, [])
            if len(correspondencias) == 1:
                movimento = correspondencias[0]
                quantidade_confere = (nota["quantidade"] is None or
                    Decimal(str(movimento.get("quantidade"))) == nota["quantidade"])
                vinculos_nf_gta.append({
                    "nf": nota["nf"], "gta": gta, "linha_agronotas": nota["linha"],
                    "classificacao": "forte", "criterio": "gta_exata_nf_ima",
                    "data_nf": nota["data"], "data_gta": movimento.get("data"),
                    "quantidade_nf": nota["quantidade"], "quantidade_gta": movimento.get("quantidade"),
                    "sentido_gta": movimento.get("sentido"), "quantidade_confere": quantidade_confere,
                })
            elif correspondencias:
                vinculos_nf_gta.append({"nf": nota["nf"], "gta": gta, "linha_agronotas": nota["linha"],
                    "classificacao": "ambiguo", "criterio": "gta_repetida_no_ima",
                    "correspondencias": len(correspondencias)})
            else:
                vinculos_nf_gta.append({
                    "nf": nota["nf"], "gta": gta,
                    "linha_agronotas": nota["linha"], "data_nf": data_nf,
                    "classificacao": "fora_periodo_ima" if fora_periodo_ima else "pendente",
                    "criterio": (
                        "documento_fora_do_periodo_ima"
                        if fora_periodo_ima else "gta_nao_encontrada_no_ima"
                    ),
                })

    candidatos_banco = []
    for nota in agronotas["registros"]:
        if nota["valor"] is None:
            continue
        candidatos = [t for t in banco["transacoes"]
            if abs(t["valor"]) == abs(nota["valor"])
            and (distancia_dias(nota["data"], t["data"]) is None or distancia_dias(nota["data"], t["data"]) <= 90)]
        if len(candidatos) == 1:
            t = candidatos[0]
            candidatos_banco.append({"nf": nota["nf"], "linha_agronotas": nota["linha"],
                "fitid_hash": t["fitid_hash"], "data_nf": nota["data"], "data_banco": t["data"],
                "valor": nota["valor"], "distancia_dias": distancia_dias(nota["data"], t["data"]),
                "classificacao": "provavel", "criterio": "valor_exato_candidato_unico", "confirmado": False})
        elif len(candidatos) > 1:
            candidatos_banco.append({"nf": nota["nf"], "linha_agronotas": nota["linha"],
                "valor": nota["valor"], "correspondencias": len(candidatos),
                "classificacao": "ambiguo", "criterio": "valor_exato_repetido", "confirmado": False})

    registros_negocio = (negocios or {}).get("registros") or []
    por_gta_negocio, por_nf_negocio = indice_lista(registros_negocio, "gtas"), indice_lista(registros_negocio, "nfs")
    candidatos_negocio = []
    for nota in agronotas["registros"]:
        encontrados: dict[str, dict[str, Any]] = {}
        criterios: dict[str, set[str]] = defaultdict(set)
        for gta in nota["gtas"]:
            for negocio in por_gta_negocio.get(gta, []):
                encontrados[negocio["codigo"]] = negocio; criterios[negocio["codigo"]].add("gta_exata")
        if nota["nf"]:
            for negocio in por_nf_negocio.get(nota["nf"], []):
                encontrados[negocio["codigo"]] = negocio; criterios[negocio["codigo"]].add("nf_exata")
        if not encontrados and nota["valor"] is not None:
            for negocio in registros_negocio:
                if (negocio["valor"] is not None and abs(negocio["valor"]) == abs(nota["valor"])
                    and (distancia_dias(nota["data"], negocio["data"]) is None
                         or distancia_dias(nota["data"], negocio["data"]) <= 30)):
                    encontrados[negocio["codigo"]] = negocio; criterios[negocio["codigo"]].add("valor_e_data")
        if len(encontrados) == 1:
            codigo, negocio = next(iter(encontrados.items()))
            usados = sorted(criterios[codigo]); forte = "gta_exata" in usados or "nf_exata" in usados
            candidatos_negocio.append({"nf": nota["nf"], "gtas": nota["gtas"], "codigo_negocio": codigo,
                "aba_negocio": negocio["aba"], "linha_negocio": negocio["linha"],
                "classificacao": "forte" if forte else "provavel", "criterios": usados, "confirmado": False})
        elif len(encontrados) > 1:
            candidatos_negocio.append({"nf": nota["nf"], "gtas": nota["gtas"], "classificacao": "ambiguo",
                "correspondencias": len(encontrados), "criterios": sorted(set().union(*criterios.values())),
                "confirmado": False})

    def contagem(itens: list[dict[str, Any]]) -> dict[str, int]:
        saida: dict[str, int] = defaultdict(int)
        for item in itens:
            saida[item["classificacao"]] += 1
        return dict(sorted(saida.items()))

    plano = {
        "gerado_em": datetime.now().astimezone().isoformat(), "data_referencia": referencia.isoformat(),
        "modo": "dry_run_somente_leitura", "plano_gera_escrita": False,
        "escritas_executadas": 0, "tabelas_operacionais_alteradas": 0,
        "fontes": {
            "agronotas": {"arquivo": agronotas["arquivo"], "sha256": agronotas["sha256"],
                "arquivos": agronotas.get("arquivos", [agronotas["arquivo"]]),
                "notas": len(agronotas["registros"]), "duplicados_ignorados": agronotas["duplicados"],
                "documentos_nao_pecuarios_ignorados": agronotas.get("ignorados_nao_pecuarios", 0),
                "com_gta": sum(bool(i["gtas"]) for i in agronotas["registros"]),
                "data_final": max((i["data"] for i in agronotas["registros"] if i["data"]), default=None),
                "consultado_ate": agronotas.get("consultado_ate")},
            "ima": {"arquivo_sha256": ficha["sha256"], "periodo_inicial": ficha["periodo_inicial"],
                "periodo_final": ficha["periodo_final"], "movimentos": len(ficha["movimentos"]),
                "saldo_rebanho": ficha["saldo_rebanho"]},
            "banco": {"arquivo": banco["arquivo"], "sha256": banco["sha256"],
                "arquivos": banco.get("arquivos", [banco["arquivo"]]),
                "transacoes": len(banco["transacoes"]),
                "duplicados_ignorados": banco.get("duplicados_ignorados", 0),
                "data_final": max((i["data"] for i in banco["transacoes"]), default=None)},
            "negocios": {"arquivo": (negocios or {}).get("arquivo"), "sha256": (negocios or {}).get("sha256"),
                "registros": len(registros_negocio),
                "codigos_unicos": (negocios or {}).get("codigos_unicos", 0),
                "codigos_operacionais": (negocios or {}).get("codigos_operacionais", 0),
                "contextos_agregadores": (negocios or {}).get("contextos_agregadores", 0)},
        },
        "resumo": {"vinculos_nf_gta": contagem(vinculos_nf_gta),
            "candidatos_banco": contagem(candidatos_banco), "candidatos_negocio": contagem(candidatos_negocio)},
        "vinculos_nf_gta": vinculos_nf_gta, "candidatos_banco": candidatos_banco,
        "candidatos_negocio": candidatos_negocio, "pendencias": [],
    }
    corte_agronotas = (plano["fontes"]["agronotas"].get("consultado_ate")
                       or plano["fontes"]["agronotas"]["data_final"])
    if corte_agronotas != referencia.isoformat(): plano["pendencias"].append("agronotas_nao_consultado_ate_data_de_referencia")
    if ficha["periodo_final"] != referencia.isoformat(): plano["pendencias"].append("ima_nao_chega_a_data_de_referencia")
    if plano["fontes"]["banco"]["data_final"] != referencia.isoformat(): plano["pendencias"].append("extrato_nao_chega_a_data_de_referencia")
    if plano["resumo"]["vinculos_nf_gta"].get("pendente"): plano["pendencias"].append("nfs_ou_gtas_sem_correspondencia_exigem_revisao")
    if any(plano["resumo"][chave].get("ambiguo") for chave in plano["resumo"]): plano["pendencias"].append("referencias_ambiguas_preservadas_sem_vinculo")
    if candidatos_banco: plano["pendencias"].append("pagamentos_candidatos_exigem_confirmacao")
    if candidatos_negocio: plano["pendencias"].append("negocios_candidatos_exigem_confirmacao")
    assinavel = {chave: valor for chave, valor in plano.items() if chave != "gerado_em"}
    plano["plano_id"] = hashlib.sha256(json.dumps(assinavel, ensure_ascii=False, sort_keys=True,
        default=serializar).encode()).hexdigest()[:12]
    return plano


def relatorio_markdown(plano: dict[str, Any]) -> str:
    fontes, resumo = plano["fontes"], plano["resumo"]
    pendencias = "\n".join(f"- {item}" for item in plano["pendencias"]) or "- nenhuma"
    return f"""# Conciliação privada de NF, GTA, banco e negócios

Plano `{plano['plano_id']}`, gerado em {plano['gerado_em']}, em modo somente
leitura. Nenhuma escrita foi executada e nenhuma tabela operacional foi alterada.

## Fontes e cortes

- Agronotas: {fontes['agronotas']['notas']} notas, {fontes['agronotas']['com_gta']} com GTA, último documento em {fontes['agronotas']['data_final']}, consulta até {fontes['agronotas'].get('consultado_ate') or fontes['agronotas']['data_final']};
- IMA: {fontes['ima']['movimentos']} movimentações, período até {fontes['ima']['periodo_final']};
- banco: {fontes['banco']['transacoes']} lançamentos, corte {fontes['banco']['data_final']};
- negócios de referência: {fontes['negocios']['registros']} linhas, {fontes['negocios']['codigos_unicos']} agrupamentos, {fontes['negocios']['codigos_operacionais']} códigos operacionais e {fontes['negocios']['contextos_agregadores']} contextos agregadores;
- data de referência solicitada: {plano['data_referencia']}.

## Resultado do cruzamento

- NF × GTA: {json.dumps(resumo['vinculos_nf_gta'], ensure_ascii=False)};
- NF × banco: {json.dumps(resumo['candidatos_banco'], ensure_ascii=False)};
- NF × negócio: {json.dumps(resumo['candidatos_negocio'], ensure_ascii=False)};
- duplicidades documentais ignoradas: {fontes['agronotas']['duplicados_ignorados']}.

## Regras usadas

- GTA igual entre a observação/coluna da NF e o IMA é vínculo documental forte;
- valor bancário igual e único é somente candidato provável, nunca confirmação;
- negócio por GTA ou NF igual é forte, mas permanece não confirmado;
- correspondência apenas por valor e data é provável;
- múltiplas correspondências ficam ambíguas e não são escolhidas;
- documento fora do período da ficha IMA fica preservado como histórico fora
  da cobertura, não como pendência operacional;
- ausência de dado permanece pendência, sem preenchimento por inferência.

## Pendências

{pendencias}

Este plano não autoriza criar ou alterar compra, venda, GTA, pagamento, negócio,
rascunho ou evento. Os candidatos precisam ser conferidos antes de qualquer ação.
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agronotas", required=True, type=Path, action="append",
                        help="pode ser repetido para combinar histórico e atualização")
    parser.add_argument("--aba-agronotas")
    parser.add_argument("--agronotas-consultado-ate", type=date.fromisoformat,
                        help="data final confirmada pela consulta, mesmo sem documento no dia")
    parser.add_argument("--ima-pdf", required=True, type=Path)
    parser.add_argument("--ofx", required=True, type=Path, action="append",
                        help="pode ser repetido para combinar contas ou períodos")
    parser.add_argument("--negocios", type=Path)
    parser.add_argument("--aba-negocios", action="append")
    parser.add_argument("--data-referencia", required=True, type=date.fromisoformat)
    parser.add_argument("--saida-json", required=True, type=Path)
    parser.add_argument("--saida-md", required=True, type=Path)
    args = parser.parse_args()
    temporario = args.saida_json.with_suffix(".ima.txt.tmp")
    try:
        texto_ima = extrair_texto_pdf(args.ima_pdf, temporario)
    finally:
        temporario.unlink(missing_ok=True)
    fonte_agronotas = combinar_agronotas([
            ler_agronotas(caminho, args.aba_agronotas) for caminho in args.agronotas
        ])
    if args.agronotas_consultado_ate:
        fonte_agronotas["consultado_ate"] = args.agronotas_consultado_ate.isoformat()
    plano = gerar_plano(
        fonte_agronotas,
        ler_ficha(texto_ima, sha256_arquivo(args.ima_pdf)),
        combinar_extratos([ler_ofx_detalhado(caminho) for caminho in args.ofx]),
        ler_negocios(args.negocios, args.aba_negocios) if args.negocios else None,
        args.data_referencia,
    )
    args.saida_json.parent.mkdir(parents=True, exist_ok=True)
    args.saida_md.parent.mkdir(parents=True, exist_ok=True)
    args.saida_json.write_text(json.dumps(plano, ensure_ascii=False, indent=2,
        default=serializar) + "\n", encoding="utf-8")
    args.saida_md.write_text(relatorio_markdown(plano), encoding="utf-8")
    print(json.dumps({"plano_id": plano["plano_id"], "modo": plano["modo"],
        "escritas_executadas": plano["escritas_executadas"],
        "tabelas_operacionais_alteradas": plano["tabelas_operacionais_alteradas"],
        "resumo": plano["resumo"], "pendencias": plano["pendencias"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
