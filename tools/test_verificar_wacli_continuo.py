import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tools.verificar_wacli_continuo import diagnosticar, verificar


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
    @patch("tools.verificar_wacli_continuo.diagnosticar")
    def test_aceita_doctor_desconectado_quando_follow_detem_bloqueio(self, diagnosticar, _unidade):
        diagnosticar.return_value = {
            "autenticado": True, "conectado": False, "bloqueio_ativo": True
        }
        resultado = verificar(Path("/wacli"), Path("/privado"), "captura.service")
        self.assertTrue(resultado["saudavel"])

    @patch("tools.verificar_wacli_continuo.unidade_ativa", return_value=True)
    @patch("tools.verificar_wacli_continuo.diagnosticar")
    def test_reprova_servico_sem_bloqueio_do_store(self, diagnosticar, _unidade):
        diagnosticar.return_value = {
            "autenticado": True, "conectado": False, "bloqueio_ativo": False
        }
        resultado = verificar(Path("/wacli"), Path("/privado"), "captura.service")
        self.assertFalse(resultado["saudavel"])

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

    def test_alerta_nao_contem_credencial_e_nao_envia_whatsapp(self):
        fonte = Path(__file__).with_name("notificar_falha_wacli.sh").read_text()
        self.assertIn("whatsapp-health.env", fonte)
        self.assertNotIn("wacli send", fonte)
        self.assertNotRegex(fonte, r"bot[0-9]{6,}:")


if __name__ == "__main__":
    unittest.main()
