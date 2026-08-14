import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ABATE = ROOT / "abate.html"


class AbateOrigensTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = ABATE.read_text(encoding="utf-8")
        seletor = re.search(
            r'<select id="nOrigem">(.*?)</select>', cls.html, flags=re.DOTALL
        )
        if not seletor:
            raise AssertionError("seletor de origem do abate não encontrado")
        cls.opcoes = dict(
            re.findall(r'<option value="([^"]+)">([^<]+)</option>', seletor.group(1))
        )

    def test_boi_balanca_pode_ser_selecionado(self):
        self.assertEqual("Boi Balança", self.opcoes.get("boi_balanca"))
        self.assertIn("boi_balanca:'Boi Balança'", self.html)

    def test_wilson_nao_pode_ser_selecionado_em_novo_abate(self):
        self.assertNotIn("wilson", self.opcoes)

    def test_origens_operacionais_permanecem_disponiveis(self):
        self.assertEqual(
            {
                "fazenda": "Fazenda própria",
                "boi_balanca": "Boi Balança",
                "ricardo": "Parceria Ricardo",
                "xande": "Parceria Xande",
            },
            self.opcoes,
        )


if __name__ == "__main__":
    unittest.main()
