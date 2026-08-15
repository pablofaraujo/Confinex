#!/usr/bin/env python3
"""Evita a volta de Actions, runtimes e dependências obsoletas."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


class VersoesDependenciasTest(unittest.TestCase):
    def test_actions_usam_runtime_node24(self) -> None:
        esperadas = {
            "actions/checkout": "v7",
            "actions/setup-python": "v7",
            "actions/setup-node": "v7",
            "actions/upload-artifact": "v7",
            "actions/configure-pages": "v6",
            "actions/upload-pages-artifact": "v5",
            "actions/deploy-pages": "v5",
        }
        referencias: dict[str, set[str]] = {}
        for arquivo in WORKFLOWS.glob("*.yml"):
            fonte = arquivo.read_text(encoding="utf-8")
            for acao, versao in re.findall(r"uses:\s*([^@\s]+)@([^\s]+)", fonte):
                referencias.setdefault(acao, set()).add(versao)
        for acao, versao in esperadas.items():
            self.assertEqual(referencias.get(acao), {versao}, msg=acao)

    def test_ci_usa_runtimes_atuais(self) -> None:
        validacao = (WORKFLOWS / "validacao.yml").read_text(encoding="utf-8")
        painel = (WORKFLOWS / "atualizar-painel-boi-gordo.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("node-version: '24'", validacao)
        self.assertIn("python-version: '3.14'", validacao)
        self.assertIn("python-version: '3.14'", painel)

    def test_dependencias_de_auditoria_estao_fixadas(self) -> None:
        pacote = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
        self.assertEqual(pacote["devDependencies"]["playwright"], "1.62.1")
        self.assertEqual(lock["packages"][""]["devDependencies"]["playwright"], "1.62.1")
        self.assertEqual(
            (ROOT / "requirements-tools.txt").read_text(encoding="utf-8").strip(),
            "pypdf==6.16.1",
        )

    def test_cdns_nao_usam_alias_flutuante_nem_chart_antigo(self) -> None:
        fontes = "\n".join(
            arquivo.read_text(encoding="utf-8")
            for arquivo in [*ROOT.glob("*.html"), ROOT / "DESIGN.md"]
        )
        self.assertNotIn("@supabase/supabase-js@2/dist", fontes)
        self.assertNotIn("Chart.js/4.4.1", fontes)
        self.assertIn("@supabase/supabase-js@2.112.3/dist", fontes)
        self.assertIn("chart.js@4.5.1/dist/chart.umd.min.js", fontes)


if __name__ == "__main__":
    unittest.main()
