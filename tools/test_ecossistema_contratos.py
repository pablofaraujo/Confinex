from __future__ import annotations

import re
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import Mock

from test_ecossistema import (
    FalhaValidacao,
    assinatura_ids,
    preparar_validacao_completa,
    selecionar_todos,
)

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"


class ContratosEcossistemaTests(unittest.TestCase):
    def test_validacao_completa_exige_contexto_privado_e_ativa_agente(self):
        args = Namespace(
            completa=True,
            testar_agente=False,
            vps_host="host",
            vps_pdf="compra.pdf",
            vps_foto="compra.jpg",
            vps_grupo_id="grupo",
            vps_legenda_pdf="compra em PDF",
            vps_legenda_foto="compra em foto",
        )
        preparar_validacao_completa(args)
        self.assertTrue(args.testar_agente)

        args.vps_pdf = None
        with self.assertRaisesRegex(
            FalhaValidacao,
            "CONFINEX_TESTE_PDF",
        ):
            preparar_validacao_completa(args)

    def test_snapshot_pagina_ate_o_fim_e_assina_ids_ordenados(self):
        client = Mock()
        client.select.side_effect = [
            [{"id": 2}, {"id": 1}],
            [{"id": 3}],
        ]
        rows = selecionar_todos(client, "eventos", select="id", pagina=2)
        self.assertEqual([row["id"] for row in rows], [2, 1, 3])
        self.assertEqual(assinatura_ids(rows)["quantidade"], 3)
        self.assertEqual(client.select.call_count, 2)
        self.assertEqual(client.select.call_args_list[1].kwargs["offset"], "2")

    def test_frontend_nao_expoe_id_de_grupo_ou_json(self):
        source = (TOOLS / "test_revisoes_frontend.js").read_text(encoding="utf-8")
        self.assertIn("Contexto não identificado", source)
        self.assertIn("doesNotMatch(api.contextosResumoHtml", source)
        self.assertIn("Dados técnicos avançados", source)

    def test_eventos_da_fila_usam_status_validos(self):
        html = (ROOT / "revisoes.html").read_text(encoding="utf-8")
        statuses = set(
            re.findall(
                r"db\.from\('eventos'\)\.insert\(\{[\s\S]{0,900}?status:'([^']+)'",
                html,
            )
        )
        statuses.add("registrado" if "status:'registrado'" in html else "")
        self.assertTrue(statuses)
        self.assertLessEqual(
            statuses - {""},
            {"cancelado", "corrigido", "pendente", "registrado"},
        )

    def test_rotinas_operacionais_nao_usam_memoria_como_banco(self):
        for name in (
            "promocao_operacional.py",
            "promocao_confirmacao_router.py",
            "reconciliar_compras_telegram.py",
        ):
            source = (TOOLS / name).read_text(encoding="utf-8")
            self.assertNotRegex(
                source,
                r"(?:insert|update)\(['\"](?:memorias_agentes|contexto_handoff)",
                msg=f"{name} não deve gravar memória/handoff como dado operacional",
            )

    def test_compra_direta_exige_executor_e_confirmacao(self):
        reconciliar = (TOOLS / "reconciliar_compras_telegram.py").read_text(
            encoding="utf-8"
        )
        promover = (TOOLS / "promocao_operacional.py").read_text(encoding="utf-8")
        self.assertIn('insert("operation_drafts"', reconciliar)
        self.assertNotIn('insert_operational("compras"', reconciliar)
        self.assertIn("if confirmacao != expected", promover)
        self.assertIn("validate_execution_origin", promover)

    def test_ci_executa_o_comando_unico(self):
        workflow = (ROOT / ".github" / "workflows" / "validacao.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("python3 tools/test_ecossistema.py", workflow)
        self.assertRegex(workflow, r"(?m)^\s+schedule:")
        self.assertIn("timeout-minutes:", workflow)

    def test_verificador_vps_exige_roteador_antes_da_midia(self):
        source = (TOOLS / "test_juan_vps.py").read_text(encoding="utf-8")
        for contract in (
            "MediaPath:",
            "MediaPaths:",
            "arquivo_grupo_router.py",
            "--dry-run",
            '"name": "pdf"',
            '"name": "image"',
        ):
            self.assertIn(contract, source)
        self.assertIn('{".pdf", ".jpg", ".jpeg", ".png", ".webp"}', source)

    def test_verificador_vps_compara_todas_as_tabelas_criticas(self):
        source = (TOOLS / "test_juan_vps.py").read_text(encoding="utf-8")
        for table in (
            "operation_drafts",
            "pending_actions",
            "eventos",
            "compras",
            "vendas",
            "pesagens_caderno",
            "abates",
            "memorias_agentes",
            "contexto_handoff",
        ):
            self.assertIn(f'"{table}"', source)
        self.assertIn("if before != after", source)
        self.assertIn("limpar_sessao(marker)", source)
        self.assertIn("snapshot_cache_ocr()", source)
        self.assertIn('"--dry-run",\n            "--fix-missing"', source)
        self.assertNotIn('"--enforce"', source)


if __name__ == "__main__":
    unittest.main()
