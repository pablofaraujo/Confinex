import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from confinex_client import (
    ConfinexClient,
    ConfinexConnectionError,
    ConfinexHTTPError,
    OperationalInsertResult,
    RPC_FUNCTIONS,
)
from promocao_operacional import (
    clean_record,
    expected_confirmation,
    expected_recovery_confirmation,
    execute_promotion,
    purchase_idempotency_key,
    reconciliar_promocao_em_execucao,
    validate_action,
)


class FakeClient:
    def __init__(self, action):
        self.action = action
        self.operational_inserts = []
        self.audit_inserts = []
        self.updates = []
        self.rpc_calls = []
        self.draft = {"id": "draft-1", "revisao_tipo": "pre_revisao"}
        self.claim_succeeds = True
        self.fail_operational_insert = False
        self.fail_audit_insert = False
        self.fail_draft_update = False
        self.operational_failure = RuntimeError("insert indisponível")
        self.operational_record = None
        self.recovery_response = {
            "recuperada": True,
            "repeticao_idempotente": False,
            "status": "executado",
            "evento_id": "evento-recuperacao-1",
            "target_record_id": "operacional-1",
        }

    def select(self, table, **params):
        if table == "pending_actions" and params.get("id") == f"eq.{self.action['id']}":
            return [self.action]
        if table == "operation_drafts" and params.get("id") == "eq.draft-1":
            return [self.draft]
        return []

    def rpc(self, function, payload):
        self.rpc_calls.append((function, payload))
        if function == "assumir_promocao_operacional":
            return {
                "assumida": True,
                "pending_action_id": self.action["id"],
                "lease_token": "11111111-1111-1111-1111-111111111111",
                "fencing_token": 7,
            }
        if function == "concluir_promocao_operacional":
            return {
                "concluida": True,
                "status": payload["p_status"],
                "evento_id": "evento-atomico-1",
            }
        if function == "reconciliar_promocao_em_execucao":
            return dict(self.recovery_response)
        raise AssertionError(f"RPC inesperada: {function}")

    def insert_operational(self, table, payload, *, idempotency_key=None):
        if self.fail_operational_insert:
            raise self.operational_failure
        self.operational_inserts.append((table, payload, idempotency_key))
        return OperationalInsertResult(
            status="inserted",
            record=self.operational_record or {"id": "operacional-1", **payload},
        )

    def insert(self, table, payload):
        if self.fail_audit_insert:
            raise RuntimeError("auditoria indisponivel")
        self.audit_inserts.append((table, payload))
        return {"id": "evento-1", **payload}

    def update(self, table, filters, payload):
        self.updates.append((table, filters, payload))
        if table == "operation_drafts" and self.fail_draft_update:
            return []
        if table == "pending_actions" and payload.get("status") == "em_execucao" and not self.claim_succeeds:
            return []
        if table == "pending_actions":
            self.action.update(payload)
        return [{**payload, "id": filters["id"].replace("eq.", "")}]


def action_for(target="compras"):
    return {
        "id": "pa-1",
        "acao_tipo": "promover_revisao_operacional",
        "executavel": True,
        "status": "aguardando_confirmacao",
        "entidade_tipo": target,
        "entidade_codigo": "CF-TESTE",
        "payload": {
            "source_draft_id": "draft-1",
            "target_table": target,
            "promovido_para_operacional": False,
            "dados_revisados": {
                "origem_conversa_id": "grupo-1",
                "origem_mensagem_id": "msg-original",
            },
            "proposed_record": {
                "quantidade": "18",
                "valor_total": "R$ 115.033,27",
                "origem_registro": "confinex_revisoes",
                "campo_estranho": "ignorar",
            },
        },
    }


