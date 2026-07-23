from __future__ import annotations

import unittest

from validar_memorias import build_report, problemas


def memoria(**changes):
    base = {
        "id": "m1",
        "tipo": "regra",
        "escopo": "confinamento",
        "agente_origem": "juan",
        "assunto": "Confirmação operacional",
        "importancia": 3,
        "validade_inicio": "2026-07-23",
        "fonte_tipo": "confirmacao_pablo",
        "status_confirmacao": "confirmada",
        "texto": "Nunca promover sem confirmação.",
        "dados": {},
    }
    return {**base, **changes}


class FakeClient:
    def select(self, table, **_kwargs):
        assert table == "memorias_agentes"
        return [
            memoria(),
            memoria(
                id="m2",
                tipo="contexto",
                texto="Compra de 20 cabeças, peso 500 kg",
                status_confirmacao="pendente",
            ),
        ]


class MemoriasTests(unittest.TestCase):
    def test_aceita_apenas_conhecimento_reutilizavel(self):
        self.assertEqual(problemas(memoria()), [])
        issues = problemas(memoria(tipo="contexto"))
        self.assertIn("tipo não representa conhecimento reutilizável", issues)

    def test_detecta_dado_operacional_com_numero(self):
        issues = problemas(memoria(texto="Venda de 10 cabeças, peso 500 kg"))
        self.assertIn("possível dado operacional dentro da memória", issues)

    def test_relatorio_nao_expoe_conteudo_nem_escreve(self):
        report = build_report(FakeClient())
        self.assertEqual(report["total"], 2)
        self.assertEqual(report["conformes"], 1)
        self.assertEqual(report["para_revisao"], 1)
        self.assertEqual(report["escritas_realizadas"], 0)
        self.assertNotIn("Compra de 20", str(report))


if __name__ == "__main__":
    unittest.main()
