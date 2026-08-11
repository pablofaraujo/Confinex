import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools.analisar_ficha_ima import extrair_texto_pdf, gerar_plano, ler_ficha


TEXTO = """
Período de 20/07/2026 a 26/07/2026 23:59
Rebanhos existentes
Total: 253
GTA - Bovino e Bubalino
GTAs de Saída
BOVINO 100001 U 21/07/26 PROPRIEDADE ENGORDA DESTINO 0 0 0 0 65
BOVINO 100002 U 23/07/26 PROPRIEDADE ABATE DESTINO 0 0 0 0 20
GTAs de Entrada
BOVINO 100003 U 22/07/26 PROPRIEDADE RURAL RECRIA ORIGEM 0 0 0 0 40
GTAs de Outras Espécies
"""


class AnalisarFichaImaTest(unittest.TestCase):
    def test_extrai_movimentos_e_mantem_ausentes_sem_escrita(self):
        ficha = ler_ficha(TEXTO, "hash")
        plano = gerar_plano(ficha, [], [], [], [], date(2026, 7, 26))
        self.assertEqual(plano["ficha"]["gtas"], 3)
        self.assertEqual(plano["ficha"]["animais_saida"], 85)
        self.assertEqual(plano["ficha"]["animais_entrada"], 40)
        self.assertEqual(plano["cruzamento"]["sem_qualquer_vinculo"], 3)
        self.assertEqual(plano["escritas_executadas"], 0)

    def test_reconhece_gta_em_qualquer_fonte_e_divergencia_de_saldo(self):
        ficha = ler_ficha(TEXTO, "hash")
        plano = gerar_plano(
            ficha,
            [{"numero": "100001"}],
            [{"gta": "100002"}],
            [{"gta": "100003"}],
            [{"data": "2026-07-25", "saldo_apos_movimento": 288}],
            date(2026, 7, 26),
        )
        self.assertEqual(plano["cruzamento"]["presentes_em_alguma_fonte"], 3)
        self.assertEqual(plano["cruzamento"]["sem_qualquer_vinculo"], 0)
        self.assertEqual(plano["ledger_fazenda"]["diferenca_para_ficha"], -35)
        self.assertIn("saldo_rebanho_diverge_do_ledger", plano["pendencias"])

    def test_rejeita_ficha_sem_periodo(self):
        with self.assertRaisesRegex(ValueError, "período"):
            ler_ficha("Total: 10", "hash")

    def test_extrai_pdf_com_pypdf_em_layout_quando_poppler_nao_existe(self):
        pagina = SimpleNamespace(extract_text=lambda **kwargs: (
            TEXTO if kwargs.get("extraction_mode") == "layout" else ""
        ))
        leitor = lambda _: SimpleNamespace(pages=[pagina])
        with tempfile.TemporaryDirectory() as pasta:
            entrada = Path(pasta) / "ficha.pdf"
            saida = Path(pasta) / "ficha.txt"
            entrada.write_bytes(b"%PDF-1.4\n")
            with patch("tools.analisar_ficha_ima.shutil.which", return_value=None), \
                 patch.dict(sys.modules, {"pypdf": SimpleNamespace(PdfReader=leitor)}):
                texto = extrair_texto_pdf(entrada, saida)
            self.assertIn("Período", texto)
            self.assertEqual(saida.read_text(encoding="utf-8"), texto)


if __name__ == "__main__":
    unittest.main()
