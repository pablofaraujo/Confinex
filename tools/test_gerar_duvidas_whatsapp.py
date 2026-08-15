import unittest
from datetime import date

from tools.gerar_duvidas_whatsapp import (
    acerto_final,
    combinar_duvidas,
    gerar_pendencias_acertos,
)


class ClienteFalso:
    def __init__(self, tabelas):
        self.tabelas = tabelas

    def select(self, tabela, **_params):
        return self.tabelas.get(tabela, [])


class GerarDuvidasWhatsappTest(unittest.TestCase):
    def setUp(self):
        self.tabelas = {
            "operacoes": [{
                "id": "op-1", "codigo": "CF-26-009",
                "confinamento_id": "conf-1", "status": "em_confinamento",
            }],
            "abates": [{
                "id": "ab-1", "operacao_id": "op-1", "data_abate": "2026-08-07",
                "quantidade": 141, "romaneio": None, "frigorifico": None,
            }],
            "acertos": [],
            "confinamentos": [{"id": "conf-1", "nome": "CSAP"}],
            "confinamento_contatos": [{
                "confinamento_id": "conf-1", "contato_id": "ct-1",
                "papel": "administrativo", "principal": True,
            }],
            "contatos": [{
                "id": "ct-1", "nome": "Marcia", "telefone": None,
                "whatsapp": "+5517999991111",
            }],
        }

    def test_gera_acerto_ausente_com_contato_e_termos(self):
        itens = gerar_pendencias_acertos(
            ClienteFalso(self.tabelas), hoje=date(2026, 8, 15),
        )
        self.assertEqual(len(itens), 1)
        self.assertEqual(itens[0]["operacao_codigo"], "CF-26-009")
        self.assertEqual(itens[0]["quantidade"], 141)
        self.assertEqual(itens[0]["contatos"][0]["nome"], "Marcia")
        self.assertIn("141", itens[0]["termos_busca"])
        self.assertIn("acerto", itens[0]["termos_busca"])

    def test_nao_gera_quando_acerto_esta_final(self):
        self.tabelas["acertos"] = [{
            "operacao_id": "op-1", "status": "recebido",
            "documento_id": "doc-1", "data_recebimento": "2026-08-13",
            "valor_recebido": 1,
        }]
        self.assertEqual(gerar_pendencias_acertos(
            ClienteFalso(self.tabelas), hoje=date(2026, 8, 15),
        ), [])

    def test_acerto_so_e_final_com_evidencia_suficiente(self):
        self.assertFalse(acerto_final({"status": "aguardando"}))
        self.assertTrue(acerto_final({"status": "conciliado"}))

    def test_combina_sem_duplicar_codigo(self):
        resultado = combinar_duvidas(
            [{"codigo": "A", "fonte": "base"}],
            [{"codigo": "A", "fonte": "automatico"}, {"codigo": "B"}],
        )
        self.assertEqual([item["codigo"] for item in resultado], ["A", "B"])
        self.assertEqual(resultado[0]["fonte"], "automatico")

    def test_fonte_nao_escreve_no_supabase_nem_envia_whatsapp(self):
        from pathlib import Path
        fonte = Path(__file__).with_name("gerar_duvidas_whatsapp.py").read_text()
        for proibido in (".insert(", ".update(", ".delete(", "wacli send", "openclaw message"):
            self.assertNotIn(proibido, fonte.lower())


if __name__ == "__main__":
    unittest.main()
