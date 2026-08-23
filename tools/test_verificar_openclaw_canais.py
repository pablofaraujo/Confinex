import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools.verificar_openclaw_canais import (
    configuracoes_modelos,
    criar_xlsx_minimo,
    grupos_configurados,
    ids_grupos,
    validar_agentes,
    validar_canais,
    validar_confinex,
    validar_modelos,
    validar_monitor_agronota,
    validar_probe_modelos,
    validar_indice_sessoes,
    validar_roteador_xlsx,
    reparar_indice_sessoes,
    reparar,
)


class VerificarOpenClawCanaisTest(unittest.TestCase):
    def test_deduplica_configuracoes_de_modelos_por_contrato(self):
        config = {"agents": {
            "defaults": {"model": {
                "primary": "openai/gpt-5.5",
                "fallbacks": ["anthropic/claude-sonnet-4-6"],
            }},
            "list": [
                {"id": "juan"},
                {"id": "ceci"},
                {"id": "wey", "model": {
                    "primary": "openai/gpt-5.4",
                    "fallbacks": ["anthropic/claude-sonnet-4-6"],
                }},
                {"id": "zeus", "model": {
                    "primary": "openai/gpt-5.4",
                    "fallbacks": ["anthropic/claude-sonnet-4-6"],
                }},
            ],
        }}
        self.assertEqual(configuracoes_modelos(config), [
            {
                "agente": "juan",
                "primario": "openai/gpt-5.5",
                "fallbacks": ["anthropic/claude-sonnet-4-6"],
            },
            {
                "agente": "wey",
                "primario": "openai/gpt-5.4",
                "fallbacks": ["anthropic/claude-sonnet-4-6"],
            },
        ])

    def test_probe_detecta_primario_e_fallback_indisponiveis(self):
        payload = {"auth": {"probes": {"results": [
            {"model": "openai/gpt-5.5", "status": "ok"},
            {"model": "anthropic/claude-sonnet-4-6", "status": "auth"},
        ]}}}
        self.assertEqual(validar_probe_modelos(
            payload,
            primario="openai/gpt-5.5",
            fallbacks=["anthropic/claude-sonnet-4-6"],
        ), ["modelo_fallback_indisponivel:anthropic/claude-sonnet-4-6"])
        self.assertIn(
            "modelo_primario_indisponivel:openai/gpt-5.4",
            validar_probe_modelos(
                payload,
                primario="openai/gpt-5.4",
                fallbacks=[],
            ),
        )

    @patch("tools.verificar_openclaw_canais.json_comando")
    def test_probe_modelos_usa_cache_sanitizado(self, comando):
        comando.return_value = {"auth": {"probes": {"results": [
            {"model": "openai/gpt-5.5", "status": "ok"},
            {"model": "anthropic/claude-sonnet-4-6", "status": "auth"},
        ]}}}
        config = {"agents": {"defaults": {"model": {
            "primary": "openai/gpt-5.5",
            "fallbacks": ["anthropic/claude-sonnet-4-6"],
        }}, "list": [{"id": "juan"}]}}
        with tempfile.TemporaryDirectory() as pasta:
            cache = Path(pasta) / "probe.json"
            primeira = validar_modelos(
                config, ambiente={}, cache=cache, intervalo=1800, forcar=True,
            )
            segunda = validar_modelos(
                config, ambiente={}, cache=cache, intervalo=1800,
            )
            conteudo = json.loads(cache.read_text())
        self.assertEqual(primeira, segunda)
        self.assertEqual(comando.call_count, 1)
        self.assertEqual(set(conteudo), {"timestamp", "falhas"})
        self.assertNotIn("auth", conteudo)

    @patch("tools.verificar_openclaw_canais.json_comando")
    def test_modelo_compartilhado_entre_primario_e_fallback_nao_falha(self, comando):
        comando.side_effect = [
            {"auth": {"probes": {"results": [
                {"model": "openai/gpt-5.5", "status": "ok"},
            ]}}},
            {"auth": {"probes": {"results": [
                {"model": "openai/gpt-5.4", "status": "ok"},
            ]}}},
        ]
        config = {"agents": {"list": [
            {"id": "juan", "model": {
                "primary": "openai/gpt-5.5",
                "fallbacks": ["openai/gpt-5.4"],
            }},
            {"id": "wey", "model": {
                "primary": "openai/gpt-5.4",
                "fallbacks": [],
            }},
        ]}}
        with tempfile.TemporaryDirectory() as pasta:
            falhas = validar_modelos(
                config,
                ambiente={},
                cache=Path(pasta) / "probe.json",
                intervalo=1800,
                forcar=True,
            )
        self.assertEqual([], falhas)

    def test_roteador_xlsx_executa_previa_sem_gravacao(self):
        with tempfile.TemporaryDirectory() as pasta:
            raiz = Path(pasta)
            xlsx = raiz / "fixture.xlsx"
            criar_xlsx_minimo(xlsx)
            self.assertTrue(xlsx.is_file())
            roteador = raiz / "router.py"
            roteador.write_text(
                "import json\nprint(json.dumps({'dry_run': True, 'routed': "
                "{'classe': 'planilha_xlsx', 'dados': {'importado': False}}}))\n"
            )
            self.assertEqual([], validar_roteador_xlsx(roteador))

    def test_roteador_xlsx_ausente_ou_invalido_falha(self):
        with tempfile.TemporaryDirectory() as pasta:
            raiz = Path(pasta)
            self.assertEqual(
                ["roteador_arquivo_ausente"],
                validar_roteador_xlsx(raiz / "ausente.py"),
            )
            roteador = raiz / "router.py"
            roteador.write_text("raise SystemExit(1)\n")
            self.assertEqual(
                ["roteador_xlsx_indisponivel"],
                validar_roteador_xlsx(roteador),
            )

    def test_indice_sessoes_detecta_apenas_referencia_ausente(self):
        self.assertEqual(
            ["indice_sessoes_inconsistente:juan"],
            validar_indice_sessoes({"missing": 2, "pruned": 0}, "juan"),
        )
        self.assertEqual([], validar_indice_sessoes({"missing": 0}, "juan"))
        self.assertEqual(
            ["probe_indice_sessoes_falhou:juan"],
            validar_indice_sessoes(None, "juan"),
        )

    @patch("tools.verificar_openclaw_canais.json_comando")
    def test_reparo_indice_sessoes_e_localizado_e_confirmado(self, comando):
        comando.side_effect = [
            {"missing": 2, "pruned": 0, "capped": 0,
             "dmScopeRetired": 0, "unreferencedArtifacts": {"removedFiles": []}},
            {"missing": 2},
            {"missing": 0},
        ]
        self.assertTrue(reparar_indice_sessoes("juan"))
        self.assertEqual(comando.call_count, 3)
        self.assertIn("--dry-run", comando.call_args_list[0].args[0])
        self.assertNotIn("--dry-run", comando.call_args_list[1].args[0])

    @patch("tools.verificar_openclaw_canais.json_comando")
    def test_reparo_indice_recusa_efeitos_fora_de_referencias_ausentes(self, comando):
        comando.return_value = {
            "missing": 1,
            "pruned": 1,
            "capped": 0,
            "dmScopeRetired": 0,
        }
        self.assertFalse(reparar_indice_sessoes("juan"))
        self.assertEqual(comando.call_count, 1)

    def test_heartbeat_fiscal_valida_agendamento_arquivos_e_frescor(self):
        with tempfile.TemporaryDirectory() as pasta:
            raiz = Path(pasta)
            arquivos = (raiz / "parser.py", raiz / "monitor.py", raiz / "download.py")
            for arquivo in arquivos:
                arquivo.write_text("# teste\n")
            log = raiz / "agronota.log"
            log.write_text("ok\n")
            cron = (
                "30 4 * * * /root/bin/agronota_pipeline.sh >> /var/log/agronota_pipeline.log\n"
                "15 11,15,19 * * * /root/bin/agronota_pipeline.sh >> /var/log/agronota_pipeline.log\n"
            )
            agora = log.stat().st_mtime + 60
            self.assertEqual([], validar_monitor_agronota(
                cron=cron, log=log, arquivos=arquivos, agora=agora,
            ))

    def test_heartbeat_fiscal_detecta_monitor_atrasado_e_agendamento_incompleto(self):
        with tempfile.TemporaryDirectory() as pasta:
            raiz = Path(pasta)
            arquivo = raiz / "parser.py"
            arquivo.write_text("# teste\n")
            log = raiz / "agronota.log"
            log.write_text("antigo\n")
            falhas = validar_monitor_agronota(
                cron="30 4 * * * /root/bin/agronota_pipeline.sh\n",
                log=log,
                arquivos=(arquivo, raiz / "ausente.py"),
                agora=log.stat().st_mtime + 12 * 60 * 60,
            )
            self.assertEqual(falhas, [
                "agronota_incremental_ausente",
                "agronota_monitor_atrasado",
                "agronota_monitor_ausente",
            ])

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

    @patch("tools.verificar_openclaw_canais.urllib.request.urlopen")
    @patch("tools.verificar_openclaw_canais.socket.getaddrinfo")
    def test_valida_leitura_autenticada_minima_do_confinex(self, dns, abrir):
        dns.return_value = [(None, None, None, None, None)]
        resposta = Mock(status=200)
        resposta.__enter__ = Mock(return_value=resposta)
        resposta.__exit__ = Mock(return_value=False)
        abrir.return_value = resposta
        falhas = validar_confinex({
            "CONFINEX_DB_URL": "https://projeto.supabase.co",
            "CONFINEX_DB_KEY": "segredo-de-teste",
        })
        self.assertEqual([], falhas)
        requisicao = abrir.call_args.args[0]
        self.assertEqual(
            requisicao.full_url,
            "https://projeto.supabase.co/rest/v1/operacoes?select=id&limit=1",
        )

    @patch("tools.verificar_openclaw_canais.socket.getaddrinfo")
    def test_detecta_dns_do_confinex_indisponivel(self, dns):
        import socket

        dns.side_effect = socket.gaierror("falha simulada")
        self.assertEqual(
            ["confinex_dns_indisponivel"],
            validar_confinex({
                "CONFINEX_DB_URL": "https://projeto.supabase.co",
                "CONFINEX_DB_KEY": "segredo-de-teste",
            }),
        )

    def test_saida_e_unidades_nao_enviam_mensagem_a_grupos(self):
        raiz = Path(__file__).parents[1]
        fonte = (raiz / "tools/verificar_openclaw_canais.py").read_text()
        unidade = (raiz / "infra/systemd/openclaw-agent-heartbeat.service").read_text()
        self.assertNotIn("openclaw message send", fonte + unidade)
        self.assertNotRegex(fonte + unidade, r"-[0-9]{8,}")
        self.assertIn("--reparar", unidade)
        self.assertIn("XDG_RUNTIME_DIR=/run/user/0", unidade)
        self.assertIn("DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/0/bus", unidade)
        self.assertIn("validar_confinex", fonte)
        self.assertIn("confinex_bridge_inativa", fonte)
        self.assertIn("juan-confinex-db-bridge.service", fonte)
        self.assertIn("confinex_bridge_reiniciada", fonte)
        self.assertIn("validar_modelos", fonte)
        self.assertIn("--probe-max-tokens", fonte)
        self.assertIn("validar_roteador_xlsx", fonte)
        self.assertIn("validar_indice_sessoes", fonte)
        self.assertIn("--fix-missing", fonte)
        self.assertIn("--dry-run", fonte)

    @patch("tools.verificar_openclaw_canais.reiniciar", return_value=True)
    def test_repara_ponte_sem_reiniciar_gateway(self, reiniciar):
        args = SimpleNamespace(
            confinex_bridge_service="juan-confinex-db-bridge.service",
            gateway_service="openclaw-gateway.service",
        )
        acoes = reparar(args, ["confinex_bridge_inativa"])
        self.assertEqual(acoes, ["confinex_bridge_reiniciada"])
        reiniciar.assert_called_once_with(
            "juan-confinex-db-bridge.service", usuario=True
        )

    @patch("tools.verificar_openclaw_canais.reiniciar", return_value=True)
    def test_repara_dns_no_resolvedor_do_host(self, reiniciar):
        args = SimpleNamespace(
            dns_service="systemd-resolved.service",
            gateway_service="openclaw-gateway.service",
        )
        acoes = reparar(args, ["confinex_dns_indisponivel"])
        self.assertEqual(acoes, ["dns_resolver_reiniciado"])
        reiniciar.assert_called_once_with("systemd-resolved.service")


if __name__ == "__main__":
    unittest.main()
