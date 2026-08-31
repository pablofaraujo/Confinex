"""Testes offline do dossiê da operação (F3) — nenhum teste toca rede."""

from __future__ import annotations

import unittest

from tools import dossie_operacao as dossie_mod
from tools.test_fechar_rentabilidade_operacao import dossie_base


class DossieTest(unittest.TestCase):
    def test_documento_tem_todas_as_secoes(self) -> None:
        documento = dossie_mod.renderizar_dossie(dossie_base())
        for secao in (
            "# Dossiê CF-99-001",
            "## 1. Identificação",
            "## 2. Compra e entrada",
            "## 3. Contrato e trava (hedge)",
            "## 4. Abate, acerto e pagamento",
            "## 5. Rentabilidade projetada × executada",
            "## 6. Nota explicativa do desvio",
            "## 7. Pendências",
        ):
            self.assertIn(secao, documento)

    def test_e_deterministico(self) -> None:
        self.assertEqual(
            dossie_mod.renderizar_dossie(dossie_base()),
            dossie_mod.renderizar_dossie(dossie_base()),
        )

    def test_numeros_do_fechamento_aparecem(self) -> None:
        documento = dossie_mod.renderizar_dossie(dossie_base())
        # total com hedge do dossiê base = 1700,00; hedge = −100,00
        self.assertIn("R$ 1.700,00", documento)
        self.assertIn("R$ -100,00", documento)
        self.assertIn("COMPLETO", documento)

    def test_dado_ausente_vira_nao_informado_nunca_chute(self) -> None:
        dossie = dossie_base()
        dossie["vendas"][0]["funrural"] = None
        documento = dossie_mod.renderizar_dossie(dossie)
        self.assertIn("Funrural não informado", documento)
        # e a nota estima 0,2% somente ROTULADO
        self.assertIn("ESTIMADO", documento)

    def test_reconstrucao_retrospectiva_e_avisada(self) -> None:
        dossie = dossie_base()
        dossie["estimativas"][0]["premissas"]["homologacao"] = "retrospectiva"
        documento = dossie_mod.renderizar_dossie(dossie)
        self.assertIn("RECONSTRUÇÃO retrospectiva", documento)

    def test_sem_estimativa_sem_comparativo(self) -> None:
        dossie = dossie_base()
        dossie["estimativas"] = []
        documento = dossie_mod.renderizar_dossie(dossie)
        self.assertIn("comparativo indisponível", documento)

    def test_sem_hedge_declara_ausencia(self) -> None:
        dossie = dossie_base()
        dossie["hedge"] = []
        documento = dossie_mod.renderizar_dossie(dossie)
        self.assertIn("Sem posição de hedge alocada", documento)


if __name__ == "__main__":
    unittest.main()
