from __future__ import annotations

import unittest

from planejar_handoff import build_plan, classify


class FakeClient:
    def select(self, table, **_kwargs):
        assert table == "contexto_handoff"
        return [{
            "id": "h1",
            "status": "aberto",
            "titulo": "Continuar conferência",
            "data": "Peso do lote ainda precisa ser confirmado",
            "pendencias": [],
            "proximos_passos": "Verificar a origem",
        }]


class HandoffTests(unittest.TestCase):
    def test_separa_operacao_memoria_evento_e_continuidade(self):
        self.assertIn("dado_operacional", classify("Peso do lote: 10 kg"))
        self.assertIn("memoria_permanente", classify("Regra: nunca promover sem confirmar"))
        self.assertIn("evento", classify("Revisão rejeitada"))
        self.assertIn("continuidade_temporaria", classify("Próximo passo: verificar"))

    def test_plano_e_somente_leitura_e_nao_expoe_conteudo(self):
        plan = build_plan(FakeClient())
        self.assertEqual(plan["handoffs_abertos"], 1)
        self.assertEqual(plan["escritas_realizadas"], 0)
        self.assertFalse(plan["conteudo_exposto"])
        serialized = str(plan)
        self.assertNotIn("Peso do lote", serialized)
        self.assertFalse(plan["planos"][0]["encerramento_permitido"])


if __name__ == "__main__":
    unittest.main()
