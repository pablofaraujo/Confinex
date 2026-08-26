import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools.verificar_wacli_continuo import diagnosticar, main, sessao_revogada, verificar


class VerificarWacliContinuoTest(unittest.TestCase):
    @patch("tools.verificar_wacli_continuo.executar")
    def test_diagnostico_expoe_apenas_estado_tecnico(self, executar):
        executar.return_value = Mock(
            returncode=0,
            stdout=json.dumps({"data": {
                "authenticated": True,
                "connected": True,
                "lock_held": True,
                "linked_jid": "nao-pode-vazar",
                "store": {"last_sync_at": "2026-08-14T12:43:24Z"},
            }}),
        )
        resultado = diagnosticar(Path("/wacli"), Path("/privado"))
        self.assertTrue(resultado["autenticado"])
        self.assertTrue(resultado["conectado"])
        self.assertNotIn("linked_jid", resultado)
        self.assertNotIn("nao-pode-vazar", json.dumps(resultado))

    @patch("tools.verificar_wacli_continuo.unidade_ativa", return_value=True)
    @patch("tools.verificar_wacli_continuo.sessao_revogada", return_value=False)
    @patch("tools.verificar_wacli_continuo.diagnosticar")
    def test_aceita_doctor_desconectado_quando_follow_detem_bloqueio(
        self, diagnosticar, _revogada, _unidade
    ):
        diagnosticar.return_value = {
            "autenticado": True, "conectado": False, "bloqueio_ativo": True
        }
        resultado = verificar(Path("/wacli"), Path("/privado"), "captura.service")
        self.assertTrue(resultado["saudavel"])

    @patch("tools.verificar_wacli_continuo.unidade_ativa", return_value=True)
    @patch("tools.verificar_wacli_continuo.sessao_revogada", return_value=False)
    @patch("tools.verificar_wacli_continuo.diagnosticar")
    def test_reprova_servico_sem_bloqueio_do_store(
        self, diagnosticar, _revogada, _unidade
    ):
        diagnosticar.return_value = {
            "autenticado": True, "conectado": False, "bloqueio_ativo": False
        }
        resultado = verificar(Path("/wacli"), Path("/privado"), "captura.service")
        self.assertFalse(resultado["saudavel"])

    @patch("tools.verificar_wacli_continuo.executar")
    def test_detecta_401_posterior_ao_banco_de_sessao(self, executar):
        with patch("pathlib.Path.stat") as stat:
            stat.return_value.st_mtime = 100
            executar.return_value = Mock(
                returncode=0,
                stdout="Logged out of WhatsApp (401: logged out from another device)",
            )
            self.assertTrue(sessao_revogada(Path("/store"), "captura.service"))
        self.assertIn("@100", executar.call_args.args[0])

    @patch("tools.verificar_wacli_continuo.verificar")
    @patch("tools.verificar_wacli_continuo.executar")
    @patch("tools.verificar_wacli_continuo.argparse.ArgumentParser.parse_args")
    def test_nao_reinicia_quando_reautenticacao_e_necessaria(
        self, argumentos, executar, verificar
    ):
        argumentos.return_value = SimpleNamespace(
            wacli_bin=Path("/wacli"),
            wacli_store=Path("/store"),
            unidade="captura.service",
            unidade_manutencao=None,
            reparar=True,
            espera_reparo=0,
        )
        verificar.return_value = {
            "saudavel": False,
            "autenticado": True,
            "sessao_revogada": True,
            "servico_ativo": False,
        }
        with patch("builtins.print") as imprimir:
            self.assertEqual(1, main())
        executar.assert_not_called()
        payload = json.loads(imprimir.call_args.args[0])
        self.assertEqual("reautenticacao_necessaria", payload["reparo_bloqueado"])
        self.assertFalse(payload["reparo_solicitado"])

    def test_unidades_nao_possuem_envio_para_whatsapp(self):
        raiz = Path(__file__).parents[1]
        arquivos = [
            raiz / "infra/systemd/wey-whatsapp-live-sync.service",
            raiz / "infra/systemd/wey-whatsapp-live-health.service",
            raiz / "infra/systemd/wey-whatsapp-live-health.timer",
        ]
        texto = "\n".join(arquivo.read_text() for arquivo in arquivos)
        for proibido in ("wacli send", "Supabase", "requests.post"):
            self.assertNotIn(proibido, texto)
        self.assertIn("sync --follow", texto)
        self.assertIn("OnUnitActiveSec=5min", texto)
        self.assertIn("Restart=no", texto)

    def test_alerta_nao_contem_credencial_e_nao_envia_whatsapp(self):
        fonte = Path(__file__).with_name("notificar_falha_wacli.sh").read_text()
        self.assertIn("whatsapp-health.env", fonte)
        self.assertNotIn("wacli send", fonte)
        self.assertNotRegex(fonte, r"bot[0-9]{6,}:")

    def test_sincronizacao_diaria_tem_retry_limitado(self):
        raiz = Path(__file__).parents[1]
        fonte = (raiz / "tools/sincronizar_wacli_com_retry.sh").read_text()
        unidade = (raiz / "infra/systemd/wey-whatsapp-automation.service").read_text()
        self.assertIn('MAX_TENTATIVAS="${MAX_TENTATIVAS:-3}"', fonte)
        self.assertIn("sync --once", fonte)
        self.assertIn("sincronizar_wacli_com_retry.sh", unidade)

    def test_retomada_espera_store_livre(self):
        raiz = Path(__file__).parents[1]
        fonte = (raiz / "tools/retomar_wacli_continuo.sh").read_text()
        unidade = (raiz / "infra/systemd/wey-whatsapp-automation.service").read_text()
        self.assertIn('d.get("lock_held")', fonte)
        self.assertIn("systemctl reset-failed", fonte)
        self.assertIn("retomar_wacli_continuo.sh", unidade)


if __name__ == "__main__":
    unittest.main()
