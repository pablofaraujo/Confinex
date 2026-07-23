from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from reconciliar_compras_telegram import (
    build_draft,
    cluster_candidates,
    deduplicate,
    execute_backlog,
    summary,
)


class FakeClient:
    def __init__(self):
        self.inserts = []

    def insert(self, table, payload):
        self.inserts.append((table, payload))
        return {"id": payload["id"]}


class ReconciliarComprasTelegramTests(unittest.TestCase):
    def setUp(self):
        self.candidates = [
            {
                "contexto": "boi_balanca",
                "file": "/root/juan/11111111-1111-4111-8111-111111111111.trajectory.jsonl",
                "line": 10,
                "snippet": '{"conteudo":"nao importar"}',
            },
            {
                "contexto": "boi_balanca",
                "file": "/root/juan/11111111-1111-4111-8111-111111111111.jsonl",
                "line": 15,
                "snippet": "trecho repetido",
            },
            {
                "contexto": "confinamento",
                "file": "/root/juan/22222222-2222-4222-8222-222222222222.jsonl",
                "line": 20,
                "dados_extraidos": {"quantidade": 30, "valor_total": 90000},
            },
            {
                "contexto": "telegram:-9999999999",
                "file": "/root/juan/33333333-3333-4333-8333-333333333333.jsonl",
                "line": 25,
            },
        ]

    def test_clusters_repeated_hits_by_context_and_session(self):
        clusters = cluster_candidates(self.candidates)
        self.assertEqual(len(clusters), 3)
        boi = next(row for row in clusters if row["contexto"] == "Boi Balança")
        self.assertEqual(boi["ocorrencias"], 2)

    def test_draft_is_pending_human_readable_and_never_operational(self):
        draft = build_draft(cluster_candidates(self.candidates)[0])
        serialized = json.dumps(draft, ensure_ascii=False)
        self.assertEqual(draft["status"], "rascunho")
        self.assertEqual(draft["entidade_final_tipo"], "compras")
        self.assertFalse(draft["inferencias"]["confirmacao_suficiente"])
        self.assertEqual(draft["inferencias"]["estado"], "pendente")
        self.assertIn("confirmar se o indício representa uma compra real", draft["campos_pendentes"])
        self.assertNotIn("snippet", serialized)
        self.assertNotIn("conteudo", serialized)

    def test_unknown_group_does_not_expose_group_id(self):
        cluster = next(row for row in cluster_candidates(self.candidates) if row["contexto"] == "Contexto não identificado")
        draft = build_draft(cluster)
        self.assertEqual(draft["origem_conversa_id"], "-9999999999")
        self.assertEqual(draft["contexto_canonico"], "telegram:grupo:-9999999999")
        self.assertEqual(draft["contexto_nome"], "Contexto não identificado")
        self.assertEqual(draft["dados_extraidos"]["grupo_telegram"], "Contexto não identificado")
        self.assertIn("confirmar grupo/contexto", draft["campos_pendentes"])

    def test_deduplicates_by_deterministic_id_and_fingerprint(self):
        drafts = [build_draft(row) for row in cluster_candidates(self.candidates)]
        existing = [{"id": drafts[0]["id"], "inferencias": {}, "origem_conversa_id": None, "origem_mensagem_id": None}]
        planned, reused = deduplicate(drafts + [drafts[1]], existing)
        self.assertEqual(len(planned), 2)
        self.assertEqual(len(reused), 2)

    def test_execution_inserts_only_operation_drafts_and_respects_limit(self):
        drafts = [build_draft(row) for row in cluster_candidates(self.candidates)]
        client = FakeClient()
        created = execute_backlog(client, drafts, 2)
        self.assertEqual(len(created), 2)
        self.assertEqual([table for table, _ in client.inserts], ["operation_drafts", "operation_drafts"])
        self.assertNotIn("compras", [table for table, _ in client.inserts])

    def test_execution_requires_explicit_positive_limit(self):
        client = FakeClient()
        with self.assertRaisesRegex(Exception, "--limite"):
            execute_backlog(client, [], 0)

    def test_execution_mode_remains_clear_when_everything_is_duplicate(self):
        report = summary([], ["draft-existente"], [], executed=True)
        self.assertEqual(report["modo"], "executado")
        self.assertEqual(report["rascunhos_criados"], 0)


if __name__ == "__main__":
    unittest.main()
