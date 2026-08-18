#!/usr/bin/env python3
"""Contratos puros para interpretar NF-e pecuária recebida do AgroNota.

O módulo não acessa AgroNota, Supabase ou arquivos privados. Ele transforma um
XML já obtido em metadados sanitizáveis usados pelo pipeline do Juan.
"""

from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET
from typing import Any


GADO_RE = re.compile(
    r"\b(?:BOVIN\w*|BOI(?:S)?|NOVILH\w*|GARROT\w*|VACA(?:S)?|BEZERR\w*|ANIMAIS?\s+VIVOS?)\b",
    re.IGNORECASE,
)
GTA_RE = re.compile(
    r"\bGTAS?\b[^0-9]{0,30}"
    r"([0-9][0-9.\-/\s]{4,30}[0-9])",
    re.IGNORECASE,
)


def _nome_tag(elemento: ET.Element) -> str:
    return elemento.tag.split("}")[-1]


def _textos(raiz: ET.Element, nome: str) -> list[str]:
    return [
        (elemento.text or "").strip()
        for elemento in raiz.iter()
        if _nome_tag(elemento) == nome and (elemento.text or "").strip()
    ]


def _texto_no_bloco(raiz: ET.Element, bloco: str, campo: str) -> str | None:
    """Lê um campo dentro de emit/dest sem misturar as duas partes da NF-e."""
    for elemento in raiz.iter():
        if _nome_tag(elemento) != bloco:
            continue
        for filho in elemento.iter():
            if _nome_tag(filho) == campo and (filho.text or "").strip():
                return (filho.text or "").strip()
    return None


def _normalizar_texto(valor: str) -> str:
    return unicodedata.normalize("NFKD", valor).encode("ascii", "ignore").decode().upper()


def normalizar_gta(valor: str) -> str | None:
    """Conserva somente dígitos e rejeita datas/fragmentos curtos."""
    if re.fullmatch(r"\s*\d{1,2}/\d{1,2}/\d{4}\s*", valor or ""):
        return None
    digitos = re.sub(r"\D", "", valor or "")
    if len(digitos) < 6 or len(digitos) > 20:
        return None
    return digitos


def extrair_gtas_texto(texto: str) -> list[str]:
    encontrados: list[str] = []
    for bruto in GTA_RE.findall(texto or ""):
        gta = normalizar_gta(bruto)
        if gta and gta not in encontrados:
            encontrados.append(gta)
    return encontrados


def analisar_xml_nfe(xml: bytes | str) -> dict[str, Any]:
    """Extrai apenas sinais necessários à conciliação, sem fazer inferências."""
    raiz = ET.fromstring(xml)
    campos_adicionais = ("infCpl", "infAdFisco", "xPed", "infAdProd")
    textos_adicionais = [texto for campo in campos_adicionais for texto in _textos(raiz, campo)]
    descricoes = _textos(raiz, "xProd")
    naturezas = _textos(raiz, "natOp")
    finalidades = _textos(raiz, "finNFe")
    referencias_nfe = [item for item in _textos(raiz, "refNFe") if re.fullmatch(r"\d{44}", item)]
    gtas: list[str] = []
    for texto in textos_adicionais:
        for gta in extrair_gtas_texto(texto):
            if gta not in gtas:
                gtas.append(gta)
    natureza_normalizada = " | ".join(_normalizar_texto(item) for item in naturezas)
    return {
        "gtas": gtas,
        "gta": gtas[0] if len(gtas) == 1 else None,
        "gta_ambigua": len(gtas) > 1,
        # A presença explícita de GTA é evidência documental suficiente mesmo
        # quando a descrição comercial não usa palavras como "bovino".
        "relacionada_a_gado": bool(gtas) or bool(GADO_RE.search(" | ".join(descricoes + textos_adicionais))),
        "tem_informacao_adicional": bool(textos_adicionais),
        "eh_nota_venda": bool(re.search(r"\bVENDA\b", natureza_normalizada)),
        "eh_complemento": "2" in finalidades or "COMPLEMENT" in natureza_normalizada or bool(referencias_nfe),
        "referencias_nfe": referencias_nfe,
        "natureza_operacao": " | ".join(naturezas) or None,
        "descricoes_produtos": descricoes,
        "emitente_nome": _texto_no_bloco(raiz, "emit", "xNome"),
        "destinatario_nome": _texto_no_bloco(raiz, "dest", "xNome"),
    }


def campos_pendentes_documento(
    *, tem_gta: bool, operacao_vinculada: bool
) -> list[str]:
    """Pendências humanas de indexação da NF ao negócio correto."""
    campos: list[str] = []
    if not tem_gta:
        campos.append("número da GTA")
    if not operacao_vinculada:
        campos.append("relação com o negócio")
    campos.append("extrato bancário ou comprovante")
    return campos


def documento_deve_ser_indexado(
    analise: dict[str, Any], *, fonte: str | None = None, eh_venda_gado_pablo: bool = False
) -> bool:
    """Inclui documento pecuário e toda NF de venda emitida pelo negócio."""
    emitida = str(fonte or "").endswith("_emitida")
    return bool(
        analise.get("relacionada_a_gado")
        or eh_venda_gado_pablo
        or (emitida and analise.get("eh_nota_venda"))
    )
