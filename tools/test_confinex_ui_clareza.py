import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ClarezaInterfaceConfinexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fonte = (ROOT / "confinex-app.latest.js").read_text(encoding="utf-8")

    def test_cabecalho_nao_repete_identidade_do_shell(self):
        self.assertIn('className: "logo", children: "Confinex"', self.fonte)
        self.assertIn("Avaliação e comparativo de confinamento", self.fonte)
        self.assertNotIn("PABLO FERREIRA", self.fonte)
        self.assertNotIn('children: "\\u2302 In\\xEDcio"', self.fonte)

    def test_fonte_legivel_esta_integro_e_empacotavel(self):
        self.assertNotIn("tokens truncated", self.fonte)
        self.assertNotIn("original token count", self.fonte)
        for componente in (
            "function ScPanel(",
            "function SensPanel(",
            "function EvolucaoTempo(",
            "function RelatorioComparativo(",
            "function Comparativo(",
        ):
            self.assertIn(componente, self.fonte)

    def test_arquivo_do_estudo_reune_acoes_em_linguagem_humana(self):
        for texto in (
            "Arquivo do estudo",
            "Importar estudo",
            "Baixar cópia",
            "Novo estudo",
            "Restaurar anterior",
            "Salvar versão",
            "Versões salvas",
        ):
            self.assertIn(texto, self.fonte)
        self.assertNotIn("Salvar JSON", self.fonte)

    def test_sincronizacao_nao_expoe_configuracao_tecnica_na_tela(self):
        for texto in (
            "Cópia online e segurança",
            "Carregar cópia",
            "Salvar cópia",
            "Consultar versões",
        ):
            self.assertIn(texto, self.fonte)
        for texto in (
            'label: "URL Apps Script"',
            'label: "Carregar Sheets"',
            "Sincroniza\\xE7\\xE3o tempor\\xE1ria",
        ):
            self.assertNotIn(texto, self.fonte)

    def test_funcoes_de_persistencia_continuam_ligadas_a_botoes(self):
        for funcao in (
            "importarJSON",
            "exportarJSON",
            "resetarInformacoes",
            "retornarAntesReset",
            "salvarVersaoNomeada",
            "restaurarVersaoNomeada",
            "apagarVersaoNomeada",
            "carregarSheets",
            "salvarSheetsAgora",
            "carregarVersoesSheets(true)",
        ):
            self.assertIn(f"onClick: {funcao}", self.fonte) if funcao not in ("importarJSON", "carregarVersoesSheets(true)") else self.assertIn(funcao, self.fonte)


if __name__ == "__main__":
    unittest.main()
