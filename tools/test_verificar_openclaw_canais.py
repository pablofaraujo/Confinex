import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tools.verificar_openclaw_canais import (
    grupos_configurados,
    ids_grupos,
    validar_agentes,
    validar_canais,
)


class VerificarOpenClawCanaisTest(unittest.TestCase):
    def test_valida_todas_as_contas_e_whatsapp(self):
        payload = {
            "eventLoop": {"degraded": False},
            "channelAccounts": {"telegram": [
                {"accountId": "default", "configured": True, "running": True,
                 "probe": {"ok": True}},
                {"accountId": "ceci", "configured": True, "running": True,
                 "probe": {"ok": True}},
            ]},
            "channels": {"whatsapp": {
                "configured": True, "linked": True, "running": True,
                "connected": True, "healthState": "healthy",
            }},
        }
        self.assertEqual([], validar_canais(payload))

    def test_detecta_canal_e_event_loop_degradados(self):
        payload = {
            "eventLoop": {"degraded": True},
            "channelAccounts": {"telegram": []},
            "channels": {"whatsapp": {}},
        }
        falhas = validar_canais(payload)
        self.assertIn("telegram_indisponivel:default", falhas)
        self.assertIn("telegram_indisponivel:ceci", falhas)
        self.assertIn("whatsapp_openclaw_indisponivel", falhas)
        self.assertIn("gateway_event_loop_degradado", falhas)

    def test_valida_agentes_vinculos_e_arquivos(self):
        with tempfile.TemporaryDirectory() as pasta:
            raiz = Path(pasta)
            payload = []
            for agente, vinculos in (("juan", 1), ("ceci", 1), ("wey", 1), ("zeus", 0)):
                workspace = raiz / agente / "workspace"
                agent_dir = raiz / agente / "agent"
                workspace.mkdir(parents=True)
                agent_dir.mkdir(parents=True)
                (agent_dir / "AGENT.md").write_text("ok")
                payload.append({"id": agente, "bindings": vinculos,
                                "workspace": str(workspace), "agentDir": str(agent_dir)})
            self.assertEqual([], validar_agentes(payload))

    def test_compara_grupos_sem_expor_identificadores(self):
        config = {"channels": {"telegram": {"accounts": {"default": {
            "groups": {"grupo-a": {}, "grupo-b": {}}
        }}}}}
        esperados = grupos_configurados(config, "default")
        encontrados = ids_grupos([{"id": "grupo-a"}, {"id": "grupo-b"}])
        self.assertEqual(esperados, encontrados)

    def test_saida_e_unidades_nao_enviam_mensagem_a_grupos(self):
        raiz = Path(__file__).parents[1]
        fonte = (raiz / "tools/verificar_openclaw_canais.py").read_text()
        unidade = (raiz / "infra/systemd/openclaw-agent-heartbeat.service").read_text()
        self.assertNotIn("openclaw message send", fonte + unidade)
        self.assertNotRegex(fonte + unidade, r"-[0-9]{8,}")
        self.assertIn("--reparar", unidade)


if __name__ == "__main__":
    unittest.main()
