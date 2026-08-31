"""Testes offline do fechamento de rentabilidade — nenhum teste toca rede."""

from __future__ import annotations

import unittest
from typing import Any

from tools import fechar_rentabilidade_operacao as fechamento


def dossie_base() -> dict[str, Any]:
    return {
        "operacao": {"id": "op-1", "codigo": "CF-99-001", "modalidade": "ms",
                     "tipo_negocio": "confinamento"},
        "estimativas": [{
            "versao": 1, "tipo": "original",
            "premissas": {"homologacao": "contemporanea"},
            "resultado": {"lucroBruto": 1000.0, "lucroLiquido": 800.0,
                          "receita": 10000.0},
            "motivo_revisao": None,
        }],
        "compras": [{"valor_total": 5000.0}],
        "vendas": [{"id": "v-1", "valor_bruto": 10000.0, "funrural": 20.0,
                    "finpec": 0.0, "outros_custos": 30.0, "recebido": True,
                    "data_abate": "2026-06-01"}],
        "abates": [],
        "acertos": [{"status": "recebido", "data_recebimento": "2026-06-10"}],
        "custos": [
            {"categoria": "frete", "valor": 1000.0},
            {"categoria": "trato", "valor": 2000.0},
            {"categoria": "financeiro", "valor": 150.0},
        ],
        "ressarcimentos": [],
        "entradas": [],
        "fluxo_caixa": [],
        "promissorias": [],
        "hedge": [{"alocacao": {"resultado_creditado": -100.0},
                   "posicao": {"status": "encerrada",
                               "referencia_bolsa": "B3-99-001"}}],
        "participantes": [],
    }


class FechamentoCompletoTest(unittest.TestCase):
    def test_contrato_financeiro_cada_componente_uma_vez(self) -> None:
        resultado = fechamento.fechar_operacao(dossie_base())
        realizado = resultado["realizado"]
        # receita líquida = 10000 − 20 − 0 − 30 = 9950
        self.assertEqual(realizado["receita_liquida"], 9950.0)
        # bruto = 9950 − 5000 − (1000+2000) = 1950
        self.assertEqual(realizado["lucro_bruto"], 1950.0)
        # líquido = 1950 − 150 = 1800; total com hedge = 1700
        self.assertEqual(realizado["lucro_liquido"], 1800.0)
        self.assertEqual(realizado["hedge_creditado"], -100.0)
        self.assertEqual(realizado["resultado_total_com_hedge"], 1700.0)
        # desvio vs previsto líquido 800 → 900
        self.assertEqual(resultado["desvio_vs_previsto_liquido"], 900.0)
        self.assertEqual(resultado["status_fechamento"], "COMPLETO")
        self.assertEqual(resultado["pendencias"], [])

    def test_confirmacao_e_deterministica(self) -> None:
        primeiro = fechamento.fechar_operacao(dossie_base())
        segundo = fechamento.fechar_operacao(dossie_base())
        self.assertEqual(primeiro["confirmacao"], segundo["confirmacao"])
        alterado = dossie_base()
        alterado["custos"][0]["valor"] = 1001.0
        self.assertNotEqual(
            primeiro["confirmacao"],
            fechamento.fechar_operacao(alterado)["confirmacao"],
        )


class FechamentoParcialTest(unittest.TestCase):
    def test_sem_recebimento_vira_parcial_com_pendencia(self) -> None:
        dossie = dossie_base()
        dossie["vendas"][0]["recebido"] = False
        dossie["acertos"] = [{"status": "aguardando",
                              "data_recebimento": None}]
        resultado = fechamento.fechar_operacao(dossie)
        self.assertEqual(resultado["status_fechamento"], "PARCIAL")
        self.assertTrue(any(
            "Recebimento" in p for p in resultado["pendencias"]
        ))
        self.assertTrue(any(
            "aguardando" in p for p in resultado["pendencias"]
        ))

    def test_funrural_nulo_nao_e_inventado(self) -> None:
        dossie = dossie_base()
        dossie["vendas"][0]["funrural"] = None
        resultado = fechamento.fechar_operacao(dossie)
        # encargo ausente entra como 0 no número e como pendência explícita
        self.assertEqual(resultado["realizado"]["funrural"], 0.0)
        self.assertTrue(any(
            "Funrural não informado" in p for p in resultado["pendencias"]
        ))

    def test_reconstrucao_retrospectiva_e_rotulada(self) -> None:
        dossie = dossie_base()
        dossie["estimativas"][0]["premissas"]["homologacao"] = "retrospectiva"
        resultado = fechamento.fechar_operacao(dossie)
        self.assertTrue(resultado["previsto"]["reconstrucao_retrospectiva"])
        self.assertTrue(any(
            "RECONSTRUÇÃO" in p for p in resultado["pendencias"]
        ))

    def test_sem_estimativa_sem_desvio(self) -> None:
        dossie = dossie_base()
        dossie["estimativas"] = []
        resultado = fechamento.fechar_operacao(dossie)
        self.assertIsNone(resultado["desvio_vs_previsto_liquido"])
        self.assertEqual(resultado["status_fechamento"], "PARCIAL")

    def test_hedge_aberto_gera_pendencia(self) -> None:
        dossie = dossie_base()
        dossie["hedge"][0]["posicao"]["status"] = "aberta"
        resultado = fechamento.fechar_operacao(dossie)
        self.assertTrue(any(
            "hedge ainda aberta" in p for p in resultado["pendencias"]
        ))


class AbateSemVendaTest(unittest.TestCase):
    def test_abate_avulso_soma_e_vinculado_nao_duplica(self) -> None:
        dossie = dossie_base()
        dossie["abates"] = [
            {"venda_id": "v-1", "valor_bruto": 99999.0},  # vinculado: ignora
            {"venda_id": None, "valor_bruto": 5000.0, "funrural_valor": 10.0,
             "fundesa_valor": 5.0, "outros_descontos": 0.0},
        ]
        resultado = fechamento.fechar_operacao(dossie)
        # 10000 + 5000 brutos; encargos 20+10; outros 30+5
        self.assertEqual(resultado["realizado"]["faturamento_bruto"], 15000.0)
        self.assertEqual(resultado["realizado"]["receita_liquida"],
                         15000.0 - 30.0 - 35.0)


class GravacaoTest(unittest.TestCase):
    def test_gravacao_offline_e_recusada(self) -> None:
        import contextlib
        import io
        import json
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json",
                                         delete=False) as arquivo:
            json.dump(dossie_base(), arquivo)
            caminho = arquivo.name
        erro = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(erro):
            codigo = fechamento.main([
                "--entrada", caminho, "--executar", "--confirmacao", "x",
            ])
        self.assertEqual(codigo, 2)
        self.assertIn("modo ao vivo", erro.getvalue())


if __name__ == "__main__":
    unittest.main()
