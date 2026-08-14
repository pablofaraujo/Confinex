import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools.analisar_ficha_ima import (
    combinar_fichas,
    detectar_gtas_canceladas_pdf,
    extrair_texto_pdf,
    gerar_plano,
    ler_ficha,
)


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

    def test_exclui_gta_cancelada_e_conserva_contagem(self):
        ficha = ler_ficha(TEXTO, "hash", {"100002"})
        self.assertEqual([item["gta"] for item in ficha["movimentos"]], ["100001", "100003"])
        self.assertEqual(ficha["gtas_canceladas"], ["100002"])

    def test_combina_periodos_e_deduplica_sobreposicao(self):
        primeira = ler_ficha(TEXTO, "hash-a", {"100002"})
        segunda = ler_ficha(TEXTO.replace("20/07/2026", "26/07/2026"), "hash-b", {"100002"})
        segunda["saldo_rebanho"] = 240
        combinada = combinar_fichas([primeira, segunda])
        self.assertEqual(combinada["periodo_inicial"], "2026-07-20")
        self.assertEqual(combinada["periodo_final"], "2026-07-26")
        self.assertEqual(combinada["saldo_rebanho"], 240)
        self.assertEqual(len(combinada["movimentos"]), 2)
        self.assertEqual(combinada["movimentos_duplicados_ignorados"], 2)
        self.assertEqual(combinada["lacunas_periodo"], [])

    def test_combina_fichas_sem_esconder_lacuna_de_periodo(self):
        primeira = ler_ficha(TEXTO, "hash-a")
        segunda = ler_ficha(
            TEXTO.replace("20/07/2026", "29/07/2026").replace("26/07/2026", "30/07/2026"),
            "hash-b",
        )
        combinada = combinar_fichas([primeira, segunda])
        self.assertEqual(combinada["lacunas_periodo"], [
            {"inicio": "2026-07-27", "fim": "2026-07-28"}
        ])

    def test_cancelamento_em_uma_ficha_prevalece_sobre_periodo_sobreposto(self):
        primeira = ler_ficha(TEXTO, "hash-a")
        segunda = ler_ficha(TEXTO, "hash-b", {"100002"})
        combinada = combinar_fichas([primeira, segunda])
        self.assertEqual(
            [item["gta"] for item in combinada["movimentos"]],
            ["100001", "100003"],
        )
        self.assertEqual(combinada["gtas_canceladas"], ["100002"])

    def test_detecta_risco_grafico_alinhado_ao_numero_da_gta(self):
        class Pagina:
            def extract_text(self, visitor_text=None, visitor_operand_before=None, **_kwargs):
                visitor_text("100002", None, [1, 0, 0, 1, 68, 306.4], None, 7)
                visitor_operand_before(b"m", [283, 309.8], None, None)
                visitor_operand_before(b"l", [398, 309.8], None, None)
                visitor_operand_before(b"S", [], None, None)
                return ""

        leitor = lambda _: SimpleNamespace(pages=[Pagina()])
        with patch.dict(sys.modules, {"pypdf": SimpleNamespace(PdfReader=leitor)}):
            self.assertEqual(detectar_gtas_canceladas_pdf(Path("ficha.pdf")), {"100002"})

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
