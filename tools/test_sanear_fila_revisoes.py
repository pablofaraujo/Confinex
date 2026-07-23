from __future__ import annotations

import unittest

from sanear_fila_revisoes import build_plan, execute_plan, match_score


class FakeClient:
    def __init__(self):
        self.rows = {
            "operation_drafts": [
                {
                    "id": "d1",
                    "status": "em_revisao",
                    "pending_action_id": None,
                    "tipo_operacao": "compra_boi_balanca_revisao",
                    "entidade_final_tipo": "compras",
                    "codigo_sugerido": "CF-1",
                    "dados_extraidos": {
                        "origem_canal": "telegram",
                        "origem_conversa_id": "-100",
                        "origem_mensagem_id": "m1",
                    },
                    "contexto_canonico": "telegram:grupo:-100",
                    "contexto_nome": "Boi Balança",
                    "origem_canal": "telegram",
                    "origem_conversa_id": "-100",
                    "origem_mensagem_id": "m1",
                },
                {
                    "id": "d2",
                    "status": "em_revisao",
                    "pending_action_id": None,
                    "tipo_operacao": "compra_confinamento",
                    "entidade_final_tipo": "compras",
                    "codigo_sugerido": "CF-2",
                    "dados_extraidos": {},
                    "contexto_canonico": "telegram:grupo:-200",
                    "contexto_nome": "Confinamento",
                    "origem_canal": "telegram",
                    "origem_conversa_id": "-200",
                    "origem_mensagem_id": "m2",
                },
                {
                    "id": "d3",
                    "status": "cancelado",
                    "pending_action_id": None,
                    "tipo_operacao": "compra_cancelada",
                    "dados_extraidos": {},
                    "origem_canal": "telegram",
                    "origem_conversa_id": "-100",
                    "origem_mensagem_id": "m3",
                },
            ],
            "pending_actions": [
                {
                    "id": "p1",
                    "status": "em_revisao",
                    "acao_tipo": "revisar_compra",
                    "entidade_tipo": "compras",
                    "entidade_codigo": "CF-1",
                    "payload": {"dados_extraidos": {"origem_mensagem_id": "m1"}},
                    "canal": "telegram",
                    "conversa_id": "-100",
                    "mensagem_id": "m1",
                    "contexto_canonico": "telegram:grupo:-100",
                    "contexto_nome": "Boi Balança",
                },
                {
                    "id": "p2",
                    "status": "em_revisao",
                    "acao_tipo": "revisar_compra",
                    "entidade_tipo": "compras",
                    "payload": {},
                    "canal": "telegram",
                    "conversa_id": "-200",
                    "mensagem_id": "m2",
                    "contexto_canonico": "telegram:grupo:-200",
                    "contexto_nome": "Confinamento",
                },
                {
                    "id": "p3",
                    "status": "em_revisao",
                    "acao_tipo": "revisar_compra",
                    "entidade_tipo": "compras",
                    "payload": {},
                    "canal": "telegram",
                    "conversa_id": "-200",
                    "mensagem_id": "m2",
                    "contexto_canonico": "telegram:grupo:-200",
                    "contexto_nome": "Confinamento",
                },
            ],
            "eventos": [
                {"id": "e1", "entidade_tipo": "operation_draft", "entidade_id": "d1"},
                {"id": "e2", "entidade_tipo": "pending_action", "entidade_id": "p-inexistente"},
            ],
        }
        self.updates = []

    def select(self, table, **_params):
        return list(self.rows[table])

    def update(self, table, filters, payload):
        self.updates.append((table, filters, payload))
        if table != "operation_drafts":
            return []
        for row in self.rows[table]:
            if row["id"] == filters["id"][3:] and row.get("pending_action_id") is None:
                row.update(payload)
                return [row]
        return []


class SanearFilaRevisoesTests(unittest.TestCase):
    def test_dry_run_planeja_sem_escrever(self):
        client = FakeClient()
        plan = build_plan(client)
        self.assertEqual(plan["modo"], "dry-run")
        self.assertEqual(plan["escritas_realizadas"], 0)
        self.assertEqual(client.updates, [])
        self.assertEqual(len(plan["vinculos_propostos"]), 1)
        self.assertEqual(plan["vinculos_propostos"][0]["draft_id"], "d1")
        self.assertEqual(len(plan["ambiguos_preservados"]), 1)
        self.assertEqual(plan["eventos_com_referencia_quebrada"][0]["id"], "e2")

    def test_score_exige_mesma_origem_e_familia(self):
        client = FakeClient()
        score, reasons = match_score(client.rows["operation_drafts"][0], client.rows["pending_actions"][0])
        self.assertGreaterEqual(score, 8)
        self.assertIn("mesma mensagem", reasons)
        self.assertIn("mesma família operacional", reasons)

    def test_execucao_exige_confirmacao_forte_e_nao_toca_operacional(self):
        client = FakeClient()
        plan = build_plan(client)
        with self.assertRaisesRegex(Exception, "use exatamente"):
            execute_plan(client, plan, "SANEAR FILA errado")
        result = execute_plan(client, plan, f"SANEAR FILA {plan['plano_id']}")
        self.assertEqual(result["modo"], "executado")
        self.assertEqual(result["escritas_realizadas"], 1)
        self.assertEqual(result["tabelas_operacionais_alteradas"], 0)
        self.assertEqual(client.updates[0][0], "operation_drafts")
        self.assertEqual(client.updates[0][2], {"pending_action_id": "p1"})

    def test_limite_restringe_quantidade_executada(self):
        client = FakeClient()
        plan = build_plan(client)
        result = execute_plan(client, plan, f"SANEAR FILA {plan['plano_id']}", limit=0)
        self.assertEqual(result["escritas_realizadas"], 0)
        self.assertEqual(client.updates, [])


if __name__ == "__main__":
    unittest.main()
