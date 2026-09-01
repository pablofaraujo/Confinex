"""Testes offline do aplicador de propostas — nenhum teste toca rede."""

from __future__ import annotations

import unittest

from tools import aplicar_proposta_rentabilidade as aplicador
from tools import fechar_rentabilidade_operacao as f1
from tools import nota_desvio_operacao as f2
from tools.test_fechar_rentabilidade_operacao import dossie_base


def proposta_para(dossie, decisoes, status="aguardando_confirmacao"):
    fechamento = f1.fechar_operacao(dossie)
    cascata = f2.montar_cascata(dossie)
    return {
        "id": "prop-1",
        "agente": aplicador.AGENTE_CEREBRO,
        "acao_tipo": aplicador.ACAO_TIPO,
        "status": status,
        "entidade_codigo": dossie["operacao"]["codigo"],
        "payload": {
            "decisoes": [{"decisao": d} for d in decisoes],
            "confirmacao_f1": fechamento["confirmacao"],
            "confirmacao_f2": cascata["confirmacao"],
        },
    }


class ValidacaoTest(unittest.TestCase):
    def test_plano_mapeia_decisoes(self) -> None:
        dossie = dossie_base()
        proposta = proposta_para(dossie, ["refechar", "reconsolidar"])
        plano = aplicador.validar_proposta(
            proposta, f1.fechar_operacao(dossie), f2.montar_cascata(dossie)
        )
        self.assertTrue(plano["aplicar_fechamento"])
        self.assertTrue(plano["aplicar_consolidacao"])
        self.assertEqual(len(plano["confirmacao"]), 64)

    def test_dados_mudaram_aborta_sem_aplicar(self) -> None:
        dossie = dossie_base()
        proposta = proposta_para(dossie, ["refechar"])
        alterado = dossie_base()
        alterado["custos"][0]["valor"] = 1234.0  # mundo mudou após a proposta
        with self.assertRaises(ValueError) as contexto:
            aplicador.validar_proposta(
                proposta, f1.fechar_operacao(alterado),
                f2.montar_cascata(alterado),
            )
        self.assertIn("dados mudaram", str(contexto.exception))

    def test_status_nao_aplicavel_e_recusado(self) -> None:
        dossie = dossie_base()
        for status in ("executado", "rejeitado", "cancelado", "expirado"):
            proposta = proposta_para(dossie, ["refechar"], status=status)
            with self.assertRaises(ValueError):
                aplicador.validar_proposta(
                    proposta, f1.fechar_operacao(dossie),
                    f2.montar_cascata(dossie),
                )

    def test_agente_estranho_e_recusado(self) -> None:
        dossie = dossie_base()
        proposta = proposta_para(dossie, ["refechar"])
        proposta["agente"] = "outro_agente"
        with self.assertRaises(ValueError):
            aplicador.validar_proposta(
                proposta, f1.fechar_operacao(dossie),
                f2.montar_cascata(dossie),
            )

    def test_em_dia_sem_decisao_aplicavel(self) -> None:
        dossie = dossie_base()
        proposta = proposta_para(dossie, ["em_dia"])
        with self.assertRaises(ValueError):
            aplicador.validar_proposta(
                proposta, f1.fechar_operacao(dossie),
                f2.montar_cascata(dossie),
            )


class PlanejadoresUpsertTest(unittest.TestCase):
    def test_f1_inserir_vs_atualizar(self) -> None:
        fechamento = f1.fechar_operacao(dossie_base())
        novo = f1.plano_gravacao_fechamento(fechamento, None)
        self.assertEqual(novo["modo"], "inserido")
        self.assertIn("operacao_id", novo["payload"])
        self.assertIsNone(novo["evento"])
        refeito = f1.plano_gravacao_fechamento(
            fechamento, {"previsto": 1.0, "realizado_liquido": 2.0},
            motivo="teste",
        )
        self.assertEqual(refeito["modo"], "atualizado")
        self.assertNotIn("operacao_id", refeito["payload"])
        self.assertNotIn("desvio", refeito["payload"])  # coluna GERADA
        self.assertEqual(refeito["evento"]["tipo"], "fechamento_refechado")
        self.assertIn("fechamento_anterior", refeito["evento"]["dados"])

    def test_f2_reconsolidacao_indicador_orfao_e_erro(self) -> None:
        novos = [{"indicador": "frete"}, {"indicador": "hedge"}]
        plano = f2.plano_reconsolidacao(novos, ["frete"])
        self.assertEqual([d["indicador"] for d in plano["atualizar"]],
                         ["frete"])
        self.assertEqual([d["indicador"] for d in plano["inserir"]],
                         ["hedge"])
        with self.assertRaises(ValueError):
            f2.plano_reconsolidacao(novos, ["frete", "indicador_sumido"])


class ExecucaoTest(unittest.TestCase):
    def test_aplicacao_usa_ferramentas_dedicadas_e_marca_proposta(self) -> None:
        chamadas = []

        class FechadorFake:
            def gravar_fechamento(self, fechamento, motivo=None):
                chamadas.append(("f1", motivo))
                return "atualizado"

        class NotasFake:
            def gravar_consolidacao(self, cascata, avaliacao_id,
                                    versao, motivo=None):
                chamadas.append(("f2", avaliacao_id, versao))
                return "atualizado"

        class ClienteFake(aplicador.ClienteAplicador):
            def __init__(self) -> None:  # sem rede
                self.fechador = FechadorFake()
                self.notas = NotasFake()

        dossie = dossie_base()
        dossie["avaliacao"] = {"id": "aval-1"}
        proposta = proposta_para(dossie, ["refechar", "reconsolidar"])
        fechamento = f1.fechar_operacao(dossie)
        cascata = f2.montar_cascata(dossie)
        plano = aplicador.validar_proposta(proposta, fechamento, cascata)
        modos = ClienteFake().aplicar(plano, fechamento, cascata, dossie,
                                      "pablo")
        self.assertEqual(modos, {"fechamento": "atualizado",
                                 "consolidacao": "atualizado"})
        self.assertEqual([c[0] for c in chamadas], ["f1", "f2"])
        resultado = aplicador.resultado_aplicacao(plano, modos, "pablo")
        self.assertEqual(resultado["aprovado_por"], "pablo")
        self.assertEqual(resultado["fechamento"], "atualizado")

    def test_escrita_propria_fora_da_allowlist_recusada(self) -> None:
        cliente = aplicador.ClienteAplicador("https://exemplo.invalid", "x")
        for tabela in ("fechamentos_operacao", "confinex_consolidacoes",
                       "eventos", "compras"):
            with self.assertRaises(ValueError):
                cliente._patch(tabela, {"id": "eq.x"}, {})


if __name__ == "__main__":
    unittest.main()
