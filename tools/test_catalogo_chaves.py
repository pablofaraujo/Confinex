import copy
from datetime import date, datetime
from decimal import Decimal
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from catalogo_chaves import perfilar_relacao, perfilar_tabela


class CatalogoChavesTests(unittest.TestCase):
    def test_campo_conta_nulos_sem_perder_zero_false_e_tipos(self):
        registros = [
            {"codigo": "A", "valor": 0},
            {"codigo": "B", "valor": False},
            {"codigo": "C", "valor": None},
            {"codigo": "D", "valor": "  "},
            {"codigo": "E", "valor": Decimal("1.0")},
        ]
        perfil = perfilar_tabela(registros, ["codigo", "valor"])
        campo = {item["campo"]: item for item in perfil["campos"]}["valor"]
        self.assertEqual(campo["preenchidos"], 3)
        self.assertEqual(campo["nulos"], 2)
        self.assertEqual(campo["distintos"], 3)
        self.assertEqual(campo["grupos_duplicados"], 0)
        self.assertEqual(campo["tipos"], ["bool", "numero"])
        self.assertFalse(campo["candidata_unica_na_amostra"])

    def test_igualdade_numerica_exata_e_tipos_separados(self):
        registros = [
            {"valor": 1},
            {"valor": 1.0},
            {"valor": Decimal("1.00")},
            {"valor": "1"},
            {"valor": True},
            {"valor": 0.1},
            {"valor": Decimal("0.1")},
        ]
        campo = perfilar_tabela(registros, ["valor"])["campos"][0]
        self.assertEqual(campo["distintos"], 5)
        self.assertEqual(campo["grupos_duplicados"], 1)
        self.assertEqual(campo["repeticoes_excedentes"], 2)
        self.assertEqual(campo["tipos"], ["bool", "numero", "texto"])

    def test_chave_composta_e_leading_zero_sao_exatos(self):
        registros = [
            {"fazenda": "A", "brinco": "001"},
            {"fazenda": "A", "brinco": "1"},
            {"fazenda": "A", "brinco": "001"},
            {"fazenda": "B"},
        ]
        perfil = perfilar_tabela(
            registros,
            ["fazenda", "brinco"],
            [{"nome": "fazenda_brinco", "campos": ["fazenda", "brinco"]}],
        )
        chave = perfil["chaves"][0]
        self.assertEqual(chave["completos"], 3)
        self.assertEqual(chave["incompletos"], 1)
        self.assertEqual(chave["distintos"], 2)
        self.assertEqual(chave["grupos_duplicados"], 1)
        self.assertEqual(chave["repeticoes_excedentes"], 1)
        self.assertFalse(chave["candidata_unica_na_amostra"])

    def test_normalizacao_aponta_colisao_sem_mudar_unicidade_exata(self):
        registros = [{"codigo": "Ａ"}, {"codigo": " a "}, {"codigo": "b"}]
        campo = perfilar_tabela(
            registros,
            ["codigo"],
            [{"nome": "codigo", "campos": ["codigo"]}],
        )
        self.assertEqual(campo["campos"][0]["distintos"], 3)
        self.assertTrue(campo["campos"][0]["candidata_unica_na_amostra"])
        self.assertEqual(campo["chaves"][0]["colisoes_normalizacao"], 1)

    def test_objeto_tem_comparacao_canonica_mas_nao_e_chave_simples(self):
        registros = [
            {"payload": {"b": 2, "a": [1, False]}},
            {"payload": {"a": [1, False], "b": 2}},
            {"payload": {"a": [1, True]}},
        ]
        perfil = perfilar_tabela(
            registros,
            ["payload"],
            [{"nome": "payload", "campos": ["payload"]}],
        )
        self.assertEqual(perfil["campos"][0]["distintos"], 2)
        self.assertEqual(perfil["campos"][0]["grupos_duplicados"], 1)
        self.assertFalse(perfil["campos"][0]["candidata_unica_na_amostra"])
        self.assertFalse(perfil["chaves"][0]["candidata_unica_na_amostra"])

    def test_vazia_nao_atesta_unicidade(self):
        perfil = perfilar_tabela(
            [], ["codigo"], [{"nome": "codigo", "campos": ["codigo"]}]
        )
        self.assertEqual(perfil["registros"], 0)
        self.assertFalse(perfil["campos"][0]["candidata_unica_na_amostra"])
        self.assertFalse(perfil["chaves"][0]["candidata_unica_na_amostra"])

    def test_coluna_de_chave_inexistente_falha_sem_ecoa_valor(self):
        with self.assertRaises(ValueError) as contexto:
            perfilar_tabela(
                [{"codigo": "segredo"}],
                ["codigo"],
                [{"nome": "fantasma", "campos": ["fantasma"]}],
            )
        self.assertNotIn("segredo", str(contexto.exception))

    def test_configuracao_invalida_e_entrada_preservada(self):
        registros = [{"codigo": "A", "n": 1}, {"codigo": "B", "n": None}]
        antes = copy.deepcopy(registros)
        with self.assertRaises(ValueError):
            perfilar_tabela(registros, ["codigo"], [{"nome": "x", "campos": ["n"]}])
        self.assertEqual(registros, antes)
        self.assertEqual(
            perfilar_tabela(registros, ["codigo"])["registros"], len(registros)
        )

    def test_datas_sao_tipo_separado_e_nao_viram_texto(self):
        registros = [
            {"quando": date(2026, 1, 1)},
            {"quando": datetime(2026, 1, 1)},
            {"quando": "2026-01-01"},
        ]
        campo = perfilar_tabela(registros, ["quando"])["campos"][0]
        self.assertEqual(campo["distintos"], 3)
        self.assertEqual(campo["tipos"], ["texto", "data"])

    def test_relacao_orfaos_incompletos_ambiguidade_e_n_n(self):
        origem = [
            {"pai": "A"}, {"pai": "A"}, {"pai": "B"}, {"pai": None}, {"pai": "Z"}
        ]
        destino = [
            {"id": "A"}, {"id": "A"}, {"id": "B"}, {"id": "C"}, {"id": "C"},
        ]
        perfil = perfilar_relacao(origem, ["pai"], destino, ["id"])
        self.assertEqual(perfil["registros_origem"], 5)
        self.assertEqual(perfil["incompletos_origem"], 1)
        self.assertEqual(perfil["incompletos_destino"], 0)
        self.assertEqual(perfil["correspondentes"], 3)
        self.assertEqual(perfil["orfaos"], 1)
        self.assertEqual(perfil["grupos_ambiguos_destino"], 2)
        self.assertEqual(perfil["cardinalidade_observada"], "N:N")
        self.assertFalse(perfil["fk_valida_na_amostra"])

    def test_relacao_cardinalidade_n_1_1_n_e_sem_correspondencia(self):
        destino = [{"id": "A"}, {"id": "B"}]
        self.assertEqual(
            perfilar_relacao(
                [{"pai": "A"}], ["pai"], destino, ["id"]
            )["cardinalidade_observada"],
            "1:1",
        )
        self.assertEqual(
            perfilar_relacao(
                [{"pai": "A"}, {"pai": "A"}], ["pai"], destino, ["id"]
            )["cardinalidade_observada"],
            "N:1",
        )
        self.assertEqual(
            perfilar_relacao(
                [{"pai": "A"}], ["pai"], [{"id": "A"}, {"id": "A"}], ["id"]
            )["cardinalidade_observada"],
            "1:N",
        )
        self.assertEqual(
            perfilar_relacao(
                [{"pai": "X"}], ["pai"], destino, ["id"]
            )["cardinalidade_observada"],
            "sem_correspondencia",
        )

    def test_relacao_rejeita_quantidades_de_campos_diferentes(self):
        with self.assertRaises(ValueError):
            perfilar_relacao(
                [{"a": "A", "b": "B"}], ["a", "b"], [{"id": "A"}], ["id"]
            )

    def test_relacao_nao_vaza_valores_e_coluna_ausente_falha(self):
        with self.assertRaises(ValueError) as contexto:
            perfilar_relacao(
                [{"pai": "segredo"}], ["pai"], [{"id": "A"}], ["id", "inexistente"]
            )
        self.assertNotIn("segredo", str(contexto.exception))
        perfil = perfilar_relacao(
            [{"pai": "segredo"}], ["pai"], [{"id": "segredo"}], ["id"]
        )
        self.assertNotIn("segredo", repr(perfil))


if __name__ == "__main__":
    unittest.main()
