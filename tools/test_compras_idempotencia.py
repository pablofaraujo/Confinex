import re
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from confinex_client import (
    ConfinexClient,
    ConfinexConnectionError,
    ConfinexError,
    ConfinexHTTPError,
    ConfinexIdempotencyConflict,
)


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase/migrations/202607250001_compras_idempotencia.sql"


class MemoryStore:
    def __init__(self):
        self.lock = threading.Lock()
        self.records = {}
        self.posts = 0
        self.timeout_mode = None
        self.timeout_used = False


class MemoryClient(ConfinexClient):
    def __init__(self, store):
        super().__init__(url="https://example.invalid", key="test-only")
        self.store = store

    def _request(self, method, table, *, params=None, payload=None, prefer="return=representation"):
        if method == "GET" and table == "compras":
            raw_key = (params or {}).get("idempotency_key", "")
            key = raw_key.removeprefix("eq.")
            record = self.store.records.get(key)
            return [dict(record)] if record else []

        if method != "POST":
            raise AssertionError(f"metodo inesperado no teste: {method}")

        with self.store.lock:
            self.store.posts += 1
            if self.store.timeout_mode == "before" and not self.store.timeout_used:
                self.store.timeout_used = True
                raise ConfinexConnectionError("timeout simulado antes do envio")

            key = (payload or {}).get("idempotency_key")
            if key and key in self.store.records:
                raise ConfinexHTTPError("duplicidade simulada", status=409)

            record = {"id": f"registro-{self.store.posts}", **(payload or {})}
            if key:
                self.store.records[key] = record

            if self.store.timeout_mode == "after" and not self.store.timeout_used:
                self.store.timeout_used = True
                raise ConfinexConnectionError("timeout simulado depois do envio")
            return [record]


class ComprasIdempotenciaTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.client = MemoryClient(self.store)
        self.payload = {
            "operacao_id": "operacao-teste",
            "quantidade": 10,
            "valor_total": 1000,
        }

    def test_positive_insert_returns_inserted(self):
        result = self.client.insert_operational(
            "compras",
            self.payload,
            idempotency_key="origem:mensagem-1",
        )
        self.assertEqual(result.status, "inserted")
        self.assertEqual(result.record["idempotency_key"], "origem:mensagem-1")
        self.assertEqual(self.store.posts, 1)

    def test_retry_same_key_same_payload_returns_duplicate(self):
        first = self.client.insert_operational(
            "compras", self.payload, idempotency_key="origem:mensagem-1"
        )
        second = self.client.insert_operational(
            "compras", self.payload, idempotency_key="origem:mensagem-1"
        )
        self.assertEqual(first.status, "inserted")
        self.assertEqual(second.status, "duplicate")
        self.assertEqual(first.record["id"], second.record["id"])
        self.assertEqual(len(self.store.records), 1)

    def test_same_key_with_different_payload_is_rejected(self):
        self.client.insert_operational(
            "compras", self.payload, idempotency_key="origem:mensagem-1"
        )
        changed = {**self.payload, "quantidade": 11}
        with self.assertRaisesRegex(
            ConfinexIdempotencyConflict,
            "dados diferentes",
        ):
            self.client.insert_operational(
                "compras", changed, idempotency_key="origem:mensagem-1"
            )
        self.assertEqual(len(self.store.records), 1)

    def test_empty_key_is_rejected(self):
        with self.assertRaisesRegex(ConfinexError, "nao pode ser vazia"):
            self.client.insert_operational(
                "compras", self.payload, idempotency_key="  "
            )
        self.assertEqual(self.store.posts, 0)

    def test_null_key_preserves_legacy_insert(self):
        result = self.client.insert_operational(
            "compras", self.payload, idempotency_key=None
        )
        self.assertEqual(result.status, "inserted")
        self.assertNotIn("idempotency_key", result.record)
        self.assertEqual(self.store.posts, 1)

    def test_timeout_after_commit_is_reconciled_as_duplicate(self):
        self.store.timeout_mode = "after"
        result = self.client.insert_operational(
            "compras", self.payload, idempotency_key="origem:mensagem-1"
        )
        self.assertEqual(result.status, "duplicate")
        self.assertEqual(len(self.store.records), 1)
        self.assertEqual(self.store.posts, 1)

    def test_timeout_before_commit_does_not_retry_post(self):
        self.store.timeout_mode = "before"
        with self.assertRaises(ConfinexConnectionError):
            self.client.insert_operational(
                "compras", self.payload, idempotency_key="origem:mensagem-1"
            )
        self.assertEqual(self.store.posts, 1)
        self.assertEqual(self.store.records, {})

    def test_transport_timeout_is_classified_for_reconciliation(self):
        client = ConfinexClient(url="https://example.invalid", key="test-only")
        with mock.patch(
            "confinex_client.urllib.request.urlopen",
            side_effect=TimeoutError("timeout simulado"),
        ):
            with self.assertRaises(ConfinexConnectionError):
                client.insert_operational(
                    "compras",
                    self.payload,
                    idempotency_key="origem:mensagem-1",
                )

    def test_concurrent_same_key_creates_one_record(self):
        clients = [MemoryClient(self.store), MemoryClient(self.store)]

        def run(client):
            return client.insert_operational(
                "compras", self.payload, idempotency_key="origem:concorrente"
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, clients))

        self.assertEqual(
            sorted(result.status for result in results),
            ["duplicate", "inserted"],
        )
        self.assertEqual(len(self.store.records), 1)
        self.assertEqual(
            len({result.record["id"] for result in results}),
            1,
        )


class ComprasIdempotenciaMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = MIGRATION.read_text(encoding="utf-8")

    def test_migration_is_additive_and_preserves_rls(self):
        self.assertRegex(
            self.sql,
            r"add column if not exists idempotency_key text",
        )
        self.assertRegex(
            self.sql,
            r"create unique index if not exists compras_idempotency_key_unique",
        )
        self.assertRegex(
            self.sql,
            r"where idempotency_key is not null",
        )
        self.assertNotRegex(
            self.sql,
            r"\b(insert|update|delete|truncate)\b",
        )
        self.assertNotRegex(
            self.sql,
            r"\b(disable row level security|drop policy|create policy|grant|revoke)\b",
        )

    def test_migration_documents_nullable_legacy_contract(self):
        self.assertIn("idempotency_key is null", self.sql)
        self.assertIn("registros históricos com chave nula", self.sql)
        self.assertIn("comment on column public.compras.idempotency_key", self.sql)


if __name__ == "__main__":
    unittest.main()
