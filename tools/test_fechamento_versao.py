from __future__ import annotations

import unittest
from unittest.mock import patch

from planejar_fechamento_versao import REQUIRED_FILES, build_plan


class FechamentoVersaoTests(unittest.TestCase):
    def test_plano_expoe_gates_de_fechamento(self):
        with patch("planejar_fechamento_versao.git_state") as git_state:
            git_state.return_value = {
                "head": "abc123",
                "upstream": "abc123",
                "ahead": False,
                "dirty": False,
                "status": "## main...origin/main",
            }
            plan = build_plan(saneamento_dry_run=True, validacao_completa=True, publicado=True)
        nomes = [gate["gate"] for gate in plan["gates"]]
        self.assertIn("arquivos essenciais", nomes)
        self.assertIn("publicado no GitHub", nomes)
        self.assertIn("validação completa VPS/Juan", nomes)
        self.assertTrue(plan["versao_pronta"])
        self.assertEqual([], plan["pendencias"])
        self.assertFalse(any(plan["proximas_acoes"]))
        self.assertTrue(any(path.endswith("revisoes.js") for path in REQUIRED_FILES))

    def test_commit_local_impede_versao_pronta(self):
        with patch("planejar_fechamento_versao.git_state") as git_state:
            git_state.return_value = {
                "head": "novo",
                "upstream": "antigo",
                "ahead": True,
                "dirty": False,
                "status": "## main...origin/main [ahead 1]",
            }
            plan = build_plan(validacao_completa=False)
        self.assertFalse(plan["versao_pronta"])
        self.assertIn("publicado no GitHub", plan["pendencias"])
        self.assertIn("publicar commits locais", plan["proximas_acoes"])


if __name__ == "__main__":
    unittest.main()