class RpcResponse:
    def __init__(self, payload):
        self.payload = payload.encode("utf-8")

    def read(self):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class PromocaoOperacionalTests(unittest.TestCase):
    def test_clean_record_filters_columns_and_normalizes_numbers(self):
        record = clean_record("compras", {"quantidade": "18", "valor_total": "R$ 115.033,27", "campo_estranho": "x"})
        self.assertEqual(record["quantidade"], 18)
        self.assertEqual(record["valor_total"], 115033.27)
        self.assertNotIn("campo_estranho", record)

    def test_compras_maps_telegram_review_fields_to_real_schema(self):
        record = clean_record("compras", {
            "data_compra": "2026-07-22",
            "cabecas": "18",
            "valor_bruto": "R$ 115.033,27",
            "vencimento": "2026-08-08",
            "origem_mensagem_id": "msg-compra",
            "observacao": "compra conferida",
            "campo_estranho": "ignorar",
        })
        self.assertEqual(record["data"], "2026-07-22")
        self.assertEqual(record["quantidade"], 18)
        self.assertEqual(record["valor_total"], 115033.27)
        self.assertEqual(record["data_pagamento"], "2026-08-08")
        self.assertEqual(record["telegram_msg_id"], "msg-compra")
        self.assertEqual(record["obs"], "compra conferida")
        self.assertEqual(record["origem_registro"], "confinex_revisoes")
        self.assertNotIn("campo_estranho", record)

    def test_vendas_maps_review_fields_without_using_days_as_receipt_date(self):
        record = clean_record("vendas", {
            "quantidade": "18",
            "peso_liquido_kg": "5228,785",
            "valor_bruto": "R$ 115.033,27",
            "vencimento": "2026-08-08",
            "prazo_dias": "30",
            "documento": "Romaneio Frical lote 5",
        })
        self.assertEqual(record["cabecas"], 18)
        self.assertEqual(record["peso_carcaca_total"], 5228.785)
        self.assertEqual(record["valor_bruto"], 115033.27)
        self.assertEqual(record["prazo_recebimento"], "2026-08-08")
        self.assertEqual(record["romaneio"], "Romaneio Frical lote 5")
        self.assertNotEqual(record["prazo_recebimento"], 30)

    def test_pesagens_caderno_maps_legacy_preview_fields_to_real_schema(self):
        record = clean_record("pesagens_caderno", {
            "contexto_operacional": "boi_balanca",
            "data_pesagem": "2026-07-22",
            "peso_total_kg": "5228,785",
            "origem_mensagem_id": "msg-1",
            "observacao": "romaneio teste",
            "quantidade": "18",
        })
        self.assertEqual(record["contexto"], "boi_balanca")
        self.assertEqual(record["data_folha"], "2026-07-22")
        self.assertEqual(record["peso_kg"], 5228.785)
        self.assertEqual(record["foto_ref"], "msg-1")
        self.assertEqual(record["origem"], "confinex_revisoes")
        self.assertTrue(record["conferido"])
        self.assertNotIn("quantidade", record)

    def test_validate_rejects_wrong_action_type(self):
        action = action_for()
        action["acao_tipo"] = "outra_coisa"
        with self.assertRaisesRegex(Exception, "nao e uma promocao"):
            validate_action(action)

    def test_validate_rejects_non_executable_action(self):
        action = action_for()
        action["executavel"] = False
        with self.assertRaisesRegex(Exception, "nao executavel"):
            validate_action(action)

    def test_validate_rejects_missing_or_null_executable_flag(self):
        for value in (None, "missing"):
            with self.subTest(value=value):
                action = action_for()
                if value == "missing":
                    action.pop("executavel")
                else:
                    action["executavel"] = None
                with self.assertRaisesRegex(Exception, "nao executavel"):
                    validate_action(action)

    def test_execute_rejects_corrective_review_before_claim_or_insert(self):
        client = FakeClient(action_for())
        client.draft["revisao_tipo"] = "corretiva_pos_gravacao"
        with self.assertRaisesRegex(Exception, "corretiva pos-gravacao"):
            execute_promotion(
                client,
                "pa-1",
                usuario="pablo",
                executar=True,
                confirmacao=expected_confirmation("pa-1"),
                origem_conversa_id="grupo-1",
                origem_mensagem_id="msg-nova",
            )
        self.assertEqual(client.rpc_calls, [])
        self.assertEqual(client.operational_inserts, [])
        self.assertEqual(client.updates, [])

    def test_default_mode_only_previews(self):
        client = FakeClient(action_for())
        result = execute_promotion(client, "pa-1", usuario="pablo", executar=False, confirmacao=None)
        self.assertFalse(result["executado"])
        self.assertEqual(client.operational_inserts, [])
        self.assertEqual(result["confirmacao_esperada"], "PROMOVER pa-1")
        self.assertNotIn("record", result)
        self.assertRegex(result["record_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            result["idempotency_key"],
            purchase_idempotency_key("pa-1"),
        )

    def test_execute_requires_exact_confirmation(self):
        client = FakeClient(action_for())
        with self.assertRaisesRegex(Exception, "confirmacao invalida"):
            execute_promotion(client, "pa-1", usuario="pablo", executar=True, confirmacao="sim")

    def test_execute_requires_confirmation_message_id(self):
        client = FakeClient(action_for())
        with self.assertRaisesRegex(Exception, "origem_mensagem_id"):
            execute_promotion(client, "pa-1", usuario="pablo", executar=True, confirmacao=expected_confirmation("pa-1"))

    def test_execute_rejects_same_source_message(self):
        client = FakeClient(action_for())
        with self.assertRaisesRegex(Exception, "nova mensagem"):
            execute_promotion(
                client,
                "pa-1",
                usuario="pablo",
                executar=True,
                confirmacao=expected_confirmation("pa-1"),
                origem_conversa_id="grupo-1",
                origem_mensagem_id="msg-original",
            )

    def test_execute_rejects_different_group(self):
        client = FakeClient(action_for())
        with self.assertRaisesRegex(Exception, "mesmo contexto/grupo"):
            execute_promotion(
                client,
                "pa-1",
                usuario="pablo",
                executar=True,
                confirmacao=expected_confirmation("pa-1"),
                origem_conversa_id="grupo-2",
                origem_mensagem_id="msg-nova",
            )

    def test_execute_rejects_missing_group_when_source_context_is_known(self):
        client = FakeClient(action_for())
        with self.assertRaisesRegex(Exception, "mesmo contexto/grupo"):
            execute_promotion(
                client,
                "pa-1",
                usuario="pablo",
                executar=True,
                confirmacao=expected_confirmation("pa-1"),
                origem_conversa_id=None,
                origem_mensagem_id="msg-nova",
            )
        self.assertEqual(client.operational_inserts, [])

    def test_execute_writes_operational_and_audit(self):
        client = FakeClient(action_for())
        result = execute_promotion(
            client,
            "pa-1",
            usuario="pablo",
            executar=True,
            confirmacao=expected_confirmation("pa-1"),
            origem_conversa_id="grupo-1",
            origem_mensagem_id="msg-nova",
        )
        self.assertTrue(result["executado"])
        self.assertEqual(client.operational_inserts[0][0], "compras")
        self.assertEqual(
            client.operational_inserts[0][2],
            purchase_idempotency_key("pa-1"),
        )
        self.assertEqual(result["idempotency_status"], "inserted")
        self.assertEqual(client.updates[0][0], "pending_actions")
        self.assertEqual(client.updates[0][2]["status"], "em_execucao")
        self.assertEqual(
            client.updates[0][2]["payload"]["idempotency"],
            {
                "key": purchase_idempotency_key("pa-1"),
                "state": "processing",
            },
        )
        self.assertEqual(client.updates[1][0], "operation_drafts")
        self.assertEqual(client.updates[2][2]["status"], "executado")
        self.assertEqual(client.audit_inserts[0][1]["tipo"], "promocao_operacional_executada")

    def test_lease_v1_uses_claim_and_completion_rpcs_without_pending_action_updates(self):
        action = action_for()
        action["promocao_controle_version"] = "lease-v1"
        client = FakeClient(action)
        result = execute_promotion(
            client,
            "pa-1",
            usuario="pablo",
            executar=True,
            confirmacao=expected_confirmation("pa-1"),
            origem_conversa_id="grupo-1",
            origem_mensagem_id="msg-nova",
        )
        self.assertTrue(result["executado"])
        self.assertEqual(
            [call[0] for call in client.rpc_calls],
            ["assumir_promocao_operacional", "concluir_promocao_operacional"],
        )
        self.assertEqual(
            client.rpc_calls[0][1],
            {
                "p_pending_action_id": "pa-1",
                "p_status_esperado": "aguardando_confirmacao",
                "p_executor": "pablo",
                "p_confirmacao_origem_conversa_id": "grupo-1",
                "p_confirmacao_origem_mensagem_id": "msg-nova",
                "p_lease_segundos": 300,
            },
        )
        self.assertEqual(
            client.rpc_calls[1][1]["p_lease_token"],
            "11111111-1111-1111-1111-111111111111",
        )
        self.assertEqual(client.rpc_calls[1][1]["p_fencing_token"], 7)
        self.assertEqual(client.rpc_calls[1][1]["p_status"], "executado")
        self.assertEqual(
            client.rpc_calls[1][1]["p_resultado"]["target_record_id"],
            "operacional-1",
        )
        self.assertFalse(any(table == "pending_actions" for table, _, _ in client.updates))

    def test_lease_v1_marks_http_failure_by_rpc_without_pending_action_update(self):
        action = action_for()
        action["promocao_controle_version"] = "lease-v1"
        client = FakeClient(action)
        client.fail_operational_insert = True
        client.operational_failure = ConfinexHTTPError("invalido", status=400)
        with self.assertRaisesRegex(Exception, "invalido"):
            execute_promotion(
                client,
                "pa-1",
                usuario="pablo",
                executar=True,
                confirmacao=expected_confirmation("pa-1"),
                origem_conversa_id="grupo-1",
                origem_mensagem_id="msg-nova",
            )
        self.assertEqual(client.rpc_calls[-1][0], "concluir_promocao_operacional")
        self.assertEqual(client.rpc_calls[-1][1]["p_status"], "erro")
        self.assertFalse(any(table == "pending_actions" for table, _, _ in client.updates))

    def test_lease_v1_finalizer_owns_draft_and_event_atomically(self):
        action = action_for()
        action["promocao_controle_version"] = "lease-v1"
        client = FakeClient(action)
        result = execute_promotion(
            client,
            "pa-1",
            usuario="pablo",
            executar=True,
            confirmacao=expected_confirmation("pa-1"),
            origem_conversa_id="grupo-1",
            origem_mensagem_id="msg-nova",
        )
        self.assertEqual(result["evento_id"], "evento-atomico-1")
        self.assertEqual(client.rpc_calls[-1][0], "concluir_promocao_operacional")
        self.assertEqual(client.rpc_calls[-1][1]["p_status"], "executado")
        self.assertEqual(
            client.rpc_calls[-1][1]["p_resultado"]["target_record_id"],
            "operacional-1",
        )
        self.assertFalse(any(table == "pending_actions" for table, _, _ in client.updates))
        self.assertFalse(any(table == "operation_drafts" for table, _, _ in client.updates))
        self.assertEqual(client.audit_inserts, [])

    def test_lease_v1_rejects_invalid_fencing_before_operational_insert(self):
        action = action_for()
        action["promocao_controle_version"] = "lease-v1"
        client = FakeClient(action)
        original_rpc = client.rpc

        def invalid_fencing(function, payload):
            if function == "assumir_promocao_operacional":
                return {
                    "assumida": True,
                    "pending_action_id": "pa-1",
                    "lease_token": "11111111-1111-1111-1111-111111111111",
                    "fencing_token": 0,
                }
            return original_rpc(function, payload)

        client.rpc = invalid_fencing
        with self.assertRaisesRegex(Exception, "fencing_token valido"):
            execute_promotion(
                client,
                "pa-1",
                usuario="pablo",
                executar=True,
                confirmacao=expected_confirmation("pa-1"),
                origem_conversa_id="grupo-1",
                origem_mensagem_id="msg-nova",
            )
        self.assertEqual(client.operational_inserts, [])

    def test_lease_v1_retries_exact_terminal_request_after_connection_loss(self):
        action = action_for()
        action["promocao_controle_version"] = "lease-v1"
        client = FakeClient(action)
        original_rpc = client.rpc
        failures = {"remaining": 1}

        def flaky_conclusion(function, payload):
            if function == "concluir_promocao_operacional" and failures["remaining"]:
                failures["remaining"] -= 1
                client.rpc_calls.append((function, payload))
                raise ConfinexConnectionError("resposta terminal perdida")
            return original_rpc(function, payload)

        client.rpc = flaky_conclusion
        result = execute_promotion(
            client,
            "pa-1",
            usuario="pablo",
            executar=True,
            confirmacao=expected_confirmation("pa-1"),
            origem_conversa_id="grupo-1",
            origem_mensagem_id="msg-nova",
        )
        self.assertTrue(result["executado"])
        conclusions = [
            payload for function, payload in client.rpc_calls
            if function == "concluir_promocao_operacional"
        ]
        self.assertEqual(len(conclusions), 2)
        self.assertEqual(conclusions[0], conclusions[1])
        self.assertEqual(len(client.operational_inserts), 1)

    def test_lease_v1_accepts_idempotent_retry_after_commit_response_loss(self):
        action = action_for()
        action["promocao_controle_version"] = "lease-v1"
        client = FakeClient(action)
        original_rpc = client.rpc
        committed = {"value": False}

        def commit_then_lose_response(function, payload):
            if function != "concluir_promocao_operacional":
                return original_rpc(function, payload)
            client.rpc_calls.append((function, payload))
            if not committed["value"]:
                committed["value"] = True
                raise ConfinexConnectionError("commit concluído, resposta perdida")
            return {
                "concluida": False,
                "repeticao_idempotente": True,
                "status": payload["p_status"],
            }

        client.rpc = commit_then_lose_response
        result = execute_promotion(
            client,
            "pa-1",
            usuario="pablo",
            executar=True,
            confirmacao=expected_confirmation("pa-1"),
            origem_conversa_id="grupo-1",
            origem_mensagem_id="msg-nova",
        )
        self.assertTrue(result["executado"])
        self.assertEqual(len(client.operational_inserts), 1)

    def test_missing_operational_identifier_is_uncertain_and_never_reinserted(self):
        action = action_for()
        action["promocao_controle_version"] = "lease-v1"
        client = FakeClient(action)
        client.operational_record = {"quantidade": 18}
        with self.assertRaisesRegex(Exception, "sem retornar identificação.*não repita"):
            execute_promotion(
                client,
                "pa-1",
                usuario="pablo",
                executar=True,
                confirmacao=expected_confirmation("pa-1"),
                origem_conversa_id="grupo-1",
                origem_mensagem_id="msg-nova",
            )
        self.assertEqual(len(client.operational_inserts), 1)
        self.assertEqual(client.rpc_calls[-1][1]["p_status"], "erro_pos_gravacao")

    def test_client_allows_only_closed_lease_rpc_payloads(self):
        self.assertIn("assumir_promocao_operacional", RPC_FUNCTIONS)
        self.assertIn("concluir_promocao_operacional", RPC_FUNCTIONS)
        self.assertIn("reconciliar_promocao_em_execucao", RPC_FUNCTIONS)
        client = ConfinexClient(
            env={},
            url="https://supabase.example.test",
            key="chave-de-teste",
            usar_ponte=False,
        )
        payload = {
            "p_pending_action_id": "pa-1",
            "p_status_esperado": "aguardando_confirmacao",
            "p_executor": "executor-teste",
            "p_confirmacao_origem_conversa_id": "grupo-1",
            "p_confirmacao_origem_mensagem_id": "mensagem-2",
            "p_lease_segundos": 300,
        }
        with patch(
            "confinex_client.urllib.request.urlopen",
            return_value=RpcResponse('{"assumida": true}'),
        ) as mocked:
            self.assertEqual(
                client.rpc("assumir_promocao_operacional", payload),
                {"assumida": True},
            )
        request = mocked.call_args.args[0]
        self.assertEqual(request.full_url, "https://supabase.example.test/rest/v1/rpc/assumir_promocao_operacional")
        self.assertEqual(request.data, b'{"p_pending_action_id": "pa-1", "p_status_esperado": "aguardando_confirmacao", "p_executor": "executor-teste", "p_confirmacao_origem_conversa_id": "grupo-1", "p_confirmacao_origem_mensagem_id": "mensagem-2", "p_lease_segundos": 300}')
        completion_payload = {
            "p_pending_action_id": "pa-1",
            "p_lease_token": "11111111-1111-1111-1111-111111111111",
            "p_fencing_token": 7,
            "p_status": "executado",
            "p_resultado": {"target_table": "compras"},
        }
        with patch(
            "confinex_client.urllib.request.urlopen",
            return_value=RpcResponse('{"concluida": true, "status": "executado"}'),
        ):
            self.assertEqual(
                client.rpc("concluir_promocao_operacional", completion_payload),
                {"concluida": True, "status": "executado"},
            )
        with patch("confinex_client.urllib.request.urlopen") as mocked:
            with self.assertRaisesRegex(Exception, "payload fechado"):
                client.rpc(
                    "concluir_promocao_operacional",
                    {**completion_payload, "p_injetado": True},
                )
            mocked.assert_not_called()
        recovery_payload = {
            "p_pending_action_id": "pa-1",
            "p_fencing_esperado": 7,
            "p_ator": "pablo",
            "p_motivo": "Recuperar resposta perdida depois do lease.",
        }
        with patch(
            "confinex_client.urllib.request.urlopen",
            return_value=RpcResponse('{"recuperada": true, "status": "executado"}'),
        ) as mocked:
            self.assertEqual(
                client.rpc("reconciliar_promocao_em_execucao", recovery_payload),
                {"recuperada": True, "status": "executado"},
            )
        request = mocked.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "https://supabase.example.test/rest/v1/rpc/reconciliar_promocao_em_execucao",
        )
        with patch("confinex_client.urllib.request.urlopen") as mocked:
            with self.assertRaisesRegex(Exception, "payload fechado"):
                client.rpc(
                    "reconciliar_promocao_em_execucao",
                    {**recovery_payload, "p_injetado": True},
                )
            mocked.assert_not_called()

    def test_execute_rejects_when_another_worker_claimed_action(self):
        client = FakeClient(action_for())
        client.claim_succeeds = False
        with self.assertRaisesRegex(Exception, "outra execucao"):
            execute_promotion(
                client, "pa-1", usuario="pablo", executar=True,
                confirmacao=expected_confirmation("pa-1"),
                origem_conversa_id="grupo-1", origem_mensagem_id="msg-nova",
            )
        self.assertEqual(client.operational_inserts, [])

    def test_http_4xx_before_insert_returns_action_to_error_queue(self):
        client = FakeClient(action_for())
        client.fail_operational_insert = True
        client.operational_failure = ConfinexHTTPError(
            "payload inválido simulado",
            status=400,
        )
        with self.assertRaisesRegex(Exception, "payload inválido"):
            execute_promotion(
                client, "pa-1", usuario="pablo", executar=True,
                confirmacao=expected_confirmation("pa-1"),
                origem_conversa_id="grupo-1", origem_mensagem_id="msg-nova",
            )
        self.assertEqual(client.updates[-1][2]["status"], "erro")
        self.assertEqual(
            client.updates[-1][2]["payload"]["idempotency"]["state"],
            "failed",
        )

    def test_timeout_is_uncertain_and_blocks_repeat(self):
        client = FakeClient(action_for())
        client.fail_operational_insert = True
        client.operational_failure = ConfinexConnectionError(
            "timeout simulado"
        )
        kwargs = {
            "usuario": "pablo",
            "executar": True,
            "confirmacao": expected_confirmation("pa-1"),
            "origem_conversa_id": "grupo-1",
            "origem_mensagem_id": "msg-nova",
        }
        with self.assertRaisesRegex(Exception, "incerto.*não repita"):
            execute_promotion(client, "pa-1", **kwargs)
        self.assertEqual(client.updates[-1][2]["status"], "erro_pos_gravacao")
        self.assertTrue(
            client.updates[-1][2]["resultado"]["requer_reconciliacao"]
        )
        self.assertEqual(
            client.updates[-1][2]["resultado"]["estado_idempotencia"],
            "uncertain",
        )
        with self.assertRaisesRegex(Exception, "status nao permite promover"):
            execute_promotion(client, "pa-1", **kwargs)
        self.assertEqual(client.operational_inserts, [])

    def test_unexpected_failure_is_uncertain_not_failed(self):
        client = FakeClient(action_for())
        client.fail_operational_insert = True
        with self.assertRaisesRegex(Exception, "resultado incerto"):
            execute_promotion(
                client, "pa-1", usuario="pablo", executar=True,
                confirmacao=expected_confirmation("pa-1"),
                origem_conversa_id="grupo-1", origem_mensagem_id="msg-nova",
            )
        self.assertEqual(client.updates[-1][2]["status"], "erro_pos_gravacao")

    def test_failure_after_insert_blocks_retry_and_preserves_record_id(self):
        client = FakeClient(action_for())
        client.fail_audit_insert = True
        with self.assertRaisesRegex(Exception, "nao repita"):
            execute_promotion(
                client, "pa-1", usuario="pablo", executar=True,
                confirmacao=expected_confirmation("pa-1"),
                origem_conversa_id="grupo-1", origem_mensagem_id="msg-nova",
            )
        self.assertEqual(len(client.operational_inserts), 1)
        self.assertEqual(client.updates[-1][2]["status"], "erro_pos_gravacao")
        self.assertEqual(client.updates[-1][2]["resultado"]["target_record_id"], "operacional-1")

    def _acao_em_recuperacao(self):
        action = action_for()
        action.update({
            "promocao_controle_version": "lease-v1",
            "status": "em_execucao",
            "promocao_fencing_token": 7,
        })
        return action

    def test_recuperacao_manual_chama_somente_rpc_e_nao_repete_insert(self):
        client = FakeClient(self._acao_em_recuperacao())
        resultado = reconciliar_promocao_em_execucao(
            client,
            "pa-1",
            fencing_esperado=7,
            ator="pablo",
            motivo="Recuperar resposta perdida depois do prazo do lease.",
            confirmacao=expected_recovery_confirmation("pa-1", 7),
        )
        self.assertTrue(resultado["recuperacao_solicitada"])
        self.assertTrue(resultado["nenhum_insert_executado"])
        self.assertEqual(resultado["status"], "executado")
        self.assertEqual(client.operational_inserts, [])
        self.assertEqual(client.updates, [])
        self.assertEqual(client.audit_inserts, [])
        self.assertEqual(client.rpc_calls, [(
            "reconciliar_promocao_em_execucao",
            {
                "p_pending_action_id": "pa-1",
                "p_fencing_esperado": 7,
                "p_ator": "pablo",
                "p_motivo": "Recuperar resposta perdida depois do prazo do lease.",
            },
        )])

    def test_recuperacao_exige_confirmacao_e_fencing_exato_antes_da_rpc(self):
        client = FakeClient(self._acao_em_recuperacao())
        with self.assertRaisesRegex(Exception, "confirmacao invalida"):
            reconciliar_promocao_em_execucao(
                client, "pa-1", fencing_esperado=7, ator="pablo",
                motivo="Recuperar resposta perdida.", confirmacao="sim",
            )
        self.assertEqual(client.rpc_calls, [])
        with self.assertRaisesRegex(Exception, "fencing da promocao"):
            reconciliar_promocao_em_execucao(
                client, "pa-1", fencing_esperado=8, ator="pablo",
                motivo="Recuperar resposta perdida.",
                confirmacao=expected_recovery_confirmation("pa-1", 8),
            )
        self.assertEqual(client.rpc_calls, [])
        self.assertEqual(client.operational_inserts, [])

    def test_perda_de_resposta_da_recuperacao_nao_faz_retry_automatico_nem_insert(self):
        client = FakeClient(self._acao_em_recuperacao())
        original_rpc = client.rpc
        chamadas = {"total": 0}

        def resposta_perdida(function, payload):
            chamadas["total"] += 1
            if chamadas["total"] == 1:
                client.rpc_calls.append((function, payload))
                raise ConfinexConnectionError("commit pode ter ocorrido")
            return {
                "recuperada": False,
                "repeticao_idempotente": True,
                "status": "executado",
                "evento_id": "evento-recuperacao-1",
            }

        client.rpc = resposta_perdida
        kwargs = {
            "fencing_esperado": 7,
            "ator": "pablo",
            "motivo": "Recuperar resposta perdida depois do prazo do lease.",
            "confirmacao": expected_recovery_confirmation("pa-1", 7),
        }
        with self.assertRaisesRegex(Exception, "não será repetida automaticamente"):
            reconciliar_promocao_em_execucao(client, "pa-1", **kwargs)
        self.assertEqual(chamadas["total"], 1)
        self.assertEqual(len(client.rpc_calls), 1)
        self.assertEqual(client.operational_inserts, [])

        # O operador pode confirmar de novo o mesmo pedido. A RPC é
        # idempotente e a segunda chamada ainda não executa nenhum INSERT.
        client.rpc = resposta_perdida
        action = client.action
        action["status"] = "executado"
        action.pop("promocao_fencing_token")
        action["promocao_resultado_fencing_token"] = 7
        resultado = reconciliar_promocao_em_execucao(client, "pa-1", **kwargs)
        self.assertTrue(resultado["repeticao_idempotente"])
        self.assertIsNone(resultado["target_record_id"])
        self.assertTrue(resultado["vinculo_operacional_confirmado"])
        self.assertEqual(chamadas["total"], 2)
        self.assertEqual(client.operational_inserts, [])


if __name__ == "__main__":
    unittest.main()
