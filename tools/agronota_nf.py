#!/usr/bin/env python3
"""Contratos puros para interpretar NF-e pecuária recebida do AgroNota.

O módulo não acessa AgroNota, Supabase ou arquivos privados. Ele transforma um
XML já obtido em metadados sanitizáveis usados pelo pipeline do Juan.
"""

from __future__ import annotations

import re
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
    gtas: list[str] = []
    for texto in textos_adicionais:
        for gta in extrair_gtas_texto(texto):
            if gta not in gtas:
                gtas.append(gta)
    return {
        "gtas": gtas,
        "gta": gtas[0] if len(gtas) == 1 else None,
        "gta_ambigua": len(gtas) > 1,
        # A presença explícita de GTA é evidência documental suficiente mesmo
        # quando a descrição comercial não usa palavras como "bovino".
        "relacionada_a_gado": bool(gtas) or bool(GADO_RE.search(" | ".join(descricoes + textos_adicionais))),
        "tem_informacao_adicional": bool(textos_adicionais),
    }


def campos_pendentes_documento(*, tem_gta: bool, operacao_vinculada: bool) -> list[str]:
    """Pendências humanas para uma NF de compra; banco sempre chega por anexo."""
    campos: list[str] = []
    if not tem_gta:
        campos.append("número da GTA")
    if not operacao_vinculada:
        campos.append("negócio correspondente")
    campos.append("extrato bancário ou comprovante")
    return campos
