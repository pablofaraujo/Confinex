#!/usr/bin/env python3
"""Transforma achados de auditoria do Telegram em rascunhos revisaveis.

O modo padrao apenas apresenta o plano. A ferramenta escreve exclusivamente em
``operation_drafts`` e nunca cria registros em ``compras``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from confinex_client import ConfinexClient, ConfinexError


CONTEXT_NAMES = {
    "boi_balanca": "Boi Balança",
    "boi balanca": "Boi Balança",
    "boi balança": "Boi Balança",
    "-5593693732": "Boi Balança",
    "telegram:-5593693732": "Boi Balança",
    "confinamento": "Confinamento",
    "-4865454316": "Confinamento",
    "telegram:-4865454316": "Confinamento",
    "desconhecido": "Contexto não identificado",
    "sem_contexto": "Contexto não identificado",
    "": "Contexto não identificado",
}
SAFE_BUSINESS_FIELDS = {
    "operacao_id", "operacao_codigo", "negocio_id", "negocio_codigo",
    "fornecedor", "vendedor", "nome_fornecedor", "data", "data_compra",
    "quantidade", "cabecas", "valor_total", "valor_bruto", "peso_total_kg",
    "preco_arroba", "prazo_dias", "vencimento", "resumo", "situacao",
    "evidencia", "acao_recomendada",
}
REQUIRED_PURCHASE_FIELDS = {
    "negocio": ("operacao_id", "negocio_id", "operacao_codigo", "negocio_codigo"),
    "data": ("data_compra", "data"),
    "cabeças": ("quantidade", "cabecas"),
    "valor total": ("valor_total", "valor_bruto"),
}
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
NAMESPACE = uuid.UUID("7a25e890-83bb-4f31-a5ef-10b85f154919")


def canonical_context(value: Any) -> str:
    raw = str(value or "").strip()
    mapped = CONTEXT_NAMES.get(raw.lower())
    if mapped:
        return mapped
    if re.fullmatch(r"(?:telegram:)?-?\d{6,}", raw):
        return "Contexto não identificado"
    return raw or "Contexto não identificado"


def source_key(candidate: dict[str, Any]) -> str:
    explicit = candidate.get("candidate_id") or candidate.get("origem_mensagem_id")
    if explicit:
        return f"mensagem:{explicit}"
    filename = str(candidate.get("file") or candidate.get("arquivo") or "")
    match = UUID_RE.search(filename)
    if match:
        return f"sessao:{match.group(0).lower()}"
    return "arquivo:" + hashlib.sha256(Path(filename).name.encode()).hexdigest()


def stable_fingerprint(context: str, source: str) -> str:
    normalized = f"compra_telegram|{context.casefold()}|{source.casefold()}"
    return hashlib.sha256(normalized.encode()).hexdigest()


def deterministic_id(fingerprint: str) -> str:
    return str(uuid.uuid5(NAMESPACE, fingerprint))


def first_present(data: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    for key in aliases:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return None


def read_candidates(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfinexError(f"nao foi possivel ler a auditoria: {exc}") from exc
    if isinstance(payload, dict):
        payload = payload.get("candidates") or payload.get("candidatos")
    if not isinstance(payload, list):
        raise ConfinexError("a auditoria deve conter uma lista de candidatos")
    return [row for row in payload if isinstance(row, dict)]


def cluster_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clusters: dict[tuple[str, str], dict[str, Any]] = {}
    for candidate in candidates:
        context = canonical_context(candidate.get("contexto") or candidate.get("grupo_telegram"))
        source = source_key(candidate)
        key = (context, source)
        cluster = clusters.setdefault(key, {
            "contexto": context,
            "source": source,
            "ocorrencias": 0,
            "linhas": set(),
            "dados": {},
            "confirmacoes": set(),
            "origem_mensagem_id": candidate.get("origem_mensagem_id"),
        })
        cluster["ocorrencias"] += 1
        if candidate.get("line") is not None:
            cluster["linhas"].add(str(candidate["line"]))
        source_data = candidate.get("dados_extraidos") or candidate.get("dados") or {}
        if isinstance(source_data, dict):
            for field in SAFE_BUSINESS_FIELDS:
                if field not in cluster["dados"] and source_data.get(field) not in (None, ""):
                    cluster["dados"][field] = source_data[field]
        confirmations = candidate.get("confirmacoes") or []
        if isinstance(confirmations, list):
            cluster["confirmacoes"].update(str(item) for item in confirmations)
    return sorted(clusters.values(), key=lambda row: (row["contexto"], row["source"]))


def build_draft(cluster: dict[str, Any]) -> dict[str, Any]:
    context = cluster["contexto"]
    fingerprint = stable_fingerprint(context, cluster["source"])
    data = dict(cluster["dados"])
    confirmations = set(cluster["confirmacoes"])
    pending = []
    inferred = []
    for label, aliases in REQUIRED_PURCHASE_FIELDS.items():
        present = first_present(data, aliases)
        confirmed = bool(confirmations.intersection(aliases))
        if present is None or not confirmed:
            pending.append(f"confirmar {label}")
        if present is not None and not confirmed:
            inferred.append(label)
    if context == "Contexto não identificado":
        pending.insert(0, "confirmar grupo/contexto")
    if not cluster.get("origem_mensagem_id"):
        pending.append("vincular mensagem original do Telegram")
    pending.append("confirmar se o indício representa uma compra real")
    pending = list(dict.fromkeys(pending))
    data.update({
        "contexto_operacional": context,
        "grupo_telegram": context,
        "origem_canal": "telegram",
        "origem_conversa_id": context,
        "origem_mensagem_id": cluster.get("origem_mensagem_id") or "",
        "agente": "juan",
        "status_confirmacao": "pendente",
        "situacao": f"Auditoria encontrou {cluster['ocorrencias']} indício(s) de compra nesta conversa.",
        "evidencia": "Conteúdo bruto preservado apenas na auditoria do VPS; confirmar no Telegram antes de aprovar.",
    })
    return {
        "id": deterministic_id(fingerprint),
        "agente": "juan",
        "origem_canal": "telegram",
        "origem_conversa_id": context,
        "origem_mensagem_id": cluster.get("origem_mensagem_id"),
        "tipo_operacao": "triagem_compra_telegram",
        "codigo_sugerido": f"AUD-COMPRA-{fingerprint[:8].upper()}",
        "entidade_final_tipo": "compras",
        "dados_extraidos": data,
        "campos_pendentes": pending,
        "inferencias": {
            "estado": "pendente",
            "confirmacao_suficiente": False,
            "campos_inferidos": inferred,
            "audit_fingerprint": fingerprint,
            "ocorrencias_agrupadas": cluster["ocorrencias"],
        },
        "confianca": 0.2,
        "status": "rascunho",
    }


def load_existing(client: Any) -> list[dict[str, Any]]:
    return client.select(
        "operation_drafts",
        select="id,origem_conversa_id,origem_mensagem_id,inferencias",
        order="criado_em.desc",
        limit="1000",
    )


def deduplicate(drafts: list[dict[str, Any]], existing: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    existing_ids = {str(row.get("id")) for row in existing}
    existing_fingerprints = {
        str((row.get("inferencias") or {}).get("audit_fingerprint"))
        for row in existing if isinstance(row.get("inferencias"), dict)
    }
    existing_messages = {
        (canonical_context(row.get("origem_conversa_id")), str(row.get("origem_mensagem_id")))
        for row in existing if row.get("origem_mensagem_id")
    }
    planned = []
    reused = []
    seen = set()
    for draft in drafts:
        fingerprint = draft["inferencias"]["audit_fingerprint"]
        message_key = (canonical_context(draft.get("origem_conversa_id")), str(draft.get("origem_mensagem_id")))
        duplicate = (
            draft["id"] in existing_ids
            or fingerprint in existing_fingerprints
            or (draft.get("origem_mensagem_id") and message_key in existing_messages)
            or fingerprint in seen
        )
        if duplicate:
            reused.append(draft["id"])
        else:
            planned.append(draft)
            seen.add(fingerprint)
    return planned, reused


def build_client() -> ConfinexClient:
    url = os.getenv("SUPABASE_URL") or os.getenv("CONFINEX_SUPABASE_URL") or os.getenv("CONFINEX_DB_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("CONFINEX_DB_KEY") or os.getenv("SUPABASE_ANON_KEY")
    try:
        return ConfinexClient(url=url, key=key)
    except TypeError:
        return ConfinexClient(env={"CONFINEX_DB_URL": url or "", "CONFINEX_DB_KEY": key or ""})


def execute_backlog(client: Any, drafts: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if limit <= 0:
        raise ConfinexError("use --limite com um numero positivo para executar")
    created = []
    for draft in drafts[:limit]:
        inserted = client.insert("operation_drafts", draft)
        created.append({"id": inserted.get("id"), "contexto": draft["dados_extraidos"]["grupo_telegram"]})
    return created


def summary(drafts: list[dict[str, Any]], reused: list[str], created: list[dict[str, Any]], executed: bool = False) -> dict[str, Any]:
    by_context = Counter(draft["dados_extraidos"]["grupo_telegram"] for draft in drafts)
    return {
        "modo": "executado" if executed else "simulacao",
        "rascunhos_planejados": len(drafts),
        "duplicados_ignorados": len(reused),
        "rascunhos_criados": len(created),
        "por_contexto": dict(sorted(by_context.items())),
        "criados": created,
        "destino_exclusivo": "operation_drafts",
        "promocao_automatica": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Cria backlog revisavel de candidatos a compra do Telegram")
    parser.add_argument("auditoria", type=Path)
    parser.add_argument("--executar", action="store_true", help="insere somente rascunhos em operation_drafts")
    parser.add_argument("--limite", type=int, default=0, help="maximo de rascunhos a criar")
    parser.add_argument("--consultar-banco", action="store_true", help="considera rascunhos existentes na simulacao")
    args = parser.parse_args()
    try:
        candidates = read_candidates(args.auditoria)
        drafts = [build_draft(cluster) for cluster in cluster_candidates(candidates)]
        client = build_client() if args.executar or args.consultar_banco else None
        existing = load_existing(client) if client else []
        planned, reused = deduplicate(drafts, existing)
        created = execute_backlog(client, planned, args.limite) if args.executar else []
        print(json.dumps(summary(planned, reused, created, executed=args.executar), ensure_ascii=False, indent=2))
        return 0
    except (ConfinexError, OSError) as exc:
        print(json.dumps({"erro": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
