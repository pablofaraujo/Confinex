"""Testes offline da nota de desvio (F2) — nenhum teste toca rede."""

from __future__ import annotations

import unittest

from tools import nota_desvio_operacao as nota
from tools.test_fechar_rentabilidade_operacao import dossie_base


class CascataTest(unittest.TestCase):
    def dossie_com_bases(self):
        dossie = dossie_base()
        dossie["estimativas"][0]["premissas"]["dadosFonte"] = {
            "fatTotal": 10000.0, "valorCompra": 5000.0, "frete": 900.0,
            "custos": {"trato": 2100.0},
        }
        return dossie

    def test_cascata_fecha_aritmeticamente(self) -> None:
        cascata = nota.montar_cascata(self.dossie_com_bases())
        # previsto líquido 800; realizado total 1700 → desvio +900
        self.assertEqual(cascata["desvio_total"], 900.0)
        soma_impactos = round(sum(
            item["impacto_no_resultado"] for item in cascata["linhas"]
            if item["impacto_no_resultado"] is not None
        ), 2)
        self.assertEqual(soma_impactos, cascata["desvio_total"])

    def test_sinais_dos_componentes(self) -> None:
        cascata = nota.montar_cascata(self.dossie_com_bases())
        por_nome = {item["indicador"]: item for item in cascata["linhas"]}
        # frete real 1000 vs previsto 900 → custo maior = impacto −100
        self.assertEqual(por_nome["frete"]["impacto_no_resultado"], -100.0)
        self.assertEqual(por_nome["frete"]["classificacao"], "desfavoravel")
        # trato real 2000 vs previsto 2100 → custo menor = impacto +100
        self.assertEqual(
            por_nome["custo_confinamento"]["impacto_no_resultado"], 100.0
        )
        self.assertEqual(
            por_nome["custo_confinamento"]["classificacao"], "favoravel"
        )
        # componentes sem base prevista não chutam
        self.assertEqual(
            por_nome["custo_financeiro"]["classificacao"], "sem_base_prevista"
        )
        self.assertIsNone(por_nome["custo_financeiro"]["estimado"])

    def test_residuo_explicito_quando_ha_previsto(self) -> None:
        cascata = nota.montar_cascata(self.dossie_com_bases())
        residuos = [i for i in cascata["linhas"]
                    if i["natureza"] == "residual"]
        self.assertEqual(len(residuos), 1)

    def test_sem_estimativa_nota_so_realizado(self) -> None:
        dossie = dossie_base()
        dossie["estimativas"] = []
        cascata = nota.montar_cascata(dossie)
        self.assertIsNone(cascata["desvio_total"])
        self.assertNotIn("residuo_nao_decomponivel",
                         [i["indicador"] for i in cascata["linhas"]])
        self.assertIn("sem estimativa congelada", cascata["nota"])

    def test_nota_e_deterministica_e_menciona_funrural_estimado(self) -> None:
        dossie = self.dossie_com_bases()
        dossie["vendas"][0]["funrural"] = None
        primeira = nota.montar_cascata(dossie)
        segunda = nota.montar_cascata(dossie)
        self.assertEqual(primeira["confirmacao"], segunda["confirmacao"])
        self.assertEqual(primeira["nota"], segunda["nota"])
        self.assertIn("0,2%", primeira["nota"])
        self.assertIn("ESTIMADO", primeira["nota"])

    def test_payloads_persistencia_completos(self) -> None:
        cascata = nota.montar_cascata(self.dossie_com_bases())
        consolidacao, desvios = nota.payloads_persistencia(
            cascata, "aval-1", 1
        )
        self.assertEqual(consolidacao["avaliacao_id"], "aval-1")
        self.assertEqual(consolidacao["comentario_geral"], cascata["nota"])
        self.assertEqual(len(desvios), len(cascata["linhas"]))
        self.assertTrue(all("consolidacao_id" not in d for d in desvios))
        # desvio/desvio_percentual são colunas GERADAS no banco: nunca enviar
        self.assertTrue(all("desvio" not in d for d in desvios))
        self.assertTrue(all("desvio_percentual" not in d for d in desvios))
        # vocabulário persistido respeita os CHECKs do banco (202607200001)
        naturezas_validas = {"custo", "receita", "resultado",
                             "prazo", "zootecnico"}
        self.assertTrue(all(d["natureza"] in naturezas_validas
                            for d in desvios))
        for d in desvios:
            if d["classificacao"] is None:
                self.assertIn("SEM BASE PREVISTA",
                              d["comentario_automatico"])
            else:
                self.assertIn(d["classificacao"],
                              {"favoravel", "neutro", "desfavoravel"})
        # residual persiste estimado=0 e realizado=<resíduo> para que a
        # coluna gerada (realizado − estimado) reproduza o desvio
        residual = next(d for d in desvios
                        if d["indicador"] == "residuo_nao_decomponivel")
        cascata_residual = next(
            i for i in cascata["linhas"] if i["natureza"] == "residual"
        )
        self.assertEqual(residual["estimado"], 0.0)
        self.assertEqual(residual["realizado"], cascata_residual["desvio"])

    def test_escrita_fora_da_allowlist_recusada(self) -> None:
        cliente = nota.ClienteNota("https://exemplo.invalid", "chave")
        with self.assertRaises(ValueError):
            cliente._post("compras", {})


if __name__ == "__main__":
    unittest.main()
