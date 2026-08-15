import argparse
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from confinex_client import ConfinexError, ConfinexHTTPError
from ofertas_gado import montar_oferta, registrar_oferta

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase/migrations/202608150003_crm_ofertas_gado.sql"


def argumentos(**alteracoes):
    valores = {
        "fornecedor_id": None,
        "corretor_id": None,
        "sexo": "nao_informado",
        "categoria": None,
        "quantidade": None,
        "peso_medio_kg": None,
        "preco_arroba": None,
        "modalidade_preco": "arroba",
        "municipio": None,
        "uf": None,
        "origem_canal": "manual",
        "origem_conversa_id": None,
        "origem_mensagem_id": None,
        "observacoes": None,
    }
    valores.update(alteracoes)
    return argparse.Namespace(**valores)


class ClienteMemoria:
    def __init__(self):
        self.registros = []
        self.inserts = 0

    def select(self, table, **params):
        assert table == "ofertas_gado"
        canal = params["origem_canal"].removeprefix("eq.")
        conversa = params["origem_conversa_id"].removeprefix("eq.")
        mensagem = params["origem_mensagem_id"].removeprefix("eq.")
        return [r for r in self.registros if (
            r["origem_canal"], r["origem_conversa_id"], r["origem_mensagem_id"]
        ) == (canal, conversa, mensagem)]

    def insert(self, table, payload):
        assert table == "ofertas_gado"
        self.inserts += 1
        registro = {"id": f"oferta-{self.inserts}", **payload}
        self.registros.append(registro)
        return registro


class OfertasGadoTests(unittest.TestCase):
    def test_oferta_incompleta_lista_cinco_perguntas(self):
        oferta, perguntas = montar_oferta(argumentos())
        self.assertEqual(oferta["status"], "incompleta")
        self.assertEqual(
            oferta["campos_faltantes"],
            ["preco_arroba", "quantidade", "sexo", "peso_medio_kg", "localizacao"],
        )
        self.assertEqual(len(perguntas), 5)

    def test_oferta_completa_fica_nova(self):
        oferta, perguntas = montar_oferta(argumentos(
            sexo="femea", quantidade=20, peso_medio_kg=442.45,
            preco_arroba=285, municipio="Exemplo", uf="mg",
        ))
        self.assertEqual(oferta["status"], "nova")
        self.assertEqual(oferta["uf"], "MG")
        self.assertEqual(perguntas, [])

    def test_registro_exige_identidade_de_origem(self):
        oferta, _ = montar_oferta(argumentos())
        with self.assertRaisesRegex(ConfinexError, "exige canal"):
            registrar_oferta(ClienteMemoria(), oferta)

    def test_repeticao_da_mesma_mensagem_e_idempotente(self):
        oferta, _ = montar_oferta(argumentos(
            origem_canal="whatsapp",
            origem_conversa_id="conversa-teste",
            origem_mensagem_id="mensagem-teste",
        ))
        cliente = ClienteMemoria()
        primeiro = registrar_oferta(cliente, oferta)
        segundo = registrar_oferta(cliente, oferta)
        self.assertEqual(primeiro[0], "registrada")
        self.assertEqual(segundo[0], "duplicada")
        self.assertEqual(primeiro[1]["id"], segundo[1]["id"])
        self.assertEqual(cliente.inserts, 1)

    def test_mesma_mensagem_com_dados_diferentes_e_bloqueada(self):
        oferta, _ = montar_oferta(argumentos(
            origem_canal="whatsapp",
            origem_conversa_id="conversa-teste",
            origem_mensagem_id="mensagem-teste",
            quantidade=20,
        ))
        cliente = ClienteMemoria()
        registrar_oferta(cliente, oferta)
        alterada = {**oferta, "quantidade": 21}
        with self.assertRaisesRegex(ConfinexError, "dados diferentes"):
            registrar_oferta(cliente, alterada)
        self.assertEqual(cliente.inserts, 1)


class MigracaoCrmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = MIGRATION.read_text(encoding="utf-8")

    def test_cria_quatro_tabelas_sem_seed(self):
        for tabela in ("ofertas_gado", "negociacoes_gado", "interacoes_crm", "crm_followups"):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS public.{tabela}", self.sql)
        self.assertNotRegex(self.sql, r"(?im)^\s*insert\s+into")

    def test_origem_de_mensagem_e_unica(self):
        self.assertIn("ofertas_gado_origem_mensagem_unique", self.sql)
        self.assertIn("WHERE origem_mensagem_id IS NOT NULL", self.sql)

    def test_delete_e_truncate_nao_sao_concedidos(self):
        self.assertRegex(self.sql, r"REVOKE DELETE, TRUNCATE ON public\.ofertas_gado FROM authenticated")
        self.assertNotRegex(self.sql, r"GRANT[^;]*DELETE")


if __name__ == "__main__":
    unittest.main()
