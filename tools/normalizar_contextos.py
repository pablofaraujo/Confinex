#!/usr/bin/env python3
"""Planeja e, sob confirmação forte, normaliza contextos no Supabase.

O modo padrão é estritamente somente leitura. O arquivo de mapa deve ficar em
``docs/privado`` e não deve ser versionado.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from confinex_client import ConfinexClient, ConfinexError
from contexto_canonico import montar_contexto, origem_conversa_tecnica

TABLES = {
    "operation_drafts": {
        "canal": "origem_canal", "conversa": "origem_conversa_id",
        "mensagem": "origem_mensagem_id", "agente": "agente",
    },
    "pending_actions": {
        "canal": "canal", "conversa": "conversa_id",
        "mensagem": "mensagem_id", "agente": "agente",
    },
    "eventos": {
        "canal": "origem_canal", "conversa": "origem_conversa_id",
        "mensagem": "origem_mensagem_id", "agente": "agente",
    },
    "memorias_agentes": {
        "canal": "origem_canal", "conversa": "origem_conversa_id",
        "mensagem": "origem_mensagem_id", "agente": "agente_origem",
    },
}
SELECTS = {
    table: "id," + ",".join(dict.fromkeys(spec.values()))
    for table, spec in TABLES.items()
}
CONTEXT_COLUMNS = (
    "contexto_canonico", "contexto_nome", "origem_canal",
    "origem_conversa_id", "origem_mensagem_id", "agente", "escopo",
)
CONFIRM_PREFIX = "NORMALIZAR CONTEXTOS"


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfinexError(f"não foi possível ler {path}: {exc}") from exc


def load_map(path: Path) -> list[dict[str, Any]]:
    payload = load_json(path)
    rows = payload.get("contextos") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ConfinexError("o mapa deve conter uma lista 'contextos'")
    for row in rows:
        if not isinstance(row, dict) or not row.get("contexto_nome"):
            raise ConfinexError("cada contexto do mapa precisa de contexto_nome")
    return rows


def aliases(row: dict[str, Any]) -> set[str]:
    values = {
        row.get("origem_conversa_id"), row.get("contexto_canonico"),
        row.get("contexto_nome"), *(row.get("aliases") or []),
    }
    return {str(value).strip().casefold() for value in values if value not in (None, "")}


def resolve_context(value: Any, mapping: list[dict[str, Any]]) -> dict[str, Any] | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    folded = raw.casefold()
    technical = origem_conversa_tecnica(raw)
    matches = [
        row for row in mapping
        if folded in aliases(row)
        or (technical and technical == origem_conversa_tecnica(row.get("origem_conversa_id")))
    ]
    return matches[0] if len(matches) == 1 else None


def normalized_payload(row: dict[str, Any], spec: dict[str, str], mapped: dict[str, Any]) -> dict[str, Any]:
    context = montar_contexto(
        contexto_nome=mapped["contexto_nome"],
        origem_canal=mapped.get("origem_canal") or row.get(spec["canal"]) or "telegram",
        origem_conversa_id=mapped.get("origem_conversa_id") or row.get(spec["conversa"]),
        origem_mensagem_id=row.get(spec["mensagem"]),
        agente=row.get(spec["agente"]) or mapped.get("agente") or "juan",
        escopo=mapped.get("escopo") or "grupo",
    )
    payload = dict(context)
    if spec["agente"] == "agente_origem":
        payload["agente_origem"] = payload["agente"]
    if spec["canal"] == "canal":
        payload.update({
            "canal": payload["origem_canal"],
            "conversa_id": payload["origem_conversa_id"],
            "mensagem_id": payload["origem_mensagem_id"],
        })
    return payload


def select_rows(client: Any, table: str) -> list[dict[str, Any]]:
    expanded = SELECTS[table] + "," + ",".join(
        column for column in CONTEXT_COLUMNS if column not in SELECTS[table].split(",")
    )
    try:
        return client.select(table, select=expanded, order="id.asc", limit="5000")
    except ConfinexError:
        # Permite auditar a base antes de aplicar a migração aditiva.
        return client.select(table, select=SELECTS[table], order="id.asc", limit="5000")


def build_plan(client: Any, mapping: list[dict[str, Any]]) -> dict[str, Any]:
    changes, skipped = [], []
    for table, spec in TABLES.items():
        for row in select_rows(client, table):
            raw = row.get(spec["conversa"])
            mapped = resolve_context(raw, mapping)
            if not mapped:
                if raw:
                    skipped.append({"tabela": table, "id": row.get("id"), "motivo": "contexto ambíguo ou não mapeado"})
                continue
            payload = normalized_payload(row, spec, mapped)
            if all(row.get(key) == value for key, value in payload.items()):
                continue
            changes.append({
                "tabela": table,
                "id": row["id"],
                "antes": str(raw),
                "depois": payload,
            })
    contexts = []
    for row in mapping:
        context = montar_contexto(
            contexto_nome=row["contexto_nome"],
            origem_canal=row.get("origem_canal") or "telegram",
            origem_conversa_id=row.get("origem_conversa_id"),
            agente=row.get("agente") or "juan",
            escopo=row.get("escopo"),
        )
        contexts.append({
            key: context[key]
            for key in (
                "contexto_canonico", "contexto_nome", "origem_canal",
                "origem_conversa_id", "escopo",
            )
        } | {"aliases": row.get("aliases") or [], "ativo": True})
    digest_source = json.dumps(
        {"alteracoes": changes, "contextos": contexts},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    plan_id = hashlib.sha256(digest_source.encode()).hexdigest()[:12]
    handoffs = client.select(
        "contexto_handoff",
        select="id,status,estado,pendencias,proximos_passos",
        order="created_at.asc",
        limit="500",
    )
    open_handoffs = [
        {
            "id": row.get("id"),
            "acao": "separar fatos operacionais de regras duráveis e encerrar a passagem após conferência",
        }
        for row in handoffs
        if str(row.get("status") or row.get("estado") or "").casefold()
        not in {"concluido", "concluído", "encerrado", "fechado", "cancelado"}
    ]
    return {
        "modo": "dry-run",
        "plano_id": plan_id,
        "alteracoes": changes,
        "contextos": contexts,
        "ignorados": skipped,
        "total_alteracoes": len(changes),
        "total_ignorados": len(skipped),
        "handoffs_para_triagem": open_handoffs,
        "escritas_realizadas": 0,
    }


def execute_plan(client: Any, plan: dict[str, Any], confirmation: str) -> dict[str, Any]:
    expected = f"{CONFIRM_PREFIX} {plan['plano_id']}"
    if confirmation != expected:
        raise ConfinexError(f"confirmação inválida; use exatamente: {expected}")
    existing_contexts = {
        row["contexto_canonico"]
        for row in client.select("contextos_canais", select="contexto_canonico", limit="5000")
    }
    created_contexts = []
    for context in plan["contextos"]:
        if context["contexto_canonico"] not in existing_contexts:
            created = client.insert("contextos_canais", context)
            created_contexts.append(created.get("id"))
    updated = []
    for change in plan["alteracoes"]:
        rows = client.update(
            change["tabela"],
            {"id": f"eq.{change['id']}"},
            change["depois"],
        )
        if len(rows) != 1:
            raise ConfinexError(f"não foi possível normalizar {change['tabela']}/{change['id']}")
        updated.append({"tabela": change["tabela"], "id": change["id"]})
    return {
        **plan,
        "modo": "executado",
        "escritas_realizadas": len(updated) + len(created_contexts),
        "contextos_criados": created_contexts,
        "atualizados": updated,
    }


def candidate_draft(path: Path, mapping: list[dict[str, Any]]) -> dict[str, Any]:
    candidate = load_json(path)
    required = {"tipo_operacao", "origem_conversa_id", "origem_mensagem_id", "dados_extraidos"}
    missing = sorted(required - set(candidate))
    if missing:
        raise ConfinexError("candidato sem campos: " + ", ".join(missing))
    mapped = resolve_context(candidate["origem_conversa_id"], mapping)
    if not mapped:
        raise ConfinexError("contexto do candidato não está mapeado")
    context = normalized_payload(candidate, TABLES["operation_drafts"], mapped)
    data = dict(candidate["dados_extraidos"])
    data.update(context)
    return {
        **context,
        "tipo_operacao": candidate["tipo_operacao"],
        "entidade_final_tipo": candidate.get("entidade_final_tipo") or "vendas",
        "dados_extraidos": data,
        "campos_pendentes": candidate.get("campos_pendentes") or ["confirmar dados da venda/abate"],
        "inferencias": {
            **(candidate.get("inferencias") or {}),
            "estado": "pendente",
            "confirmacao_suficiente": False,
        },
        "confianca": candidate.get("confianca", 0.3),
        "status": "rascunho",
    }


def candidate_plan_id(draft: dict[str, Any]) -> str:
    source = json.dumps(draft, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(source.encode()).hexdigest()[:12]


def create_candidate_draft(client: Any, draft: dict[str, Any], confirmation: str) -> dict[str, Any]:
    plan_id = candidate_plan_id(draft)
    expected = f"CRIAR RASCUNHO {plan_id}"
    if confirmation != expected:
        raise ConfinexError(f"confirmação inválida; use exatamente: {expected}")
    if str(draft.get("origem_mensagem_id") or "").endswith("a-confirmar"):
        raise ConfinexError("vincule o ID exato da mensagem antes de criar o rascunho")
    existing = client.select(
        "operation_drafts",
        select="id",
        origem_conversa_id=f"eq.{draft['origem_conversa_id']}",
        origem_mensagem_id=f"eq.{draft['origem_mensagem_id']}",
        limit="2",
    )
    if existing:
        raise ConfinexError("já existe rascunho para essa conversa e mensagem")
    created = client.insert("operation_drafts", draft)
    event_payload = {
        key: draft.get(key)
        for key in (
            "contexto_canonico", "contexto_nome", "origem_canal",
            "origem_conversa_id", "origem_mensagem_id", "agente", "escopo",
        )
    }
    event = client.insert("eventos", {
        **event_payload,
        "tipo": "rascunho_criado_por_reconciliacao",
        "usuario": "pablo",
        "entidade_tipo": "operation_draft",
        "entidade_id": created.get("id"),
        "origem": "normalizacao_contextos",
        "status": "registrado",
        "dados": {
            "draft_id": created.get("id"),
            "promovido_para_operacional": False,
            "confirmacao_suficiente": False,
        },
        "observacao": "Lacuna histórica convertida em rascunho para revisão; nenhum lançamento operacional foi criado.",
    })
    return {
        "modo": "rascunho_criado",
        "rascunho_id": created.get("id"),
        "evento_id": event.get("id"),
        "tabelas_operacionais_alteradas": 0,
    }


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Normaliza contexto por grupo com dry-run obrigatório")
    p.add_argument("--mapa", type=Path, required=True, help="JSON privado com nomes, IDs e aliases")
    p.add_argument("--candidato-rascunho", type=Path, help="JSON privado da lacuna de venda/abate")
    p.add_argument("--criar-rascunho", action="store_true")
    p.add_argument("--executar", action="store_true")
    p.add_argument("--confirmacao", default="")
    return p


def main() -> int:
    args = parser().parse_args()
    try:
        mapping = load_map(args.mapa)
        client = ConfinexClient()
        plan = build_plan(client, mapping)
        draft = candidate_draft(args.candidato_rascunho, mapping) if args.candidato_rascunho else None
        if draft:
            plan["rascunho_proposto"] = draft
            plan["rascunho_plano_id"] = candidate_plan_id(draft)
            plan["observacao_rascunho"] = "Somente proposta; nenhuma tabela operacional será gravada."
        if args.criar_rascunho:
            if not draft:
                raise ConfinexError("--criar-rascunho exige --candidato-rascunho")
            result = create_candidate_draft(client, draft, args.confirmacao)
        else:
            result = execute_plan(client, plan, args.confirmacao) if args.executar else plan
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    except ConfinexError as exc:
        print(json.dumps({"erro": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
