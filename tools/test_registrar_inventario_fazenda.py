import copy
import unittest
from pathlib import Path

from tools.registrar_inventario_fazenda import (
    InventarioError,
    executar_plano,
    frase_confirmacao,
    preparar_plano,
)


def documento_valido():
    return {
        "unidade_codigo": "fazenda_ametista",
        "data_referencia": "2026-08-17",
        "fonte": "contagem física confirmada",
        "criado_por": "responsável",
        "cabecas_total": 7,
        "peso_total_kg": 1900,
        "itens": [
            {"local_nome": "Local A", "categoria": "boi", "sexo": "macho", "cabecas": 5, "peso_medio_kg": 300},
            {"local_nome": "Local A", "categoria": "bezerro", "cabecas": 2, "peso_medio_kg": 200},
        ],
    }


class ClienteFalso:
    def __init__(self, existentes=None, falhar=False):
        self.existentes = list(existentes or [])
        self.falhar = falhar
        self.posts = 0

    def buscar(self, _chaves):
        return copy.deepcopy(self.existentes)

    def inserir_lote(self, itens):
        self.posts += 1
        if self.falhar:
            raise TimeoutError("simulado")
        self.existentes = [{**item, "id": f"id-{i}", "peso_total_kg": str(item["cabecas"] * float(item["peso_medio_kg"]))} for i, item in enumerate(itens)]
        return copy.deepcopy(self.existentes)


class TestRegistrarInventarioFazenda(unittest.TestCase):
    def test_prepara_totais_e_chaves_idempotentes(self):
        plano = preparar_plano(documento_valido())
        repetido = preparar_plano(documento_valido())
        self.assertEqual(7, plano["cabecas_total"])
        self.assertEqual("1900", plano["peso_total_kg"])
        self.assertEqual("271.429", plano["peso_medio_kg"])
        self.assertEqual(plano["plano_id"], repetido["plano_id"])
        self.assertEqual(
            [x["idempotency_key"] for x in plano["itens"]],
            [x["idempotency_key"] for x in repetido["itens"]],
        )

    def test_rejeita_total_divergente(self):
        documento = documento_valido()
        documento["cabecas_total"] = 8
        with self.assertRaisesRegex(InventarioError, "total de cabeças"):
            preparar_plano(documento)

    def test_rejeita_item_repetido(self):
        documento = documento_valido()
        documento["itens"].append(copy.deepcopy(documento["itens"][0]))
        documento.pop("cabecas_total")
        documento.pop("peso_total_kg")
        with self.assertRaisesRegex(InventarioError, "repetido"):
            preparar_plano(documento)

    def test_insercao_usa_um_post(self):
        plano = preparar_plano(documento_valido())
        cliente = ClienteFalso()
        resultado = executar_plano(cliente, plano)
        self.assertEqual("success", resultado["estado"])
        self.assertEqual(2, resultado["registros"])
        self.assertEqual(1, cliente.posts)

    def test_reexecucao_identica_retorna_duplicate(self):
        plano = preparar_plano(documento_valido())
        cliente = ClienteFalso()
        executar_plano(cliente, plano)
        resultado = executar_plano(cliente, plano)
        self.assertEqual("duplicate", resultado["estado"])
        self.assertFalse(resultado["post_executado"])
        self.assertEqual(1, cliente.posts)

    def test_parcial_existente_bloqueia(self):
        plano = preparar_plano(documento_valido())
        cliente = ClienteFalso([copy.deepcopy(plano["itens"][0])])
        with self.assertRaisesRegex(InventarioError, "parcialmente"):
            executar_plano(cliente, plano)
        self.assertEqual(0, cliente.posts)

    def test_chave_com_conteudo_diferente_bloqueia(self):
        plano = preparar_plano(documento_valido())
        divergente = copy.deepcopy(plano["itens"])
        divergente[0]["cabecas"] = 99
        cliente = ClienteFalso(divergente)
        with self.assertRaisesRegex(InventarioError, "conteúdo diferente"):
            executar_plano(cliente, plano)

    def test_timeout_sem_reconciliacao_fica_incerto(self):
        plano = preparar_plano(documento_valido())
        cliente = ClienteFalso(falhar=True)
        with self.assertRaisesRegex(InventarioError, "não repetir"):
            executar_plano(cliente, plano)
        self.assertEqual(1, cliente.posts)

    def test_frase_de_confirmacao_depende_do_plano(self):
        plano = preparar_plano(documento_valido())
        self.assertEqual(
            f"REGISTRAR INVENTARIO FAZENDA {plano['plano_id']}",
            frase_confirmacao(plano),
        )


class TestMigracaoInventariosFazenda(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = Path("supabase/migrations/202608180001_inventarios_fazenda.sql").read_text(encoding="utf-8")

    def test_migracao_e_aditiva(self):
        self.assertIn("CREATE TABLE IF NOT EXISTS public.inventarios_fazenda", self.sql)
        self.assertIn("CREATE OR REPLACE VIEW public.v_inventarios_fazenda_resumo", self.sql)
        for proibido in ("TRUNCATE ", "DELETE FROM ", "DROP TABLE "):
            self.assertNotIn(proibido, self.sql.upper())

    def test_peso_total_e_calculado(self):
        self.assertIn("GENERATED ALWAYS AS (cabecas * peso_medio_kg) STORED", self.sql)

    def test_rls_e_permissoes(self):
        self.assertIn("ENABLE ROW LEVEL SECURITY", self.sql)
        self.assertIn("FOR SELECT TO authenticated", self.sql)
        self.assertIn("GRANT SELECT, INSERT, UPDATE ON public.inventarios_fazenda TO service_role", self.sql)
        self.assertNotIn("GRANT DELETE", self.sql.upper())

    def test_idempotencia(self):
        self.assertIn("UNIQUE (idempotency_key)", self.sql)
        self.assertIn("inventarios_fazenda_item_unico_idx", self.sql)


if __name__ == "__main__":
    unittest.main()
