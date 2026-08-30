#!/usr/bin/env python3
"""Executor controlado de promocoes operacionais do Confinex."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from confinex_client import (
    ConfinexClient,
    ConfinexConnectionError,
    ConfinexError,
    ConfinexHTTPError,
    ConfinexIdempotencyConflict,
)

ALLOWED_TARGETS = {"compras", "vendas", "pesagens_caderno", "abates"}
REQUIRED_ACTION = "promover_revisao_operacional"
CONFIRM_TEMPLATE = "PROMOVER {id}"
RECOVERY_CONFIRM_TEMPLATE = "RECUPERAR PROMOCAO {id} FENCING {fencing}"
LEASE_V1 = "lease-v1"
LEASE_SECONDS = 300

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
        "promocao_origem_id",
    },
    "pesagens_caderno": {
        "contexto", "data_folha", "brinco", "peso_kg", "dente", "conf_brinco",
        "conf_peso", "conf_dente", "conferido", "foto_ref", "origem", "lote_id",
        "operacao_id", "obs", "promocao_origem_id",
    },
    "abates": {
        "data_abate", "lote", "cabecas", "peso_liquido_kg", "valor_liquido",
        "origem_mensagem_id", "observacao", "promocao_origem_id",
    },
}

NUMERIC_FIELDS = {
    "quantidade", "peso_medio_arroba", "peso_total_kg", "preco_arroba",
    "preco_por_cabeca", "valor_total", "prazo_dias", "cabecas",
    "peso_carcaca_total", "rendimento_pct", "valor_bruto", "funrural", "finpec",
    "outros_custos", "peso_liquido_kg", "valor_liquido",
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
    if action.get("executavel") is not True:
        raise ConfinexError("pendencia marcada como nao executavel")
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


def lease_v1(action: dict[str, Any]) -> bool:
    return action.get("promocao_controle_version") == LEASE_V1


def is_corrective_review(value: Any) -> bool:
    return value == "corretiva_pos_gravacao"


def validate_source_review(
    client: ConfinexClient,
    action: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    """Impede que uma revisão corretiva seja tratada como nova promoção."""
    dados_revisados = payload.get("dados_revisados")
    revisao_dados = (
        dados_revisados.get("revisao_tipo")
        if isinstance(dados_revisados, dict)
        else None
    )
    for candidate in (
        action.get("revisao_tipo"),
        payload.get("revisao_tipo"),
        revisao_dados,
    ):
        if is_corrective_review(candidate):
            raise ConfinexError("revisao corretiva pos-gravacao nao pode ser promovida")

    source_draft_id = payload.get("source_draft_id")
    if not source_draft_id:
        return
    drafts = client.select(
        "operation_drafts",
        select="id,revisao_tipo",
        id=f"eq.{source_draft_id}",
    )
    if len(drafts) != 1:
        raise ConfinexError("rascunho de origem da promocao nao encontrado")
    if is_corrective_review(drafts[0].get("revisao_tipo")):
        raise ConfinexError("revisao corretiva pos-gravacao nao pode ser promovida")


def claim_lease_v1(
    client: ConfinexClient,
    action: dict[str, Any],
    *,
    action_id: str,
    usuario: str,
    origem_conversa_id: str,
    origem_mensagem_id: str,
) -> tuple[str, int]:
    claimed = client.rpc(
        "assumir_promocao_operacional",
        {
            "p_pending_action_id": action_id,
            "p_status_esperado": action.get("status"),
            "p_executor": usuario,
            "p_confirmacao_origem_conversa_id": origem_conversa_id,
            "p_confirmacao_origem_mensagem_id": origem_mensagem_id,
            "p_lease_segundos": LEASE_SECONDS,
        },
    )
    if not isinstance(claimed, dict) or claimed.get("assumida") is not True:
        raise ConfinexError("claim lease-v1 retornou resposta invalida")
    if str(claimed.get("pending_action_id")) != action_id:
        raise ConfinexError("claim lease-v1 retornou outra pendencia")
    token = claimed.get("lease_token")
    fencing = claimed.get("fencing_token")
    if not isinstance(token, str) or not token:
        raise ConfinexError("claim lease-v1 nao retornou lease_token valido")
    if isinstance(fencing, bool) or not isinstance(fencing, int) or fencing <= 0:
        raise ConfinexError("claim lease-v1 nao retornou fencing_token valido")
    return token, fencing


def _conclude_lease_v1_once(
    client: ConfinexClient,
    *,
    action_id: str,
    lease_token: str,
    fencing_token: int,
    status: str,
    resultado: dict[str, Any],
) -> dict[str, Any]:
    completed = client.rpc(
        "concluir_promocao_operacional",
        {
            "p_pending_action_id": action_id,
            "p_lease_token": lease_token,
            "p_fencing_token": fencing_token,
            "p_status": status,
            "p_resultado": resultado,
        },
    )
    if not isinstance(completed, dict) or completed.get("status") != status:
        raise ConfinexError("conclusao lease-v1 retornou resposta invalida")
    if completed.get("concluida") is not True and completed.get("repeticao_idempotente") is not True:
        raise ConfinexError("conclusao lease-v1 nao confirmou o encerramento")
    return completed


def conclude_lease_v1(
    client: ConfinexClient,
    *,
    action_id: str,
    lease_token: str,
    fencing_token: int,
    status: str,
    resultado: dict[str, Any],
    tentativas: int = 3,
) -> dict[str, Any]:
    """Repete somente o mesmo pedido terminal, que é idempotente no banco."""
    ultima_falha: ConfinexConnectionError | None = None
    for _ in range(tentativas):
        try:
            return _conclude_lease_v1_once(
                client,
                action_id=action_id,
                lease_token=lease_token,
                fencing_token=fencing_token,
                status=status,
                resultado=resultado,
            )
        except ConfinexConnectionError as exc:
            ultima_falha = exc
    if ultima_falha is not None:
        raise ultima_falha
    raise ConfinexError("conclusao lease-v1 nao foi tentada")


def lease_result(
    target: str,
    *,
    target_record_id: Any = None,
    promoted: bool,
    idempotency_key: str | None,
    uncertain: bool = False,
    error: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "target_table": target,
        "target_record_id": target_record_id,
        "promovido_para_operacional": promoted,
    }
    if uncertain:
        result.update(
            {
                "requer_reconciliacao": True,
                "estado_idempotencia": "uncertain",
                "idempotency_key": idempotency_key,
            }
        )
    if error:
        result["erro"] = error[:1000]
    return result


def expected_confirmation(action_id: str) -> str:
    return CONFIRM_TEMPLATE.format(id=action_id)


def expected_recovery_confirmation(action_id: str, fencing_token: int) -> str:
    """Frase humana exata para a recuperação manual de um lease perdido."""
    if isinstance(fencing_token, bool) or not isinstance(fencing_token, int) or fencing_token <= 0:
        raise ConfinexError("fencing esperado invalido para recuperacao")
    return RECOVERY_CONFIRM_TEMPLATE.format(id=action_id, fencing=fencing_token)


def purchase_idempotency_key(action_id: str) -> str:
    return f"promocao_operacional:{action_id}"


def record_sha256(target: str, record: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"target": target, "record": record},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def mark_uncertain(
    client: ConfinexClient,
    action_id: str,
    payload: dict[str, Any],
    *,
    target: str,
    idempotency_key: str | None,
) -> None:
    """Bloqueia repetição quando não é possível provar se houve gravação."""
    uncertain_payload = {
        **payload,
        "idempotency": {
            "key": idempotency_key,
            "state": "uncertain",
        },
    }
    client.update(
        "pending_actions",
        {"id": f"eq.{action_id}", "status": "eq.em_execucao"},
        {
            "status": "erro_pos_gravacao",
            "atualizado_em": utc_now(),
            "payload": uncertain_payload,
            "erro": (
                "Resultado da gravação não foi confirmado. "
                "Não repetir; reconciliar pela chave idempotente."
            ),
            "resultado": {
                "target_table": target,
                "target_record_id": None,
                "promovido_para_operacional": False,
                "requer_reconciliacao": True,
                "estado_idempotencia": "uncertain",
                "idempotency_key": idempotency_key,
            },
        },
    )


def fetch_action(client: ConfinexClient, action_id: str) -> dict[str, Any]:
    rows = client.select("pending_actions", select="*", id=f"eq.{action_id}")
    if len(rows) != 1:
        raise ConfinexError("pendencia de promocao nao encontrada")
    return rows[0]


def reconciliar_promocao_em_execucao(
    client: ConfinexClient,
    action_id: str,
    *,
    fencing_esperado: int,
    ator: str,
    motivo: str,
    confirmacao: str | None,
) -> dict[str, Any]:
    """Pede uma única reconciliação manual, sem jamais repetir o INSERT.

    Esta é uma ação de recuperação explicitamente autorizada. Ela não tenta
    assumir a promoção, não chama ``insert_operational`` e não reexecuta a RPC
    após falha de transporte: uma resposta perdida deve ser retomada por nova
    confirmação humana, usando exatamente a mesma frase.
    """
    if isinstance(fencing_esperado, bool) or not isinstance(fencing_esperado, int) or fencing_esperado <= 0:
        raise ConfinexError("fencing esperado invalido para recuperacao")
    ator_n = str(ator or "").strip()
    motivo_n = str(motivo or "").strip()
    if not ator_n or len(ator_n.encode("utf-8")) > 160:
        raise ConfinexError("ator da recuperacao invalido")
    if not motivo_n or len(motivo_n.encode("utf-8")) > 1000:
        raise ConfinexError("motivo da recuperacao e obrigatorio e deve ser curto")
    esperada = expected_recovery_confirmation(action_id, fencing_esperado)
    if confirmacao != esperada:
        raise ConfinexError(f"confirmacao invalida; use exatamente: {esperada}")

    action = fetch_action(client, action_id)
    if action.get("acao_tipo") != REQUIRED_ACTION or action.get("executavel") is not True:
        raise ConfinexError("pendencia nao e uma promocao operacional recuperavel")
    if not lease_v1(action):
        raise ConfinexError("recuperacao manual exige promocao lease-v1")
    status = action.get("status")
    if status not in {"em_execucao", "executado", "erro_pos_gravacao", "erro"}:
        raise ConfinexError(f"status nao permite recuperacao manual: {status}")
    fencing_atual = (
        action.get("promocao_fencing_token")
        if status == "em_execucao"
        else action.get("promocao_resultado_fencing_token")
    )
    if (
        isinstance(fencing_atual, bool)
        or not isinstance(fencing_atual, int)
        or fencing_atual != fencing_esperado
    ):
        raise ConfinexError("fencing da promocao nao corresponde ao pedido de recuperacao")

    pedido = {
        "p_pending_action_id": action_id,
        "p_fencing_esperado": fencing_esperado,
        "p_ator": ator_n,
        "p_motivo": motivo_n,
    }
    try:
        resposta = client.rpc("reconciliar_promocao_em_execucao", pedido)
    except ConfinexConnectionError as exc:
        raise ConfinexError(
            "resultado da recuperacao incerto; não houve novo lançamento e "
            "a recuperação não será repetida automaticamente"
        ) from exc
    if not isinstance(resposta, dict):
        raise ConfinexError("recuperacao retornou resposta invalida")
    recuperada = resposta.get("recuperada") is True
    repeticao = resposta.get("repeticao_idempotente") is True
    status_final = resposta.get("status")
    if not (recuperada or repeticao) or status_final not in {"executado", "erro_pos_gravacao"}:
        raise ConfinexError("recuperacao nao confirmou um estado terminal seguro")
    evento_id = resposta.get("evento_id")
    if not isinstance(evento_id, str) or not evento_id:
        raise ConfinexError("recuperacao nao retornou evento de auditoria")
    target_record_id = resposta.get("target_record_id")
    # Na repetição idempotente o banco retorna o evento já vinculado, sem
    # repetir o identificador operacional. Isso ainda é prova suficiente: o
    # evento determinístico só existe depois de a primeira reconciliação ter
    # validado o vínculo dentro da mesma transação. Já uma recuperação nova
    # precisa devolver a identificação do registro encontrado.
    if recuperada and status_final == "executado" and target_record_id in (None, ""):
        raise ConfinexError("recuperacao executada sem identificacao operacional")
    if status_final == "erro_pos_gravacao" and target_record_id not in (None, ""):
        raise ConfinexError("recuperacao sem vinculo retornou identificacao operacional")
    return {
        "pending_action_id": action_id,
        "recuperacao_solicitada": True,
        "nenhum_insert_executado": True,
        "status": status_final,
        "repeticao_idempotente": repeticao,
        "evento_id": evento_id,
        "target_record_id": target_record_id,
        "vinculo_operacional_confirmado": status_final == "executado",
        "confirmacao_esperada": esperada,
    }


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
    if src_chat and (
        not origem_conversa_id or str(origem_conversa_id) != src_chat
    ):
        raise ConfinexError(
            "confirmacao deve vir do mesmo contexto/grupo da origem"
        )


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
    if target != "compras":
        record["promocao_origem_id"] = action_id
    now = utc_now()
    result: dict[str, Any] = {
        "pending_action_id": action_id,
        "target_table": target,
        "record_sha256": record_sha256(target, record),
        "executado": False,
        "confirmacao_esperada": expected_confirmation(action_id),
        "idempotency_key": (
            purchase_idempotency_key(action_id)
            if target == "compras"
            else None
        ),
    }
    if not executar:
        return result

    expected = expected_confirmation(action_id)
    if confirmacao != expected:
        raise ConfinexError(f"confirmacao invalida; use exatamente: {expected}")
    validate_execution_origin(payload, origem_conversa_id, origem_mensagem_id)
    validate_source_review(client, action, payload)

    idempotency_key = purchase_idempotency_key(action_id) if target == "compras" else None
    claimed_payload = {
        **payload,
        "idempotency": {
            "key": idempotency_key,
            "state": "processing",
        },
    }
    lease: tuple[str, int] | None = None
    if lease_v1(action):
        lease = claim_lease_v1(
            client,
            action,
            action_id=action_id,
            usuario=usuario,
            origem_conversa_id=str(origem_conversa_id),
            origem_mensagem_id=str(origem_mensagem_id),
        )
    else:
        claimed = client.update(
            "pending_actions",
            {"id": f"eq.{action_id}", "status": f"eq.{action.get('status')}"},
            {
                "status": "em_execucao",
                "atualizado_em": now,
                "confirmado_por": usuario,
                "confirmado_em": now,
                "payload": claimed_payload,
            },
        )
        if len(claimed) != 1:
            raise ConfinexError("pendencia ja foi assumida ou alterada por outra execucao")

    try:
        insert_result = client.insert_operational(
            target,
            record,
            idempotency_key=idempotency_key,
        )
        inserted = insert_result.record
    except ConfinexConnectionError as exc:
        try:
            if lease is not None:
                conclude_lease_v1(
                    client,
                    action_id=action_id,
                    lease_token=lease[0],
                    fencing_token=lease[1],
                    status="erro_pos_gravacao",
                    resultado=lease_result(
                        target,
                        promoted=False,
                        idempotency_key=idempotency_key,
                        uncertain=True,
                    ),
                )
            else:
                mark_uncertain(
                    client,
                    action_id,
                    claimed_payload,
                    target=target,
                    idempotency_key=idempotency_key,
                )
        except Exception:
            # A ação continua em em_execucao com a chave persistida, o que
            # também impede nova execução até reconciliação explícita.
            pass
        raise ConfinexError(
            "resultado operacional incerto; não repita a promoção"
        ) from exc
    except (ConfinexHTTPError, ConfinexIdempotencyConflict, ConfinexError) as exc:
        if lease is not None:
            conclude_lease_v1(
                client,
                action_id=action_id,
                lease_token=lease[0],
                fencing_token=lease[1],
                status="erro",
                resultado=lease_result(
                    target,
                    promoted=False,
                    idempotency_key=idempotency_key,
                    error=f"Falha antes da gravação operacional: {exc}",
                ),
            )
        else:
            client.update(
                "pending_actions",
                {"id": f"eq.{action_id}", "status": "eq.em_execucao"},
                {
                    "status": "erro",
                    "atualizado_em": utc_now(),
                    "payload": {
                        **claimed_payload,
                        "idempotency": {
                            "key": idempotency_key,
                            "state": "failed",
                        },
                    },
                    "erro": f"Falha antes da gravação operacional: {exc}"[:1000],
                },
            )
        raise
    except Exception as exc:
        try:
            if lease is not None:
                conclude_lease_v1(
                    client,
                    action_id=action_id,
                    lease_token=lease[0],
                    fencing_token=lease[1],
                    status="erro_pos_gravacao",
                    resultado=lease_result(
                        target,
                        promoted=False,
                        idempotency_key=idempotency_key,
                        uncertain=True,
                    ),
                )
            else:
                mark_uncertain(
                    client,
                    action_id,
                    claimed_payload,
                    target=target,
                    idempotency_key=idempotency_key,
                )
        except Exception:
            pass
        raise ConfinexError(
            "falha inesperada com resultado incerto; não repita a promoção"
        ) from exc

    if not isinstance(inserted, dict) or not inserted.get("id"):
        try:
            if lease is not None:
                conclude_lease_v1(
                    client,
                    action_id=action_id,
                    lease_token=lease[0],
                    fencing_token=lease[1],
                    status="erro_pos_gravacao",
                    resultado=lease_result(
                        target,
                        promoted=False,
                        idempotency_key=idempotency_key,
                        uncertain=True,
                    ),
                )
            else:
                mark_uncertain(
                    client,
                    action_id,
                    claimed_payload,
                    target=target,
                    idempotency_key=idempotency_key,
                )
        except Exception:
            pass
        raise ConfinexError(
            "a gravação pode ter ocorrido sem retornar identificação; "
            "não repita a promoção"
        )

    payload = {
        **payload,
        "promovido_para_operacional": True,
        "promovido_em": now,
        "promovido_por": usuario,
        "target_table": target,
        "target_record_id": inserted.get("id"),
        "record_executed": record,
        "idempotency_status": insert_result.status,
        "idempotency_key": idempotency_key,
        "confirmacao_origem_conversa_id": origem_conversa_id,
        "confirmacao_origem_mensagem_id": origem_mensagem_id,
    }
    source_draft_id = payload.get("source_draft_id")
    try:
        if lease is not None:
            completion = conclude_lease_v1(
                client,
                action_id=action_id,
                lease_token=lease[0],
                fencing_token=lease[1],
                status="executado",
                resultado=lease_result(
                    target,
                    target_record_id=inserted.get("id"),
                    promoted=True,
                    idempotency_key=idempotency_key,
                ),
            )
            event = {"id": completion.get("evento_id")}
        else:
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
                        "idempotency_status": insert_result.status,
                    },
                    "observacao": "Promocao operacional executada por rotina controlada de backend.",
                },
            )
            if source_draft_id:
                draft_updated = client.update(
                    "operation_drafts",
                    {"id": f"eq.{source_draft_id}"},
                    {
                        "status": "realizado",
                        "atualizado_em": now,
                        "entidade_final_tipo": target,
                        "entidade_final_id": inserted.get("id"),
                    },
                )
                if len(draft_updated) != 1:
                    raise ConfinexError(
                        "o rascunho de origem mudou ou não pôde ser encerrado"
                    )
            completed = client.update(
                "pending_actions",
                {"id": f"eq.{action_id}", "status": "eq.em_execucao"},
                {
                    "status": "executado",
                    "atualizado_em": now,
                    "payload": payload,
                    "erro": None,
                    "resultado": {
                        "target_table": target,
                        "target_record_id": inserted.get("id"),
                        "promovido_para_operacional": True,
                    },
                },
            )
            if len(completed) != 1:
                raise ConfinexError("nao foi possivel encerrar a pendencia assumida")
    except Exception as exc:
        if lease is None:
            client.update(
                "pending_actions",
                {"id": f"eq.{action_id}", "status": "eq.em_execucao"},
                {
                    "status": "erro_pos_gravacao",
                    "atualizado_em": utc_now(),
                    "payload": payload,
                    "erro": (
                        f"Registro {target}/{inserted.get('id')} criado; "
                        f"nao repetir a promocao. Falha ao finalizar auditoria: {exc}"
                    )[:1000],
                    "resultado": {
                        "target_table": target,
                        "target_record_id": inserted.get("id"),
                        "promovido_para_operacional": True,
                        "requer_reconciliacao": True,
                    },
                },
            )
        raise ConfinexError(
            f"registro operacional {target}/{inserted.get('id')} foi criado, "
            "mas a finalizacao atomica nao foi confirmada; nao repita a "
            "promocao e execute a recuperacao controlada"
        ) from exc
    result.update(
        {
            "executado": True,
            "target_record_id": inserted.get("id"),
            "evento_id": event.get("id"),
            "idempotency_status": insert_result.status,
        }
    )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Executa promocoes operacionais preparadas na fila de revisoes")
    parser.add_argument("--pending-action-id", required=True)
    parser.add_argument("--usuario", default="pablo")
    parser.add_argument("--executar", action="store_true", help="Grava a tabela operacional e encerra a pendencia")
    parser.add_argument("--confirmacao", help="Frase exata: PROMOVER <pending_action_id>")
    parser.add_argument("--origem-conversa-id")
    parser.add_argument("--origem-mensagem-id")
    parser.add_argument(
        "--recuperar-em-execucao", action="store_true",
        help="Reconcilia manualmente um lease-v1 expirado; nunca repete o lançamento operacional",
    )
    parser.add_argument("--fencing-esperado", type=int)
    parser.add_argument("--motivo-recuperacao")
    parser.add_argument(
        "--confirmacao-recuperacao",
        help="Frase exata: RECUPERAR PROMOCAO <pending_action_id> FENCING <numero>",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        client = ConfinexClient()
        if args.recuperar_em_execucao:
            if args.executar or args.confirmacao or args.origem_conversa_id or args.origem_mensagem_id:
                raise ConfinexError(
                    "recuperacao manual nao aceita flags de uma nova promocao"
                )
            if args.fencing_esperado is None or args.motivo_recuperacao is None:
                raise ConfinexError(
                    "recuperacao manual exige fencing esperado e motivo"
                )
            emit(reconciliar_promocao_em_execucao(
                client,
                args.pending_action_id,
                fencing_esperado=args.fencing_esperado,
                ator=args.usuario,
                motivo=args.motivo_recuperacao,
                confirmacao=args.confirmacao_recuperacao,
            ))
            return 0
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
