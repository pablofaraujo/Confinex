import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from atualizar_painel_boi_gordo import atualizar, validar


BASE = {"atualizadoEm": "2026-07-24 06:32", "fonte": "teste", "indicadores": [{}], "curvaBGI": [{}]}


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


if __name__ == "__main__":
    unittest.main()

