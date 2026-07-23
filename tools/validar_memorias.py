#!/usr/bin/env python3
"""Audita o uso de memorias_agentes sem alterar ou expor seu conteúdo."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from typing import Any

from confinex_client import ConfinexClient, ConfinexError

TIPOS_REUTILIZAVEIS = {"decisao", "preferencia", "regra", "excecao", "aprendizado"}
STATUS_VALIDOS = {"pendente", "confirmada", "rejeitada", "substituida"}
OPERACAO_RE = re.compile(
    r"\b(compra|venda|abate|pesagem|lote|cabeças?|peso|pagamento|"
    r"recebimento|valor|arrobas?)\b",
    re.I,
)
NUMERO_RE = re.compile(r"(?:R\$|\b\d+(?:[.,]\d+)?\s*(?:kg|@|cabeças?)\b)", re.I)


def normalizar(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").strip().casefold())
    return "".join(char for char in text if not unicodedata.combining(char))


def problemas(row: dict[str, Any]) -> list[str]:
    issues = []
    if normalizar(row.get("tipo")) not in TIPOS_REUTILIZAVEIS:
        issues.append("tipo não representa conhecimento reutilizável")
    for field, label in (
        ("escopo", "escopo"),
        ("agente_origem", "agente de origem"),
        ("assunto", "assunto"),
        ("importancia", "importância"),
        ("validade_inicio", "início da validade"),
        ("fonte_tipo", "tipo da fonte"),
        ("status_confirmacao", "estado da confirmação"),
    ):
        if row.get(field) in (None, ""):
            issues.append(f"falta {label}")
    if normalizar(row.get("status_confirmacao")) not in STATUS_VALIDOS:
        issues.append("estado de confirmação inválido")
    content = " ".join(
        str(row.get(field) or "") for field in ("assunto", "texto", "dados")
    )
    if OPERACAO_RE.search(content) and NUMERO_RE.search(content):
        issues.append("possível dado operacional dentro da memória")
    return issues


def item_report(row: dict[str, Any]) -> dict[str, Any]:
    digest = hashlib.sha256(
        json.dumps(row, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()[:12]
    return {
        "memoria_id": row.get("id"),
        "assinatura_fonte": digest,
        "problemas": problemas(row),
        "conteudo_exposto": False,
    }


def build_report(client: Any) -> dict[str, Any]:
    rows = client.select(
        "memorias_agentes",
        select=(
            "id,tipo,escopo,agente_origem,assunto,importancia,validade_inicio,"
            "validade_fim,fonte_tipo,fonte_ref,status_confirmacao,texto,dados,"
            "origem_canal,origem_conversa_id,origem_mensagem_id"
        ),
        order="criado_em.asc",
        limit="5000",
    )
    reports = [item_report(row) for row in rows]
    return {
        "modo": "somente_leitura",
        "total": len(rows),
        "conformes": sum(not item["problemas"] for item in reports),
        "para_revisao": sum(bool(item["problemas"]) for item in reports),
        "itens": [item for item in reports if item["problemas"]],
        "escritas_realizadas": 0,
    }


def main() -> int:
    argparse.ArgumentParser(
        description="Valida o contrato de memórias sem alterar o Supabase"
    ).parse_args()
    try:
        print(json.dumps(build_report(ConfinexClient()), ensure_ascii=False, indent=2))
        return 0
    except ConfinexError as exc:
        print(json.dumps({"erro": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
