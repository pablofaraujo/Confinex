"""Contrato puro de identidade OFX. Rótulo de conta nunca comprova identidade."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

CAMPOS_IDENTIDADE = ("BANKID", "BRANCHID", "ACCTID", "ACCTTYPE", "CURDEF")


def assinatura(valor: Any) -> str:
    return hashlib.sha256(json.dumps(
        valor, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()


def texto_id(valor: Any) -> str | None:
    # Não converter números, caixa, pontuação ou remover zeros significativos.
    return valor if isinstance(valor, str) and valor.strip() == valor and valor else None


def identidade_completa(valor: Any) -> tuple[str, ...] | None:
    if not isinstance(valor, dict):
        return None
    partes = tuple(texto_id(valor.get(campo)) for campo in CAMPOS_IDENTIDADE)
    return partes if all(partes) else None


def metadados_ofx(item: dict[str, Any]) -> dict[str, Any]:
    origem = item.get("dados_origem")
    ofx = origem.get("ofx") if isinstance(origem, dict) else None
    return ofx if isinstance(ofx, dict) and ofx.get("versao") == 1 else {}


def identidade_registro(item: dict[str, Any]) -> tuple[str, ...] | None:
    ofx = metadados_ofx(item)
    identidade = ofx.get("identidade")
    chave = identidade_completa(identidade)
    if chave and ofx.get("identidade_sha256") == assinatura(identidade):
        return chave
    return None


def chave_logica(item: dict[str, Any], campo_fitid: str = "fitid") -> tuple | None:
    identidade, fitid = identidade_registro(item), texto_id(item.get(campo_fitid))
    return (identidade, fitid) if identidade and fitid else None


def decimal_assinado(valor: Any) -> Decimal | None:
    if isinstance(valor, bool) or valor is None:
        return None
    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return numero if numero.is_finite() else None


def dados_basicos(item: dict[str, Any]) -> tuple | None:
    valor = decimal_assinado(item.get("valor"))
    dia = item.get("data")
    try:
        if not isinstance(dia, str) or date.fromisoformat(dia).isoformat() != dia:
            return None
    except ValueError:
        return None
    return (dia, valor) if valor is not None else None


def comparar_conteudo(a: dict[str, Any], b: dict[str, Any]) -> str:
    """Igual exige prova integral; mesmo dia/valor não apaga diferença de horário."""
    ba, bb = dados_basicos(a), dados_basicos(b)
    if ba is None or bb is None:
        return "incompleto"
    if ba != bb:
        return "divergente"
    for campo in ("tipo", "descricao", "memo"):
        if campo not in a or campo not in b:
            return "incompleto"
        if a[campo] != b[campo]:
            return "divergente"
    oa, ob = metadados_ofx(a), metadados_ofx(b)
    for campo in ("data_ofx_original", "stmttrn_sha256"):
        if not texto_id(oa.get(campo)) or not texto_id(ob.get(campo)):
            return "incompleto"
        if campo == "stmttrn_sha256" and not all(
            re.fullmatch(r"[0-9a-f]{64}", o[campo]) for o in (oa, ob)
        ):
            return "incompleto"
        if oa[campo] != ob[campo]:
            return "divergente"
    return "igual"


def avaliar_presenca(item: dict[str, Any], existentes: list[dict[str, Any]]) -> str:
    """Consulta em memória. Legado sem prova não vira ausente nem já conciliado."""
    if dados_basicos(item) is None:
        return "conteudo_pendente"
    vinculo = texto_id(item.get("transacao_banco_id"))
    if vinculo:
        ligados = [e for e in existentes if e.get("id") == vinculo]
        if len(ligados) != 1:
            return "vinculo_nao_comprovado"
        outro = ligados[0]
        ba, bb = dados_basicos(item), dados_basicos(outro)
        if ba is None or bb is None:
            return "vinculo_nao_comprovado"
        if ba != bb or (outro.get("id_externo") and outro["id_externo"] != item.get("fitid")):
            return "conflito_de_conteudo"
        ia, ib = identidade_registro(item), identidade_registro(outro)
        if ia and ib and ia != ib:
            return "conflito_de_identidade"
        if metadados_ofx(item) and metadados_ofx(outro) and comparar_conteudo(item, outro) != "igual":
            return "conflito_de_conteudo"
        return "presente_por_vinculo"
    chave = chave_logica(item)
    if chave is None:
        return "identidade_pendente"
    candidatos = [e for e in existentes if e.get("id_externo") == item["fitid"]]
    if any(chave_logica(e, "id_externo") is None for e in candidatos):
        return "identidade_pendente"
    iguais = [e for e in candidatos if chave_logica(e, "id_externo") == chave]
    if len(iguais) > 1:
        return "referencia_ambigua"
    if not iguais:
        return "ausente_na_amostra"
    comparacao = comparar_conteudo(item, iguais[0])
    return {
        "igual": "presente_por_identidade", "divergente": "conflito_de_conteudo",
        "incompleto": "conteudo_pendente",
    }[comparacao]
