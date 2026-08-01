import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tools.sanear_duplicidades_bgi import executar, montar_plano


class ClienteFalso:
    def __init__(self, posicoes, alocacoes=None):
        self.dados = {
            "posicoes_hedge": posicoes,
            "alocacoes_hedge": alocacoes or [],
        }

    def selecionar(self, tabela):
        return [dict(item) for item in self.dados.get(tabela, [])]

    def excluir_posicao(self, registro_id):
        removidos = [
            item for item in self.dados["posicoes_hedge"]
            if item["id"] == registro_id
        ]
        self.dados["posicoes_hedge"] = [
            item for item in self.dados["posicoes_hedge"]
            if item["id"] != registro_id
        ]
        return removidos


def posicao(registro_id, termo, **extras):
    base = {
        "id": registro_id,
        "termo": termo,
        "contrato": "BGIQ26",
        "direcao": "vendido",
        "categoria": "hedge",
        "contratos_qtd": 4,
        "preco_entrada": 350,
        "preco_saida": None,
        "data_entrada": "2026-07-01",
        "data_saida": None,
        "status": "aberta",
        "custo_corretagem": None,
        "custo_finpec": None,
        "resultado_realizado": None,
        "negocio_rateio": None,
        "detalhes": None,
        "obs": None,
        "mes": None,
        "rolada_para": None,
    }
    return {**base, **extras}


class SanearDuplicidadesBgiTest(unittest.TestCase):
    def test_propõe_legado_vazio_igual_ao_gerenciado(self):
        cliente = ClienteFalso([
            posicao("legado", None),
            posicao("canonico", "bgp:canonico", negocio_rateio="CF-26-001"),
        ])
        plano, snapshot = montar_plano(cliente)
        self.assertEqual(plano["duplicidades_seguras"], 1)
        self.assertEqual(plano["ambiguidades_preservadas"], 0)
        self.assertEqual(snapshot["alvos"][0]["legado_id"], "legado")

    def test_preserva_legado_com_alocacao(self):
        cliente = ClienteFalso(
            [posicao("legado", None), posicao("canonico", "bgp:canonico")],
            [{"id": "a1", "posicao_id": "legado"}],
        )
        plano, _ = montar_plano(cliente)
        self.assertEqual(plano["duplicidades_seguras"], 0)
        self.assertEqual(plano["ambiguidades_preservadas"], 1)

    def test_preserva_legado_com_dado_exclusivo(self):
        cliente = ClienteFalso([
            posicao("legado", None, obs="informação exclusiva"),
            posicao("canonico", "bgp:canonico"),
        ])
        plano, _ = montar_plano(cliente)
        self.assertEqual(plano["duplicidades_seguras"], 0)
        self.assertEqual(plano["ambiguidades_preservadas"], 1)

    def test_preserva_grupo_com_dois_gerenciados(self):
        cliente = ClienteFalso([
            posicao("legado", None),
            posicao("canonico-1", "bgp:1"),
            posicao("canonico-2", "bgp:2"),
        ])
        plano, _ = montar_plano(cliente)
        self.assertEqual(plano["duplicidades_seguras"], 0)
        self.assertEqual(plano["ambiguidades_preservadas"], 1)

    def test_informa_campos_ocultos_que_divergem(self):
        cliente = ClienteFalso([
            posicao("legado", None, categoria=None, data_entrada=None),
            posicao("canonico", "bgp:canonico"),
        ])
        plano, _ = montar_plano(cliente)
        self.assertEqual(plano["duplicidades_seguras"], 0)
        self.assertEqual(
            plano["ambiguidades"][0]["campos_divergentes"],
            ["categoria", "data_entrada"],
        )

    def test_zero_e_vazio_sao_equivalentes_apenas_para_custos(self):
        cliente = ClienteFalso([
            posicao("legado", None, custo_finpec=0),
            posicao("canonico", "bgp:canonico", custo_finpec=None),
        ])
        plano, _ = montar_plano(cliente)
        self.assertEqual(plano["duplicidades_seguras"], 1)
        self.assertEqual(plano["ambiguidades_preservadas"], 0)

    def test_custo_presente_somente_no_canonico_e_enriquecimento_seguro(self):
        cliente = ClienteFalso([
            posicao("legado", None, custo_finpec=None),
            posicao("canonico", "bgp:canonico", custo_finpec=120),
        ])
        plano, snapshot = montar_plano(cliente)
        self.assertEqual(plano["duplicidades_seguras"], 1)
        self.assertEqual(plano["ambiguidades_preservadas"], 0)
        self.assertEqual(
            snapshot["alvos"][0]["enriquecimentos_preservados"],
            ["custo_finpec"],
        )

    def test_custo_presente_somente_no_legado_continua_bloqueado(self):
        cliente = ClienteFalso([
            posicao("legado", None, custo_finpec=120),
            posicao("canonico", "bgp:canonico", custo_finpec=None),
        ])
        plano, _ = montar_plano(cliente)
        self.assertEqual(plano["duplicidades_seguras"], 0)
        self.assertEqual(plano["ambiguidades_preservadas"], 1)

    def test_execucao_remove_somente_alvo_e_grava_snapshot(self):
        cliente = ClienteFalso([
            posicao("legado", None),
            posicao("canonico", "bgp:canonico", negocio_rateio="CF-26-001"),
            posicao("distinta", "bgp:distinta", preco_entrada=351),
        ])
        plano, snapshot = montar_plano(cliente)
        with TemporaryDirectory() as pasta:
            backup = Path(pasta) / "backup.json"
            resultado = executar(
                cliente,
                plano,
                snapshot,
                plano["confirmacao_exigida"],
                backup,
            )
            self.assertEqual(resultado["duplicidades_removidas"], 1)
            self.assertTrue(backup.exists())
            self.assertEqual(
                {item["id"] for item in cliente.dados["posicoes_hedge"]},
                {"canonico", "distinta"},
            )

    def test_confirmacao_incorreta_nao_remove(self):
        cliente = ClienteFalso([
            posicao("legado", None),
            posicao("canonico", "bgp:canonico"),
        ])
        plano, snapshot = montar_plano(cliente)
        with TemporaryDirectory() as pasta:
            with self.assertRaisesRegex(RuntimeError, "confirmação inválida"):
                executar(cliente, plano, snapshot, "CONFIRMACAO ERRADA", Path(pasta) / "backup.json")
        self.assertEqual(len(cliente.dados["posicoes_hedge"]), 2)


if __name__ == "__main__":
    unittest.main()
