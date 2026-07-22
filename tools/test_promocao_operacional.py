import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from promocao_operacional import clean_record, expected_confirmation, execute_promotion, validate_action


class FakeClient:
    def __init__(self, action):
        self.action = action
        self.operational_inserts = []
        self.audit_inserts = []
        self.updates = []

    def select(self, table, **params):
        if table == "pending_actions" and params.get("id") == f"eq.{self.action['id']}":
            return [self.action]
        return []

    def insert_operational(self, table, payload):
        self.operational_inserts.append((table, payload))
        return {"id": "operacional-1", **payload}

    def insert(self, table, payload):
        self.audit_inserts.append((table, payload))
        return {"id": "evento-1", **payload}

    def update(self, table, filters, payload):
        self.updates.append((table, filters, payload))
        return [{**payload, "id": filters["id"].replace("eq.", "")}]


def action_for(target="compras"):
    return {
        "id": "pa-1",
        "acao_tipo": "promover_revisao_operacional",
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

    def test_default_mode_only_previews(self):
        client = FakeClient(action_for())
        result = execute_promotion(client, "pa-1", usuario="pablo", executar=False, confirmacao=None)
        self.assertFalse(result["executado"])
        self.assertEqual(client.operational_inserts, [])
        self.assertEqual(result["confirmacao_esperada"], "PROMOVER pa-1")

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
        with self.assertRaisesRegex(Exception, "contexto/grupo diferente"):
            execute_promotion(
                client,
                "pa-1",
                usuario="pablo",
                executar=True,
                confirmacao=expected_confirmation("pa-1"),
                origem_conversa_id="grupo-2",
                origem_mensagem_id="msg-nova",
            )

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
        self.assertEqual(client.updates[0][0], "pending_actions")
        self.assertEqual(client.updates[1][0], "operation_drafts")
        self.assertEqual(client.audit_inserts[0][1]["tipo"], "promocao_operacional_executada")


if __name__ == "__main__":
    unittest.main()
