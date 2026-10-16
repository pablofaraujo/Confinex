import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools.verificar_openclaw_canais import (
    configuracoes_modelos,
    criar_xlsx_minimo,
    consultar_readyz,
    grupos_configurados,
    ids_grupos,
    executar,
    validar_agentes,
    validar_agentes_configuracao,
    validar_autenticacao_wacli,
    validar_canais,
    validar_status_gateway,
    validar_whatsapp_por_logs,
    validar_reautenticacao_whatsapp_openclaw,
    validar_confinex,
    validar_diretorio_grupos,
    validar_modelos,
    validar_monitor_agronota,
    validar_probe_modelos,
    validar_indice_sessoes,
    validar_indices_sessoes,
    validar_roteador_xlsx,
    reparar_indice_sessoes,
    reparar,
)


class VerificarOpenClawCanaisTest(unittest.TestCase):
    def test_timeout_encerra_todo_o_grupo_de_processos(self):
        with tempfile.TemporaryDirectory() as pasta:
            pidfile = Path(pasta) / "filho.pid"
            programa = (
                "import pathlib, subprocess, sys, time; "
                "filho=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
                "pathlib.Path(sys.argv[1]).write_text(str(filho.pid)); "
                "time.sleep(60)"
            )
            resultado = executar(
                [sys.executable, "-c", programa, str(pidfile)], timeout=1,
            )
            self.assertEqual(124, resultado.codigo)
            pid_filho = int(pidfile.read_text())
            for _ in range(20):
                try:
                    os.kill(pid_filho, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.1)
            else:
                self.fail(f"processo filho {pid_filho} sobreviveu ao timeout")

    @patch("tools.verificar_openclaw_canais.consultar_telegram_bot")
    def test_diretorio_grupos_e_serial_e_usa_cache(self, consultar):
        consultar.side_effect = [
            {"ok": True, "result": {"id": 101}},
            {"ok": True, "result": {"status": "member"}},
            {"ok": True, "result": {"id": 202}},
            {"ok": True, "result": {"status": "administrator"}},
        ]
        config = {"channels": {"telegram": {"accounts": {
            "default": {"botToken": "token-juan", "groups": {"grupo-juan": {}}},
            "ceci": {"botToken": "token-ceci", "groups": {"grupo-ceci": {}}},
        }}}}
        with tempfile.TemporaryDirectory() as pasta:
            cache = Path(pasta) / "grupos.json"
            self.assertEqual([], validar_diretorio_grupos(
                config, ambiente={}, cache=cache, intervalo=6 * 60 * 60,
            ))
            self.assertEqual(4, consultar.call_count)
            self.assertEqual([], validar_diretorio_grupos(
                config, ambiente={}, cache=cache, intervalo=6 * 60 * 60,
            ))
            self.assertEqual(4, consultar.call_count)
        self.assertEqual(
            ["getMe", "getChatMember", "getMe", "getChatMember"],
            [chamada.args[1] for chamada in consultar.call_args_list],
        )

    @patch("tools.verificar_openclaw_canais.consultar_telegram_bot")
    def test_diretorio_detecta_bot_removido_sem_expor_grupo(self, consultar):
        consultar.side_effect = [
            {"ok": True, "result": {"id": 101}},
            {"ok": True, "result": {"status": "left"}},
            {"ok": True, "result": {"id": 202}},
            {"ok": True, "result": {"status": "member"}},
        ]
        config = {"channels": {"telegram": {"accounts": {
            "default": {"botToken": "token-juan", "groups": {"grupo-secreto": {}}},
            "ceci": {"botToken": "token-ceci", "groups": {"grupo-ceci": {}}},
        }}}}
        with tempfile.TemporaryDirectory() as pasta:
            falhas = validar_diretorio_grupos(
                config, ambiente={}, cache=Path(pasta) / "grupos.json",
                intervalo=240,
            )
        self.assertEqual(["grupo_telegram_inacessivel:default"], falhas)
        self.assertNotIn("grupo-secreto", json.dumps(falhas))

    def test_configuracoes_modelos_aceita_schema_entries_atual(self):
        config = {"agents": {
            "defaults": {"model": {
                "primary": "anthropic/claude-sonnet-4-6",
                "fallbacks": ["openai/gpt-5.6-sol"],
            }},
            "entries": {
                "juan": {},
                "ceci": {"model": {
                    "primary": "anthropic/claude-opus-4-6",
                    "fallbacks": [],
                }},
            },
        }}
        self.assertEqual(configuracoes_modelos(config), [
            {
                "agente": "juan",
                "primario": "anthropic/claude-sonnet-4-6",
                "fallbacks": ["openai/gpt-5.6-sol"],
            },
            {
                "agente": "ceci",
                "primario": "anthropic/claude-opus-4-6",
                "fallbacks": [],
            },
        ])

    def test_agentes_configurados_sao_validados_sem_cli(self):
        with tempfile.TemporaryDirectory() as pasta:
            raiz = Path(pasta)
            entradas = {}
            bindings = []
            for agente, minimo in {"juan": 1, "ceci": 1, "wey": 1, "zeus": 0}.items():
                workspace = raiz / f"workspace-{agente}"
                agent_dir = raiz / "agents" / agente / "agent"
                workspace.mkdir()
                agent_dir.mkdir(parents=True)
                (agent_dir / "AGENT.md").write_text("# teste\n")
                entradas[agente] = {
                    "workspace": str(workspace), "agentDir": str(agent_dir),
                }
                if minimo:
                    bindings.append({"agentId": agente, "match": {}})
            self.assertEqual([], validar_agentes_configuracao({
                "agents": {"entries": entradas}, "bindings": bindings,
            }))

    @patch("pathlib.Path.expanduser")
    def test_agentes_expandem_til_da_configuracao(self, expandir):
        expandir.return_value.is_dir.return_value = True
        expandir.return_value.__truediv__.return_value.is_file.return_value = True
        config = {
            "agents": {"entries": {
                agente: {"workspace": f"~/.openclaw/workspace-{agente}"}
                for agente in ("juan", "ceci", "wey", "zeus")
            }},
            "bindings": [
                {"agentId": agente, "match": {}}
                for agente in ("juan", "ceci", "wey")
            ],
        }
        self.assertEqual([], validar_agentes_configuracao(config))
        self.assertGreaterEqual(expandir.call_count, 8)

    def test_whatsapp_usa_ultimo_estado_observado_nos_logs(self):
        self.assertEqual([], validar_whatsapp_por_logs(
            "Web connection closed (status 408)\n"
            "Listening for WhatsApp inbound messages (DM + 1 configured group).\n"
        ))
        self.assertEqual(
            ["whatsapp_openclaw_indisponivel"],
            validar_whatsapp_por_logs(
                "Listening for WhatsApp inbound messages\nWeb connection closed (status 408)\n"
            ),
        )
        self.assertEqual(
            ["whatsapp_openclaw_reautenticacao_necessaria"],
            validar_whatsapp_por_logs(
                "Listening for WhatsApp inbound messages\nWhatsApp session logged out\n"
            ),
        )

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
            "modelo_primario_sem_resposta:openai/gpt-5.4",
            validar_probe_modelos(
                payload,
                primario="openai/gpt-5.4",
                fallbacks=[],
            ),
        )

    def test_probe_sem_resposta_nao_prova_indisponibilidade(self):
        for payload in (None, {}, {"auth": {"probes": {"results": []}}}):
            with self.subTest(payload=payload):
                falhas = validar_probe_modelos(
                    payload,
                    primario="openai/gpt-5.5",
                    fallbacks=["openai/gpt-5.4"],
                )
                self.assertTrue(any("probe_modelos_" in falha for falha in falhas))
                self.assertFalse(any("indisponivel" in falha for falha in falhas))

    def test_probe_resultado_explicito_negativo_prova_indisponibilidade(self):
        payload = {"auth": {"probes": {"results": [
            {"model": "openai/gpt-5.5", "status": "auth"},
            {"model": "openai/gpt-5.4", "status": "erro"},
        ]}}}
        self.assertEqual(validar_probe_modelos(
            payload,
            primario="openai/gpt-5.5",
            fallbacks=["openai/gpt-5.4"],
        ), [
            "modelo_primario_indisponivel:openai/gpt-5.5",
            "modelo_fallback_indisponivel:openai/gpt-5.4",
        ])

    @patch("tools.verificar_openclaw_canais.json_comando")
    def test_validar_modelos_sem_payload_nao_inventa_indisponibilidade(self, comando):
        comando.return_value = None
        config = {"agents": {"list": [{"id": "juan", "model": {
            "primary": "openai/gpt-5.5",
            "fallbacks": ["openai/gpt-5.4"],
        }}]}}
        with tempfile.TemporaryDirectory() as pasta:
            falhas = validar_modelos(
                config,
                ambiente={},
                cache=Path(pasta) / "probe.json",
                intervalo=1800,
                forcar=True,
            )
        self.assertEqual(falhas, ["probe_modelos_falhou:juan"])

    @patch("tools.verificar_openclaw_canais.json_comando")
    def test_validar_modelos_results_vazio_nao_inventa_indisponibilidade(self, comando):
        comando.return_value = {"auth": {"probes": {"results": []}}}
        config = {"agents": {"list": [{"id": "juan", "model": {
            "primary": "openai/gpt-5.5",
            "fallbacks": ["openai/gpt-5.4"],
        }}]}}
        with tempfile.TemporaryDirectory() as pasta:
            falhas = validar_modelos(
                config,
                ambiente={},
                cache=Path(pasta) / "probe.json",
                intervalo=1800,
                forcar=True,
            )
        self.assertEqual(falhas, ["probe_modelos_falhou:juan"])

    @patch("tools.verificar_openclaw_canais.json_comando")
    def test_validar_modelos_payloads_malformados_falham_fechado(self, comando):
        payloads = [
            {"auth": "texto"},
            {"auth": {"probes": []}},
            {"auth": {"probes": {"results": 12}}},
            {"auth": {"probes": {"results": {"model": "openai/gpt-5.5", "status": "ok"}}}},
            {"auth": {"probes": {"results": [{}]}}},
            {"auth": {"probes": {"results": [{"model": "openai/gpt-5.5"}]}}},
            {"auth": {"probes": {"results": [{"model": 12, "status": "ok"}]}}},
            {"auth": {"probes": {"results": [{"model": "openai/gpt-5.5", "status": True}]}}},
        ]
        config = {"agents": {"list": [{"id": "juan", "model": {
            "primary": "openai/gpt-5.5", "fallbacks": [],
        }}]}}
        for payload in payloads:
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as pasta:
                comando.return_value = payload
                falhas = validar_modelos(
                    config,
                    ambiente={},
                    cache=Path(pasta) / "probe.json",
                    intervalo=1800,
                    forcar=True,
                )
                self.assertEqual(falhas, ["probe_modelos_falhou:juan"])
                self.assertFalse(any("indisponivel" in falha for falha in falhas))

    @patch("tools.verificar_openclaw_canais.json_comando")
    def test_primario_ok_com_fallback_ausente_permanece_bloqueado(self, comando):
        comando.return_value = {"auth": {"probes": {"results": [
            {"model": "openai/gpt-5.5", "status": "ok"},
        ]}}}
        config = {"agents": {"list": [{"id": "juan", "model": {
            "primary": "openai/gpt-5.5", "fallbacks": ["openai/gpt-5.4"],
        }}]}}
        with tempfile.TemporaryDirectory() as pasta:
            falhas = validar_modelos(
                config, ambiente={}, cache=Path(pasta) / "probe.json",
                intervalo=1800, forcar=True,
            )
        self.assertEqual(falhas, ["probe_modelos_falhou:juan"])

    @patch("tools.verificar_openclaw_canais.json_comando")
    def test_resultado_negativo_explicito_no_chamador_e_indisponibilidade(self, comando):
        comando.return_value = {"auth": {"probes": {"results": [
            {"model": "openai/gpt-5.5", "status": "auth"},
        ]}}}
        config = {"agents": {"list": [{"id": "juan", "model": {
            "primary": "openai/gpt-5.5", "fallbacks": [],
        }}]}}
        with tempfile.TemporaryDirectory() as pasta:
            falhas = validar_modelos(
                config, ambiente={}, cache=Path(pasta) / "probe.json",
                intervalo=1800, forcar=True,
            )
        self.assertEqual(falhas, ["modelo_primario_indisponivel:openai/gpt-5.5"])

    @patch("tools.verificar_openclaw_canais.json_comando")
    def test_modelo_inesperado_nao_prova_indisponibilidade_configurada(self, comando):
        comando.return_value = {"auth": {"probes": {"results": [
            {"model": "modelo/inesperado", "status": "ok"},
        ]}}}
        config = {"agents": {"list": [{"id": "juan", "model": {
            "primary": "openai/gpt-5.5", "fallbacks": [],
        }}]}}
        with tempfile.TemporaryDirectory() as pasta:
            falhas = validar_modelos(
                config, ambiente={}, cache=Path(pasta) / "probe.json",
                intervalo=1800, forcar=True,
            )
        self.assertEqual(falhas, ["probe_modelos_falhou:juan"])

    @patch("tools.verificar_openclaw_canais.json_comando")
    def test_falha_de_um_agente_nao_interrompe_validacao_do_seguinte(self, comando):
        comando.side_effect = [
            {"auth": "malformado"},
            {"auth": {"probes": {"results": [
                {"model": "openai/gpt-5.4", "status": "ok"},
            ]}}},
        ]
        config = {"agents": {"list": [
            {"id": "juan", "model": {"primary": "openai/gpt-5.5", "fallbacks": []}},
            {"id": "wey", "model": {"primary": "openai/gpt-5.4", "fallbacks": []}},
        ]}}
        with tempfile.TemporaryDirectory() as pasta:
            falhas = validar_modelos(
                config, ambiente={}, cache=Path(pasta) / "probe.json",
                intervalo=1800, forcar=True,
            )
        self.assertEqual(falhas, ["probe_modelos_falhou:juan"])
        self.assertEqual(comando.call_count, 2)

    @patch("tools.verificar_openclaw_canais.json_comando")
    def test_status_ok_precisa_ser_exato(self, comando):
        comando.return_value = {"auth": {"probes": {"results": [
            {"model": "openai/gpt-5.5", "status": "OK"},
        ]}}}
        config = {"agents": {"list": [{"id": "juan", "model": {
            "primary": "openai/gpt-5.5", "fallbacks": [],
        }}]}}
        with tempfile.TemporaryDirectory() as pasta:
            falhas = validar_modelos(
                config,
                ambiente={},
                cache=Path(pasta) / "probe.json",
                intervalo=1800,
                forcar=True,
            )
        self.assertEqual(falhas, ["modelo_primario_indisponivel:openai/gpt-5.5"])

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

    @patch("tools.verificar_openclaw_canais.executar")
    @patch("tools.verificar_openclaw_canais.json_comando")
    def test_detecta_reautenticacao_wacli_sem_expor_conta(self, comando, executar):
        comando.return_value = {
            "success": True,
            "data": {"authenticated": False, "linked_jid": "nao-expor"},
        }
        executar.return_value = Mock(codigo=0, stdout="", stderr="")
        self.assertEqual(
            ["wacli_reautenticacao_necessaria"],
            validar_autenticacao_wacli(Path("/wacli"), Path("/store")),
        )

    @patch("tools.verificar_openclaw_canais.executar")
    @patch("tools.verificar_openclaw_canais.json_comando")
    def test_aceita_wacli_autenticado(self, comando, executar):
        comando.return_value = {"data": {"authenticated": True}}
        executar.return_value = Mock(codigo=0, stdout="", stderr="")
        with patch("pathlib.Path.stat") as stat:
            stat.return_value.st_mtime = 100
            self.assertEqual(
                [], validar_autenticacao_wacli(Path("/wacli"), Path("/store")),
            )

    @patch("tools.verificar_openclaw_canais.executar")
    @patch("tools.verificar_openclaw_canais.json_comando")
    def test_detecta_401_mesmo_com_doctor_autenticado(self, comando, executar):
        comando.return_value = {"data": {"authenticated": True}}
        executar.return_value = Mock(
            codigo=0,
            stdout="401: logged out from another device",
            stderr="",
        )
        with patch("pathlib.Path.stat") as stat:
            stat.return_value.st_mtime = 100
            self.assertEqual(
                ["wacli_reautenticacao_necessaria"],
                validar_autenticacao_wacli(Path("/wacli"), Path("/store")),
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
    def test_indices_sessoes_usam_cache_de_seis_horas(self, comando):
        comando.return_value = {"missing": 0}
        with tempfile.TemporaryDirectory() as pasta:
            cache = Path(pasta) / "indices.json"
            self.assertEqual([], validar_indices_sessoes(
                cache=cache, intervalo=6 * 60 * 60,
            ))
            self.assertEqual(4, comando.call_count)
            self.assertEqual([], validar_indices_sessoes(
                cache=cache, intervalo=6 * 60 * 60,
            ))
            self.assertEqual(4, comando.call_count)

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

    def test_detecta_canais_degradados_sem_reagir_a_cpu_transitoria(self):
        payload = {
            "eventLoop": {"degraded": True},
            "channelAccounts": {"telegram": []},
            "channels": {"whatsapp": {}},
        }
        falhas = validar_canais(payload)
        self.assertIn("telegram_indisponivel:default", falhas)
        self.assertIn("telegram_indisponivel:ceci", falhas)
        self.assertIn("whatsapp_openclaw_indisponivel", falhas)
        self.assertNotIn("gateway_event_loop_degradado", falhas)

    def test_status_gateway_online_e_runtime_saudavel(self):
        self.assertEqual([], validar_status_gateway({
            "gateway": {"reachable": True},
            "health": {
                "ok": True,
                "modelRuntime": {"degraded": False, "pendingAgents": []},
            },
        }))

    def test_health_leve_online_e_runtime_saudavel(self):
        self.assertEqual([], validar_status_gateway({
            "ok": True,
            "modelRuntime": {"degraded": False, "pendingAgents": []},
        }))

    def test_readyz_online(self):
        self.assertEqual([], validar_status_gateway({
            "ready": True,
            "failing": [],
        }))
        self.assertEqual(
            ["gateway_health_indisponivel"],
            validar_status_gateway({"ready": False, "failing": ["channels"]}),
        )

    def test_status_gateway_distingue_runtime_degradado(self):
        self.assertEqual(validar_status_gateway({
            "gateway": {"reachable": True},
            "health": {
                "ok": True,
                "modelRuntime": {"degraded": True, "pendingAgents": ["juan"]},
            },
        }), ["runtime_modelos_degradado"])

    def test_status_gateway_falha_fechado_sem_payload(self):
        self.assertEqual(
            validar_status_gateway(None), ["probe_gateway_falhou"]
        )

    def test_detecta_whatsapp_openclaw_exigindo_novo_pareamento(self):
        payload = {
            "channels": {"whatsapp": {
                "configured": True, "linked": True, "running": False,
                "connected": False, "healthState": "not-running",
            }},
        }
        self.assertEqual(
            ["whatsapp_openclaw_reautenticacao_necessaria"],
            validar_reautenticacao_whatsapp_openclaw(
                payload, "WhatsApp session logged out. Run channels login",
            ),
        )

    def test_log_antigo_nao_bloqueia_whatsapp_openclaw_saudavel(self):
        payload = {
            "channels": {"whatsapp": {
                "configured": True, "linked": True, "running": True,
                "connected": True, "healthState": "healthy",
            }},
        }
        self.assertEqual([], validar_reautenticacao_whatsapp_openclaw(
            payload, "session logged out during setup",
        ))

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
        timer = (raiz / "infra/systemd/openclaw-agent-heartbeat.timer").read_text()
        self.assertNotIn("openclaw message send", fonte + unidade)
        self.assertNotRegex(fonte + unidade, r"-[0-9]{8,}")
        self.assertIn("--reparar", unidade)
        self.assertIn("Environment=HOME=/root", unidade)
        self.assertIn("Environment=TMPDIR=/tmp", unidade)
        self.assertIn("/usr/local/bin", unidade)
        self.assertIn("XDG_RUNTIME_DIR=/run/user/0", unidade)
        self.assertIn("DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/0/bus", unidade)
        self.assertIn("OnActiveSec=1min", timer)
        self.assertIn("OnUnitActiveSec=5min", timer)
        self.assertIn("validar_confinex", fonte)
        self.assertIn("confinex_bridge_inativa", fonte)
        self.assertIn("juan-confinex-db-bridge.service", fonte)
        self.assertIn("confinex_bridge_reiniciada", fonte)
        self.assertIn("validar_modelos", fonte)
        self.assertIn("--probe-max-tokens", fonte)
        self.assertIn("validar_status_gateway", fonte)
        self.assertIn("consultar_readyz", fonte)
        self.assertIn("validar_roteador_xlsx", fonte)
        self.assertIn("validar_indice_sessoes", fonte)
        self.assertIn("validar_autenticacao_wacli", fonte)
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

    @patch("tools.verificar_openclaw_canais.reiniciar", return_value=True)
    def test_nao_reinicia_gateway_quando_whatsapp_exige_pareamento(self, reiniciar):
        args = SimpleNamespace(gateway_service="openclaw-gateway.service")
        acoes = reparar(args, [
            "whatsapp_openclaw_indisponivel",
            "whatsapp_openclaw_reautenticacao_necessaria",
            "gateway_event_loop_degradado",
        ])
        self.assertEqual([], acoes)
        reiniciar.assert_not_called()

    @patch("tools.verificar_openclaw_canais.reiniciar", return_value=True)
    def test_falha_de_canal_nao_reinicia_gateway_saudavel(self, reiniciar):
        args = SimpleNamespace(gateway_service="openclaw-gateway.service")
        acoes = reparar(args, [
            "telegram_indisponivel:default",
            "whatsapp_openclaw_indisponivel",
            "whatsapp_openclaw_reautenticacao_necessaria",
        ])
        self.assertEqual([], acoes)
        reiniciar.assert_not_called()

    @patch("tools.verificar_openclaw_canais.reiniciar", return_value=True)
    def test_reinicia_gateway_apenas_com_health_indisponivel(self, reiniciar):
        args = SimpleNamespace(gateway_service="openclaw-gateway.service")
        acoes = reparar(args, ["gateway_health_indisponivel"])
        self.assertEqual(["gateway_reiniciado"], acoes)
        reiniciar.assert_called_once_with(
            "openclaw-gateway.service", usuario=True,
        )

    @patch("tools.verificar_openclaw_canais.reiniciar", return_value=True)
    def test_token_ausente_nao_reinicia_gateway(self, reiniciar):
        args = SimpleNamespace(gateway_service="openclaw-gateway.service")
        self.assertEqual([], reparar(args, ["token_gateway_indisponivel"]))
        reiniciar.assert_not_called()

    @patch("tools.verificar_openclaw_canais.reiniciar", return_value=True)
    def test_repara_timer_oficial_do_wey_sem_reiniciar_gateway(self, reiniciar):
        args = SimpleNamespace(
            wey_health_timer="wey-whatsapp-official-health.timer",
            gateway_service="openclaw-gateway.service",
        )
        self.assertEqual(
            reparar(args, ["wey_health_timer_inativo"]),
            ["wey_health_timer_reiniciado"],
        )
        reiniciar.assert_called_once_with("wey-whatsapp-official-health.timer")


if __name__ == "__main__":
    unittest.main()
