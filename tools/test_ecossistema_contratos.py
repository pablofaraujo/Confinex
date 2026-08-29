from __future__ import annotations

import re
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import Mock, patch

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


    def test_orquestrador_valida_revisoes_js_externo(self):
        source = (TOOLS / "test_ecossistema.py").read_text(encoding="utf-8")
        self.assertIn('revisoes.html não deve manter script inline', source)
        self.assertIn("./revisoes.js?v=20260829-1", source)
        self.assertIn('["node", "--check", "revisoes.js"]', source)
        self.assertNotIn("NamedTemporaryFile", source)

    def test_frontend_nao_expoe_id_de_grupo_ou_json(self):
        source = (TOOLS / "test_revisoes_frontend.js").read_text(encoding="utf-8")
        html = (ROOT / "revisoes.html").read_text(encoding="utf-8")
        js = (ROOT / "revisoes.js").read_text(encoding="utf-8")
        tela = f"{html}\n{js}"
        self.assertIn("Contexto não identificado", source)
        self.assertIn("doesNotMatch(api.contextosResumoHtml", source)
        self.assertIn("Dados técnicos avançados", source)
        self.assertIn("./revisoes.js?v=20260829-1", html)
        self.assertNotRegex(tela, r"TELEGRAM_GROUP_NAMES\s*=\s*\{[^}]*telegram:-\d+")
        self.assertIn("origem_conversa_id:dados.origem_conversa_id||''", js)

    def test_normalizacao_e_dry_run_nao_promovem_dados(self):
        source = (TOOLS / "normalizar_contextos.py").read_text(encoding="utf-8")
        self.assertIn('"modo": "dry-run"', source)
        self.assertIn("CONFIRM_PREFIX", source)
        self.assertNotRegex(
            source,
            r"(?:insert|update)\([\"'](?:compras|vendas|pesagens_caderno|abates)",
        )
        self.assertIn('"operation_drafts", {**draft, "id": draft_id}', source)
        self.assertIn('client.insert("pending_actions"', source)
        self.assertIn('client.insert("eventos"', source)

    def test_migracao_preserva_escopo_das_memorias(self):
        migration = (
            ROOT
            / "supabase"
            / "migrations"
            / "202607230001_contextos_canonicos.sql"
        ).read_text(encoding="utf-8")
        normalizer = (TOOLS / "normalizar_contextos.py").read_text(encoding="utf-8")
        self.assertRegex(migration, r"(?m)^BEGIN;$")
        self.assertRegex(migration, r"(?m)^COMMIT;$")
        self.assertIn("ADD COLUMN IF NOT EXISTS contexto_escopo text", migration)
        self.assertLess(
            migration.index("NEW.payload #>> '{dados_revisados,origem_canal}'"),
            migration.index("NEW.canal"),
        )
        self.assertNotRegex(
            migration,
            r"(?i)\b(?:drop\s+table|truncate|delete\s+from|update\s+public\.)\b",
        )
        self.assertIn('payload["contexto_escopo"] = payload.pop("escopo")', normalizer)

    def test_eventos_da_fila_usam_status_validos(self):
        js = (ROOT / "revisoes.js").read_text(encoding="utf-8")
        statuses = set(
            re.findall(
                r"db\.from\('eventos'\)\.insert\(\{[\s\S]{0,900}?status:'([^']+)'",
                js,
            )
        )
        statuses.add("registrado" if "status:'registrado'" in js else "")
        self.assertTrue(statuses)
        self.assertLessEqual(
            statuses - {""},
            {"cancelado", "corrigido", "pendente", "registrado"},
        )


    def test_saneamento_da_fila_nao_toca_tabelas_operacionais(self):
        source = (TOOLS / "sanear_fila_revisoes.py").read_text(encoding="utf-8")
        self.assertIn('CONFIRM_PREFIX = "SANEAR FILA"', source)
        self.assertIn('"tabelas_operacionais_alteradas": 0', source)
        self.assertIn('"pending_action_id": link["pending_action_id"]', source)
        self.assertNotRegex(
            source,
            r'(?:insert|update)\(["\'](?:compras|vendas|pesagens_caderno|abates)',
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

    def test_handoff_nao_e_movido_ou_encerrado_automaticamente(self):
        source = (TOOLS / "planejar_handoff.py").read_text(encoding="utf-8")
        self.assertIn('"modo": "dry-run"', source)
        self.assertIn('"encerramento_permitido": False', source)
        self.assertNotRegex(
            source,
            r"(?:insert|update)\([\"'](?:contexto_handoff|memorias_agentes|eventos)",
        )

    def test_auditoria_de_memoria_e_somente_leitura(self):
        source = (TOOLS / "validar_memorias.py").read_text(encoding="utf-8")
        for kind in ("decisao", "preferencia", "regra", "excecao", "aprendizado"):
            self.assertIn(kind, source)
        self.assertIn('"escritas_realizadas": 0', source)
        self.assertNotRegex(
            source,
            r"(?:insert|update)\([\"']memorias_agentes",
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
        self.assertIn("timeout-minutes: 20", workflow)


    def test_imports_de_ci_ignoram_caminhos_privados_sem_permissao(self):
        import promocao_confirmacao_router
        import test_juan_vps

        privado = Mock()
        privado.exists.side_effect = PermissionError("sem acesso")
        self.assertFalse(promocao_confirmacao_router.path_exists(privado))

        env_file = Mock()
        env_file.read_text.side_effect = PermissionError("sem acesso")
        with patch.object(test_juan_vps, "ENV_FILE", env_file):
            env = test_juan_vps.carregar_env()
        self.assertIsInstance(env, dict)

    def test_verificador_vps_exige_roteador_antes_da_midia(self):
        source = (TOOLS / "test_juan_vps.py").read_text(encoding="utf-8")
        for contract in (
            "media://inbound/",
            "media:/inbound/",
            "inbound/",
            "arquivo_grupo_router.py",
            "--dry-run",
            "FERRAMENTAS_MIDIA_INTERNAS",
            "minimo_roteador=3",
            "auditar_chamadas_midia",
        ):
            self.assertIn(contract, source)
        self.assertIn('{".pdf", ".jpg", ".jpeg", ".png", ".webp"}', source)
        self.assertIn('{"pdf", "image", "file_fetch"}', source)
        self.assertIn("validar_pre_processamento_anthropic()", source)

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
