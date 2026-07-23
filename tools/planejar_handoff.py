#!/usr/bin/env python3
"""Classifica handoffs abertos sem copiar ou alterar seu conteúdo."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from typing import Any

from confinex_client import ConfinexClient, ConfinexError

PADROES = {
    "dado_operacional": re.compile(
        r"\b(compra|venda|abate|pesagem|peso|cabeças?|lote|pagamento|"
        r"recebimento|valor|arroba|kg|data)\b|R\$",
        re.I,
    ),
    "memoria_permanente": re.compile(
        r"\b(regra|preferência|sempre|nunca|decisão|exceção|padrão recorrente)\b",
        re.I,
    ),
    "evento": re.compile(
        r"\b(aprovad[oa]|rejeitad[oa]|confirmad[oa]|executad[oa]|"
        r"cancelad[oa]|alterad[oa]|erro)\b",
        re.I,
    ),
    "continuidade_temporaria": re.compile(
        r"\b(próximo|pendência|continuar|verificar|aguardando|retomar|passo)\b",
        re.I,
    ),
}


def fragmentos(row: dict[str, Any]) -> list[str]:
    values = [
        row.get("titulo"),
        row.get("data"),
        row.get("tarefa"),
        row.get("ultimo_passo"),
        row.get("proximos_passos"),
        *(row.get("pendencias") or []),
    ]
    parts = []
    for value in values:
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        for part in re.split(r"[\n\r;]+", str(value or "")):
            cleaned = part.strip(" -*\t")
            if cleaned:
                parts.append(cleaned)
    return parts


def classify(text: str) -> list[str]:
    matches = [kind for kind, pattern in PADROES.items() if pattern.search(text)]
    return matches or ["revisao_humana"]


def plan_row(row: dict[str, Any]) -> dict[str, Any]:
    buckets: dict[str, int] = {kind: 0 for kind in (*PADROES, "revisao_humana")}
    for part in fragmentos(row):
        for kind in classify(part):
            buckets[kind] += 1
    digest = hashlib.sha256(
        json.dumps(row, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()[:12]
    return {
        "handoff_id": row.get("id"),
        "assinatura_fonte": digest,
        "classificacao": buckets,
        "destinos": {
            "dado_operacional": "rascunho, evento ou tabela operacional após confirmação",
            "memoria_permanente": "memorias_agentes após revisão e confirmação",
            "evento": "eventos, ligado à entidade e mensagem de origem",
            "continuidade_temporaria": "permanecer no handoff até a conclusão",
            "revisao_humana": "não mover automaticamente",
        },
        "encerramento_permitido": False,
        "escritas_realizadas": 0,
    }


def build_plan(client: Any) -> dict[str, Any]:
    rows = client.select(
        "contexto_handoff",
        select=(
            "id,status,titulo,data,tarefa,ultimo_passo,proximos_passos,"
            "pendencias,agente_origem,agente_destino,fonte"
        ),
        order="created_at.asc",
        limit="500",
    )
    open_rows = [
        row
        for row in rows
        if str(row.get("status") or "").casefold()
        not in {"concluido", "concluído", "encerrado", "fechado", "cancelado"}
    ]
    return {
        "modo": "dry-run",
        "handoffs_abertos": len(open_rows),
        "planos": [plan_row(row) for row in open_rows],
        "conteudo_exposto": False,
        "escritas_realizadas": 0,
    }


def main() -> int:
    argparse.ArgumentParser(
        description="Classifica handoffs abertos sem alterar o Supabase"
    ).parse_args()
    try:
        print(json.dumps(build_plan(ConfinexClient()), ensure_ascii=False, indent=2))
        return 0
    except ConfinexError as exc:
        print(json.dumps({"erro": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
