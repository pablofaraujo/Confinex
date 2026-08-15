#!/usr/bin/env python3
"""Contratos da ponte local usada pelo sandbox do Juan."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from confinex_client import ConfinexClient, ConfinexConnectionError
from confinex_db_bridge import BridgeError, action_request, execute_action


class BridgeActionTests(unittest.TestCase):
    def test_review_insert_accepts_only_non_operational_table(self):
        method, route, body = action_request(
            "post_review",
            ["pending_actions", json.dumps({"status": "aguardando_confirmacao"})],
        )
        self.assertEqual((method, route), ("POST", "pending_actions"))
        self.assertEqual(json.loads(body), {"status": "aguardando_confirmacao"})
        with self.assertRaisesRegex(BridgeError, "recurso_revisao_nao_permitido"):
            action_request("post_review", ["compras", "{}"])

    def test_review_patch_requires_filter_and_blocks_operational_table(self):
        method, route, body = action_request(
            "patch_review",
            ["operation_drafts", "id=eq.teste", json.dumps({"status": "erro"})],
        )
        self.assertEqual((method, route), ("PATCH", "operation_drafts?id=eq.teste"))
        self.assertEqual(json.loads(body), {"status": "erro"})
        with self.assertRaisesRegex(BridgeError, "filtro_revisao_invalido"):
            action_request("patch_review", ["operation_drafts", "", "{}"])
        with self.assertRaisesRegex(BridgeError, "recurso_revisao_nao_permitido"):
            action_request("patch_review", ["vendas", "id=eq.teste", "{}"])

    def test_write_network_failure_is_never_retried(self):
        opener = mock.Mock(side_effect=urllib.error.URLError("sem rede"))
        with self.assertRaisesRegex(BridgeError, "tentativas=1"):
            execute_action(
                "post_review",
                ["pending_actions", "{}"],
                url="https://example.invalid",
                key="teste",
                opener=opener,
            )
        self.assertEqual(opener.call_count, 1)

    def test_read_network_failure_uses_bounded_retries(self):
        opener = mock.Mock(side_effect=urllib.error.URLError("sem rede"))
        with mock.patch("confinex_db_bridge.time.sleep"):
            with self.assertRaisesRegex(BridgeError, "tentativas=5"):
                execute_action(
                    "get_read",
                    ["pending_actions?select=id&limit=1"],
                    url="https://example.invalid",
                    key="teste",
                    opener=opener,
                )
        self.assertEqual(opener.call_count, 5)


class ConfinexClientBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.bridge = root / "bridge.py"
        self.ready = root / ".ready"
        self.bridge.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
        self.ready.write_text("1", encoding="utf-8")
        self.client = ConfinexClient(
            url="https://example.invalid",
            key="segredo-nao-deve-ir-ao-subprocesso",
            bridge_path=self.bridge,
            bridge_ready_path=self.ready,
        )

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def completed(body: str, status: int = 200, returncode: int = 0):
        return subprocess.CompletedProcess(
            args=[], returncode=returncode, stdout=f"{body}\nHTTP_STATUS:{status}\n", stderr=""
        )

    def test_select_uses_bridge_without_credentials_in_command(self):
        with mock.patch(
            "confinex_client.subprocess.run",
            return_value=self.completed('[{"id":"ok"}]'),
        ) as runner, mock.patch("confinex_client.urllib.request.urlopen") as direct:
            rows = self.client.select("pending_actions", select="id", limit=1)
        self.assertEqual(rows, [{"id": "ok"}])
        command = runner.call_args.args[0]
        self.assertIn("get_read", command)
        self.assertNotIn(self.client.key, command)
        direct.assert_not_called()

    def test_insert_and_update_review_use_bridge(self):
        responses = [
            self.completed('[{"id":"acao"}]', 201),
            self.completed('[{"id":"acao","status":"erro"}]'),
        ]
        with mock.patch("confinex_client.subprocess.run", side_effect=responses) as runner:
            inserted = self.client.insert("pending_actions", {"status": "aguardando_confirmacao"})
            updated = self.client.update("pending_actions", {"id": "eq.acao"}, {"status": "erro"})
        self.assertEqual(inserted["id"], "acao")
        self.assertEqual(updated[0]["status"], "erro")
        self.assertIn("post_review", runner.call_args_list[0].args[0])
        self.assertIn("patch_review", runner.call_args_list[1].args[0])

    def test_bridge_failure_is_classified_without_direct_fallback(self):
        with mock.patch(
            "confinex_client.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=[], returncode=2, stdout='{"error":"rede_indisponivel"}\n', stderr=""
            ),
        ), mock.patch("confinex_client.urllib.request.urlopen") as direct:
            with self.assertRaises(ConfinexConnectionError):
                self.client.insert("pending_actions", {"status": "teste"})
        direct.assert_not_called()


if __name__ == "__main__":
    unittest.main()
