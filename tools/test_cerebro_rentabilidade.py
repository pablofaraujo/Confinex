"""Testes offline do cérebro de rentabilidade — nenhum teste toca rede."""

from __future__ import annotations

import unittest

from tools import cerebro_rentabilidade as cerebro
from tools import fechar_rentabilidade_operacao as f1
from tools import nota_desvio_operacao as f2
from tools.test_fechar_rentabilidade_operacao import dossie_base


def gravado_de(dossie) -> dict:
    """Fechamento gravado coerente com o dossiê (como as ferramentas gravam)."""
    fechamento = f1.fechar_operacao(dossie)
    realizado = fechamento["realizado"]
    previsto = (fechamento.get("previsto") or {})
    return {
        "previsto": previsto.get("lucro_liquido_previsto"),
        "realizado_bruto": realizado["lucro_bruto"],
        "realizado_liquido": realizado["lucro_liquido"],
        "hedge_creditado": realizado["hedge_creditado"],
        "explicacao": f1.explicacao_texto(fechamento),
    }


def consolidada_de(dossie) -> dict:
    cascata = f2.montar_cascata(dossie)
    return {"resultado_final": {"confirmacao": cascata["confirmacao"]}}


class DecisaoTest(unittest.TestCase):
    def test_sem_fechamento_propoe_fechar_e_consolidar(self) -> None:
        dossie = dossie_base()
        dossie["avaliacao"] = {"id": "aval-1"}
        avaliacao = cerebro.avaliar_operacao(dossie, None, None)
        decisoes = [d["decisao"] for d in avaliacao["decisoes"]]
        self.assertEqual(decisoes, ["fechar", "consolidar"])
        self.assertTrue(avaliacao["acionavel"])

    def test_tudo_gravado_e_coerente_fica_em_dia(self) -> None:
        dossie = dossie_base()
        dossie["avaliacao"] = {"id": "aval-1"}
        avaliacao = cerebro.avaliar_operacao(
            dossie, gravado_de(dossie), consolidada_de(dossie)
        )
        self.assertEqual(
            [d["decisao"] for d in avaliacao["decisoes"]], ["em_dia"]
        )
        self.assertFalse(avaliacao["acionavel"])

    def test_numero_divergente_propoe_refechar_com_diff(self) -> None:
        dossie = dossie_base()
        gravado = gravado_de(dossie)
        dossie["custos"][0]["valor"] = 1500.0  # frete mudou depois
        avaliacao = cerebro.avaliar_operacao(dossie, gravado, None)
        refechar = next(d for d in avaliacao["decisoes"]
                        if d["decisao"] == "refechar")
        campos = {diff["campo"] for diff in refechar["diffs"]}
        self.assertIn("realizado_bruto", campos)

    def test_pendencia_resolvida_propoe_refechar_sem_diff(self) -> None:
        dossie = dossie_base()
        dossie["vendas"][0]["recebido"] = False
        dossie["acertos"] = [{"status": "aguardando",
                              "data_recebimento": None}]
        gravado = gravado_de(dossie)  # gravou PARCIAL sem recebimento
        dossie["vendas"][0]["recebido"] = True  # recebimento chegou
        avaliacao = cerebro.avaliar_operacao(dossie, gravado, None)
        refechar = next(d for d in avaliacao["decisoes"]
                        if d["decisao"] == "refechar")
        self.assertEqual(refechar["diffs"], [])
        self.assertTrue(refechar["pendencias_resolvidas"])

    def test_consolidacao_divergente_propoe_reconsolidar(self) -> None:
        dossie = dossie_base()
        dossie["avaliacao"] = {"id": "aval-1"}
        consolidada = {"resultado_final": {"confirmacao": "0" * 64}}
        avaliacao = cerebro.avaliar_operacao(
            dossie, gravado_de(dossie), consolidada
        )
        self.assertIn("reconsolidar",
                      [d["decisao"] for d in avaliacao["decisoes"]])

    def test_sem_avaliacao_nao_propoe_consolidacao(self) -> None:
        dossie = dossie_base()
        dossie["avaliacao"] = None
        dossie["estimativas"] = []
        avaliacao = cerebro.avaliar_operacao(dossie, gravado_de(dossie), None)
        decisoes = [d["decisao"] for d in avaliacao["decisoes"]]
        self.assertNotIn("consolidar", decisoes)
        self.assertNotIn("reconsolidar", decisoes)

    def test_chave_muda_quando_dados_mudam(self) -> None:
        dossie = dossie_base()
        primeira = cerebro.avaliar_operacao(dossie, None, None)
        alterado = dossie_base()
        alterado["custos"][0]["valor"] = 1001.0
        segunda = cerebro.avaliar_operacao(alterado, None, None)
        self.assertNotEqual(primeira["chave_proposta"],
                            segunda["chave_proposta"])
        # e é determinística para o mesmo dossiê
        self.assertEqual(primeira["chave_proposta"],
                         cerebro.avaliar_operacao(
                             dossie_base(), None, None)["chave_proposta"])


class PropostaTest(unittest.TestCase):
    def test_payload_sem_campos_de_promocao(self) -> None:
        avaliacao = cerebro.avaliar_operacao(dossie_base(), None, None)
        payload = cerebro.proposta_payload(avaliacao)
        texto = str(payload)
        for proibido in ("target_table", "proposed_record", "idempotency",
                         "promocao_controle_version"):
            self.assertNotIn(proibido, texto)
        self.assertEqual(payload["chave_proposta"],
                         avaliacao["chave_proposta"])
        self.assertIn("nota", payload)

    def test_resumo_texto_traz_codigo_e_decisao(self) -> None:
        avaliacao = cerebro.avaliar_operacao(dossie_base(), None, None)
        resumo = cerebro.resumo_texto(avaliacao)
        self.assertIn("CF-99-001", resumo)
        self.assertIn("fechar", resumo)


class GuardrailsTest(unittest.TestCase):
    def test_escrita_fora_da_allowlist_recusada(self) -> None:
        cliente = cerebro.ClienteCerebro("https://exemplo.invalid", "chave")
        for tabela in ("fechamentos_operacao", "confinex_consolidacoes",
                       "compras", "operacoes"):
            with self.assertRaises(ValueError):
                cliente._post(tabela, {})

    def test_proposta_e_marcada_nao_executavel(self) -> None:
        capturado = []

        class ClienteFake(cerebro.ClienteCerebro):
            def __init__(self) -> None:  # sem rede
                self.url = "x"
                self.chave = "x"
                self.timeout = 1

            def _post(self, tabela, corpo):
                capturado.append((tabela, corpo))

        avaliacao = cerebro.avaliar_operacao(dossie_base(), None, None)
        ClienteFake().propor(avaliacao)
        tabelas = [t for t, _ in capturado]
        self.assertEqual(tabelas, ["pending_actions", "eventos"])
        acao = capturado[0][1]
        self.assertIs(acao["executavel"], False)
        self.assertEqual(acao["acao_tipo"], "proposta_rentabilidade")
        self.assertEqual(acao["agente"], cerebro.AGENTE)


if __name__ == "__main__":
    unittest.main()
