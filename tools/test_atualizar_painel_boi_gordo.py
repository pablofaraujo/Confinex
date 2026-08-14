import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from atualizar_painel_boi_gordo import atualizar, validar


BASE = {"atualizadoEm": "2026-07-24 06:32", "fonte": "teste", "indicadores": [{}], "curvaBGI": [{}]}
ROOT = Path(__file__).resolve().parents[1]


class AtualizadorPainelTest(unittest.TestCase):
    def test_valida_schema_completo(self):
        self.assertIs(validar(BASE), BASE)

    def test_rejeita_resposta_vazia(self):
        with self.assertRaisesRegex(ValueError, "campos"):
            validar({})

    def test_falha_nao_sobrescreve_artefato(self):
        with tempfile.TemporaryDirectory() as pasta:
            destino = Path(pasta) / "painel.json"
            destino.write_text(json.dumps(BASE), encoding="utf-8")
            with patch("atualizar_painel_boi_gordo.baixar", side_effect=ValueError("vazia")):
                with self.assertRaises(ValueError):
                    atualizar("https://fonte.invalid", destino)
            self.assertEqual(json.loads(destino.read_text(encoding="utf-8")), BASE)

    def test_agendamento_nao_finge_atualizacao_sem_fonte(self):
        workflow = (ROOT / ".github" / "workflows" / "atualizar-painel-boi-gordo.yml").read_text(encoding="utf-8")
        self.assertIn("bloqueado: PAINEL_BOI_GORDO_SOURCE_URL não configurada", workflow)
        self.assertIn("::error::Painel não atualizado", workflow)
        self.assertNotIn("não testado: PAINEL_BOI_GORDO_SOURCE_URL", workflow)
        self.assertIn("timeout-minutes: 5", workflow)


if __name__ == "__main__":
    unittest.main()
