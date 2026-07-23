#!/usr/bin/env python3
"""Planeja e, sob confirmação forte, saneia vínculos da fila de Revisões.

O modo padrão é somente leitura. A rotina nunca escreve em tabelas operacionais;
quando autorizada, limita-se a ligar `operation_drafts.pending_action_id` a uma
`pending_actions` já existente e fortemente correspondente.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from typing import Any

from confinex_client import ConfinexClient, ConfinexError

OPEN_DRAFT_STATUS = {"rascunho", "aguardando_confirmacao", "confirmado_telegram", "em_revisao", "erro"}
OPEN_ACTION_STATUS = {"aguardando_confirmacao", "confirmado_telegram", "em_revisao", "erro", "em_execucao", "erro_pos_gravacao"}
CONFIRM_PREFIX = "SANEAR FILA"
SELECT_DRAFTS = ",".join([
    "id", "status", "pending_action_id", "tipo_operacao", "entidade_final_tipo",
    "codigo_sugerido", "dados_extraidos", "contexto_canonico", "contexto_nome",
    "origem_canal", "origem_conversa_id", "origem_mensagem_id", "agente", "criado_em",
])
SELECT_ACTIONS = ",".join([
    "id", "status", "acao_tipo", "entidade_tipo", "entidade_id", "entidade_codigo",
    "resumo", "payload", "canal", "conversa_id", "mensagem_id", "contexto_canonico",
    "contexto_nome", "criado_em",
])
SELECT_EVENTS = "id,tipo,entidade_tipo,entidade_id,origem_canal,origem_conversa_id,origem_mensagem_id,contexto_canonico,status"


def payload(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("payload") or row.get("dados_extraidos") or {}
    return value if isinstance(value, dict) else {}


def nested(row: dict[str, Any], *paths: str) -> Any:
    sources = [row, payload(row)]
    extra = payload(row)
    for key in ("dados_extraidos", "dados_revisados"):
        if isinstance(extra.get(key), dict):
            sources.append(extra[key])
    for source in sources:
        for path in paths:
            cur: Any = source
            for part in path.split("."):
                if not isinstance(cur, dict) or part not in cur:
                    cur = None
                    break
                cur = cur[part]
            if cur not in (None, ""):
                return cur
    return None


def origin(row: dict[str, Any], kind: str) -> dict[str, str]:
    if kind == "draft":
        canal = nested(row, "origem_canal") or ""
        conversa = nested(row, "origem_conversa_id") or ""
        mensagem = nested(row, "origem_mensagem_id") or ""
    else:
        canal = nested(row, "canal", "origem_canal") or ""
        conversa = nested(row, "conversa_id", "origem_conversa_id") or ""
        mensagem = nested(row, "mensagem_id", "origem_mensagem_id") or ""
    return {
        "canal": str(canal),
        "conversa": str(conversa),
        "mensagem": str(mensagem),
        "contexto": str(nested(row, "contexto_canonico") or ""),
        "codigo": str(nested(row, "codigo_sugerido", "entidade_codigo", "operacao_codigo", "negocio_codigo") or ""),
    }


def open_draft(row: dict[str, Any]) -> bool:
    return str(row.get("status") or "") in OPEN_DRAFT_STATUS


def open_action(row: dict[str, Any]) -> bool:
    return str(row.get("status") or "") in OPEN_ACTION_STATUS


def target_text(row: dict[str, Any]) -> str:
    return " ".join(str(value or "") for value in (
        row.get("tipo_operacao"), row.get("entidade_final_tipo"), row.get("acao_tipo"),
        row.get("entidade_tipo"), row.get("resumo"), nested(row, "tipo_documento"),
    )).casefold()


def same_business_family(draft: dict[str, Any], action: dict[str, Any]) -> bool:
    combined = f"{target_text(draft)} {target_text(action)}"
    families = {
        "compra": ("compra", "gado"),
        "venda": ("venda", "abate", "romaneio"),
        "pesagem": ("pesagem", "caderno"),
    }
    for terms in families.values():
        if any(term in combined for term in terms):
            return True
    return False


def match_score(draft: dict[str, Any], action: dict[str, Any]) -> tuple[int, list[str]]:
    d, a = origin(draft, "draft"), origin(action, "action")
    score, reasons = 0, []
    if d["mensagem"] and d["mensagem"] == a["mensagem"]:
        score += 4
        reasons.append("mesma mensagem")
    if d["conversa"] and d["conversa"] == a["conversa"]:
        score += 2
        reasons.append("mesma conversa")
    if d["contexto"] and d["contexto"] == a["contexto"]:
        score += 1
        reasons.append("mesmo contexto")
    if d["codigo"] and d["codigo"] == a["codigo"]:
        score += 1
        reasons.append("mesmo código operacional")
    source_draft = nested(action, "source_draft_id", "revisao_confinex.source_draft_id")
    if source_draft and str(source_draft) == str(draft.get("id")):
        score += 5
        reasons.append("payload aponta para o rascunho")
    if same_business_family(draft, action):
        score += 1
        reasons.append("mesma família operacional")
    return score, reasons


def duplicates(rows: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str], list[str]] = {}
    for row in rows:
        o = origin(row, kind)
        key = (o["canal"], o["conversa"], o["mensagem"], target_text(row))
        if key[2]:
            grouped.setdefault(key, []).append(str(row.get("id")))
    return [
        {"origem": {"canal": k[0], "conversa": k[1], "mensagem": k[2]}, "ids": v}
        for k, v in grouped.items() if len(v) > 1
    ]


def plan_links(drafts: list[dict[str, Any]], actions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates, ambiguous = [], []
    active_drafts = [row for row in drafts if open_draft(row) and not row.get("pending_action_id")]
    active_actions = [row for row in actions if open_action(row)]
    used_actions: set[str] = set()
    for draft in active_drafts:
        scored = []
        for action in active_actions:
            if str(action.get("id")) in used_actions:
                continue
            score, reasons = match_score(draft, action)
            if score >= 6:
                scored.append((score, action, reasons))
        scored.sort(key=lambda item: item[0], reverse=True)
        if len(scored) == 1 or (len(scored) > 1 and scored[0][0] > scored[1][0]):
            score, action, reasons = scored[0]
            link = {
                "draft_id": draft["id"],
                "pending_action_id": action["id"],
                "score": score,
                "motivos": reasons,
                "contexto_nome": draft.get("contexto_nome") or action.get("contexto_nome"),
                "origem_mensagem_id": origin(draft, "draft")["mensagem"] or origin(action, "action")["mensagem"],
            }
            candidates.append(link)
            used_actions.add(str(action.get("id")))
        elif scored:
            ambiguous.append({
                "draft_id": draft["id"],
                "motivo": "mais de uma pendência possível",
                "opcoes": [{"pending_action_id": row[1]["id"], "score": row[0]} for row in scored[:5]],
            })
    return candidates, ambiguous


def build_plan(client: Any) -> dict[str, Any]:
    drafts = client.select("operation_drafts", select=SELECT_DRAFTS, order="criado_em.asc", limit="5000")
    actions = client.select("pending_actions", select=SELECT_ACTIONS, order="criado_em.asc", limit="5000")
    events = client.select("eventos", select=SELECT_EVENTS, order="id.asc", limit="5000")
    links, ambiguous = plan_links(drafts, actions)
    draft_ids = {str(row.get("id")) for row in drafts}
    action_ids = {str(row.get("id")) for row in actions}
    broken_events = [
        {"id": row.get("id"), "entidade_tipo": row.get("entidade_tipo"), "entidade_id": row.get("entidade_id")}
        for row in events
        if row.get("entidade_tipo") in {"operation_draft", "pending_action"}
        and row.get("entidade_id")
        and str(row.get("entidade_id")) not in (draft_ids if row.get("entidade_tipo") == "operation_draft" else action_ids)
    ]
    orphan_drafts = [row["id"] for row in drafts if open_draft(row) and not row.get("pending_action_id")]
    orphan_actions = [row["id"] for row in actions if open_action(row)]
    digest_source = json.dumps({"links": links, "ambiguous": ambiguous}, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    plan_id = hashlib.sha256(digest_source.encode()).hexdigest()[:12]
    return {
        "modo": "dry-run",
        "plano_id": plan_id,
        "vinculos_propostos": links,
        "ambiguos_preservados": ambiguous,
        "duplicidades": {
            "operation_drafts": duplicates(drafts, "draft"),
            "pending_actions": duplicates(actions, "action"),
        },
        "eventos_com_referencia_quebrada": broken_events,
        "rascunhos_abertos_sem_vinculo": orphan_drafts,
        "pendencias_abertas": orphan_actions,
        "totais": {
            "rascunhos": len(drafts),
            "pendencias": len(actions),
            "eventos": len(events),
            "vinculos_propostos": len(links),
            "ambiguos": len(ambiguous),
        },
        "escritas_realizadas": 0,
        "tabelas_operacionais_alteradas": 0,
    }


def execute_plan(client: Any, plan: dict[str, Any], confirmation: str, limit: int | None = None) -> dict[str, Any]:
    expected = f"{CONFIRM_PREFIX} {plan['plano_id']}"
    if confirmation != expected:
        raise ConfinexError(f"confirmação inválida; use exatamente: {expected}")
    links = plan["vinculos_propostos"][:limit]
    updated = []
    for link in links:
        rows = client.update(
            "operation_drafts",
            {"id": f"eq.{link['draft_id']}", "pending_action_id": "is.null"},
            {"pending_action_id": link["pending_action_id"]},
        )
        if len(rows) != 1:
            raise ConfinexError(f"não foi possível vincular rascunho {link['draft_id']}")
        updated.append({"draft_id": link["draft_id"], "pending_action_id": link["pending_action_id"]})
    return {
        **plan,
        "modo": "executado",
        "vinculos_executados": updated,
        "escritas_realizadas": len(updated),
        "tabelas_operacionais_alteradas": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Saneia vínculos da fila de Revisões com dry-run por padrão")
    parser.add_argument("--executar", action="store_true", help="aplica somente vínculos fortemente correspondentes")
    parser.add_argument("--confirmacao", default="", help="frase exata: SANEAR FILA <plano_id>")
    parser.add_argument("--limite", type=int, default=None, help="limita quantidade de vínculos aplicados")
    args = parser.parse_args()
    try:
        client = ConfinexClient()
        plan = build_plan(client)
        result = execute_plan(client, plan, args.confirmacao, args.limite) if args.executar else plan
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except ConfinexError as exc:
        print(json.dumps({"erro": str(exc)}, ensure_ascii=False), file=None)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
