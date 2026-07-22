#!/usr/bin/env python3
"""Executor controlado de promocoes operacionais do Confinex."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from confinex_client import ConfinexClient, ConfinexError

ALLOWED_TARGETS = {"compras", "vendas", "pesagens_caderno", "abates"}
REQUIRED_ACTION = "promover_revisao_operacional"
CONFIRM_TEMPLATE = "PROMOVER {id}"

TARGET_COLUMNS = {
    "compras": {
        "operacao_id", "vendedor_id", "corretor_id", "data", "data_embarque_prevista",
        "quantidade", "peso_medio_arroba", "peso_total_kg", "preco_arroba",
        "preco_por_cabeca", "valor_total", "prazo_dias", "data_pagamento",
        "pago", "funrural", "descontos", "origem_registro", "telegram_msg_id", "obs",
    },
    "vendas": {
        "operacao_id", "comprador_id", "data_abate", "cabecas", "peso_carcaca_total",
        "rendimento_pct", "preco_arroba", "valor_bruto", "funrural", "finpec",
        "prazo_recebimento", "nf_venda", "romaneio", "recebido", "outros_custos", "custos_obs",
    },
    "pesagens_caderno": {
        "contexto", "data_folha", "brinco", "peso_kg", "dente", "conf_brinco",
        "conf_peso", "conf_dente", "conferido", "foto_ref", "origem", "lote_id",
        "operacao_id", "obs",
    },
    "abates": {
        "data_abate", "lote", "cabecas", "peso_liquido_kg", "valor_liquido",
        "origem_mensagem_id", "observacao",
    },
}

NUMERIC_FIELDS = {
    "quantidade", "peso_medio_arroba", "peso_total_kg", "preco_arroba",
    "preco_por_cabeca", "valor_total", "prazo_dias", "cabecas",
    "peso_carcaca_total", "rendimento_pct", "valor_bruto", "funrural", "finpec",
    "prazo_recebimento", "outros_custos", "peso_liquido_kg", "valor_liquido",
    "peso_kg", "conf_brinco", "conf_peso", "conf_dente",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def normalize_number(value: Any) -> Any:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip().replace("R$", "").replace(" ", "")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        number = Decimal(text)
    except InvalidOperation:
        return value
    if number == number.to_integral_value():
        return int(number)
    return float(number)



def first_present(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return None


def normalize_compras(proposed: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(proposed)
    if "data" not in normalized:
        value = first_present(normalized, "data_compra", "data_pesagem", "data_folha")
        if value is not None:
            normalized["data"] = value
    if "quantidade" not in normalized:
        value = first_present(normalized, "cabecas", "qtd_cabecas")
        if value is not None:
            normalized["quantidade"] = value
    if "valor_total" not in normalized:
        value = first_present(normalized, "valor_bruto", "valor_total_base")
        if value is not None:
            normalized["valor_total"] = value
    if "telegram_msg_id" not in normalized:
        value = first_present(normalized, "origem_mensagem_id", "mensagem_id", "foto_ref")
        if value is not None:
            normalized["telegram_msg_id"] = str(value)
    if "data_pagamento" not in normalized:
        value = first_present(normalized, "vencimento", "data_vencimento")
        if value is not None:
            normalized["data_pagamento"] = value
    if "obs" not in normalized:
        value = first_present(normalized, "observacao", "resumo", "situacao")
        if value is not None:
            normalized["obs"] = value
    normalized.setdefault("origem_registro", "confinex_revisoes")
    return normalized


def normalize_vendas(proposed: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(proposed)
    if "cabecas" not in normalized:
        value = first_present(normalized, "quantidade", "qtd_cabecas")
        if value is not None:
            normalized["cabecas"] = value
    if "peso_carcaca_total" not in normalized:
        value = first_present(normalized, "peso_liquido_kg", "peso_total_kg")
        if value is not None:
            normalized["peso_carcaca_total"] = value
    if "prazo_recebimento" not in normalized:
        value = first_present(normalized, "vencimento", "data_recebimento", "data_vencimento")
        if value is not None:
            normalized["prazo_recebimento"] = value
    if "romaneio" not in normalized:
        value = first_present(normalized, "documento", "tipo_documento")
        if value is not None:
            normalized["romaneio"] = value
    return normalized


def normalize_pesagens_caderno(proposed: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(proposed)
    if "contexto" not in normalized and normalized.get("contexto_operacional"):
        normalized["contexto"] = normalized.get("contexto_operacional")
    if "data_folha" not in normalized and normalized.get("data_pesagem"):
        normalized["data_folha"] = normalized.get("data_pesagem")
    if "peso_kg" not in normalized:
        normalized["peso_kg"] = normalized.get("peso_kg") or normalized.get("peso_total_kg")
    if "foto_ref" not in normalized and normalized.get("origem_mensagem_id"):
        normalized["foto_ref"] = str(normalized.get("origem_mensagem_id"))
    if "obs" not in normalized:
        normalized["obs"] = normalized.get("observacao") or normalized.get("resumo")
    normalized.setdefault("origem", "confinex_revisoes")
    normalized.setdefault("conferido", True)
    return normalized

def clean_record(target: str, proposed: dict[str, Any]) -> dict[str, Any]:
    if target not in TARGET_COLUMNS:
        raise ConfinexError(f"destino operacional nao permitido: {target}")
    if target == "compras":
        proposed = normalize_compras(proposed)
    elif target == "vendas":
        proposed = normalize_vendas(proposed)
    elif target == "pesagens_caderno":
        proposed = normalize_pesagens_caderno(proposed)
    clean: dict[str, Any] = {}
    for key, value in proposed.items():
        if key not in TARGET_COLUMNS[target]:
            continue
        if value in (None, ""):
            continue
        clean[key] = normalize_number(value) if key in NUMERIC_FIELDS else value
    return clean


def validate_action(action: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any]]:
    if action.get("acao_tipo") != REQUIRED_ACTION:
        raise ConfinexError("pendencia nao e uma promocao operacional")
    if action.get("status") not in {"aguardando_confirmacao", "aprovado_confinex"}:
        raise ConfinexError(f"status nao permite promover: {action.get('status')}")
    payload = action.get("payload")
    if not isinstance(payload, dict):
        raise ConfinexError("payload da pendencia esta ausente ou invalido")
    target = payload.get("target_table") or action.get("entidade_tipo")
    if target not in ALLOWED_TARGETS:
        raise ConfinexError(f"destino operacional nao permitido: {target}")
    if payload.get("promovido_para_operacional") is True:
        raise ConfinexError("pendencia ja foi marcada como promovida")
    proposed = payload.get("proposed_record")
    if not isinstance(proposed, dict):
        raise ConfinexError("proposed_record ausente ou invalido")
    record = clean_record(target, proposed)
    if not record:
        raise ConfinexError("registro operacional ficou vazio apos validacao")
    return target, payload, record


def expected_confirmation(action_id: str) -> str:
    return CONFIRM_TEMPLATE.format(id=action_id)


def fetch_action(client: ConfinexClient, action_id: str) -> dict[str, Any]:
    rows = client.select("pending_actions", select="*", id=f"eq.{action_id}")
    if len(rows) != 1:
        raise ConfinexError("pendencia de promocao nao encontrada")
    return rows[0]


def source_value(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        current: Any = payload
        ok = True
        for part in key.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                ok = False
                break
        if ok and current not in (None, ""):
            return str(current)
    return None


def validate_execution_origin(
    payload: dict[str, Any],
    origem_conversa_id: str | None,
    origem_mensagem_id: str | None,
) -> None:
    src_chat = source_value(payload, "dados_revisados.origem_conversa_id", "origem_conversa_id")
    src_msg = source_value(payload, "dados_revisados.origem_mensagem_id", "origem_mensagem_id")
    if not origem_mensagem_id:
        raise ConfinexError("origem_mensagem_id da confirmacao e obrigatoria para executar")
    if src_msg and str(origem_mensagem_id) == src_msg:
        raise ConfinexError("a confirmacao deve vir de uma nova mensagem")
    if src_chat and origem_conversa_id and str(origem_conversa_id) != src_chat:
        raise ConfinexError("confirmacao veio de contexto/grupo diferente da origem")


def execute_promotion(
    client: ConfinexClient,
    action_id: str,
    *,
    usuario: str,
    executar: bool,
    confirmacao: str | None,
    origem_conversa_id: str | None = None,
    origem_mensagem_id: str | None = None,
) -> dict[str, Any]:
    action = fetch_action(client, action_id)
    target, payload, record = validate_action(action)
    now = utc_now()
    result: dict[str, Any] = {
        "pending_action_id": action_id,
        "target_table": target,
        "record": record,
        "executado": False,
        "confirmacao_esperada": expected_confirmation(action_id),
    }
    if not executar:
        return result

    expected = expected_confirmation(action_id)
    if confirmacao != expected:
        raise ConfinexError(f"confirmacao invalida; use exatamente: {expected}")
    validate_execution_origin(payload, origem_conversa_id, origem_mensagem_id)

    inserted = client.insert_operational(target, record)
    payload = {
        **payload,
        "promovido_para_operacional": True,
        "promovido_em": now,
        "promovido_por": usuario,
        "target_table": target,
        "target_record_id": inserted.get("id"),
        "record_executed": record,
        "confirmacao_origem_conversa_id": origem_conversa_id,
        "confirmacao_origem_mensagem_id": origem_mensagem_id,
    }
    client.update(
        "pending_actions",
        {"id": f"eq.{action_id}"},
        {
            "status": "executado",
            "atualizado_em": now,
            "confirmado_por": usuario,
            "confirmado_em": now,
            "payload": payload,
            "resultado": {
                "target_table": target,
                "target_record_id": inserted.get("id"),
                "promovido_para_operacional": True,
            },
        },
    )
    source_draft_id = payload.get("source_draft_id")
    if source_draft_id:
        client.update(
            "operation_drafts",
            {"id": f"eq.{source_draft_id}"},
            {
                "status": "realizado",
                "atualizado_em": now,
                "entidade_final_tipo": target,
                "entidade_final_id": inserted.get("id"),
            },
        )
    event = client.insert(
        "eventos",
        {
            "tipo": "promocao_operacional_executada",
            "agente": "confinex",
            "usuario": usuario,
            "entidade_tipo": target,
            "entidade_id": inserted.get("id"),
            "entidade_codigo": action.get("entidade_codigo"),
            "origem": "confinex_promocao_operacional",
            "status": "registrado",
            "dados": {
                "pending_action_id": action_id,
                "source_draft_id": source_draft_id,
                "target_table": target,
                "target_record_id": inserted.get("id"),
                "record": record,
                "promovido_para_operacional": True,
            },
            "observacao": "Promocao operacional executada por rotina controlada de backend.",
        },
    )
    result.update({"executado": True, "target_record_id": inserted.get("id"), "evento_id": event.get("id")})
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Executa promocoes operacionais preparadas na fila de revisoes")
    parser.add_argument("--pending-action-id", required=True)
    parser.add_argument("--usuario", default="pablo")
    parser.add_argument("--executar", action="store_true", help="Grava a tabela operacional e encerra a pendencia")
    parser.add_argument("--confirmacao", help="Frase exata: PROMOVER <pending_action_id>")
    parser.add_argument("--origem-conversa-id")
    parser.add_argument("--origem-mensagem-id")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        client = ConfinexClient()
        emit(
            execute_promotion(
                client,
                args.pending_action_id,
                usuario=args.usuario,
                executar=args.executar,
                confirmacao=args.confirmacao,
                origem_conversa_id=args.origem_conversa_id,
                origem_mensagem_id=args.origem_mensagem_id,
            )
        )
        return 0
    except ConfinexError as exc:
        print(json.dumps({"erro": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
