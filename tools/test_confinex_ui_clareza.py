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

    def test_resumo_prioriza_rentabilidade_bruta_e_mantem_liquido_complementar(self):
        for texto in (
            'children: "Lucro l\\xEDquido"',
            'children: "Custo financeiro total"',
            'children: "Capital investido"',
            "m\\xE1ximo para VP zero",
            "Revenda para igualar este lucro l\\xEDquido",
        ):
            self.assertIn(texto, self.fonte)
        trecho_cartoes = self.fonte[
            self.fonte.index('className: "rank-row"'):
            self.fonte.index('className: "cmp-tbl"')
        ]
        self.assertIn("fP(r.rentMensal)", trecho_cartoes)
        self.assertIn('"rent. líquida: "', trecho_cartoes)
        self.assertNotIn('r.pagamentoConfinamentoRotulo', trecho_cartoes)
        self.assertNotIn('"limite compra VP: "', trecho_cartoes)

    def test_ranking_e_relatorio_usam_rentabilidade_bruta(self):
        ordenacao = "b.r.rentMensal - a.r.rentMensal || b.r.lucroBruto - a.r.lucroBruto || b.r.rentTotal - a.r.rentTotal"
        self.assertGreaterEqual(self.fonte.count(ordenacao), 2)
        self.assertNotIn("sort((a, b) => b.r.rMliq", self.fonte)
        for texto in (
            "Resumo por rentabilidade mensal bruta",
            "Rent. bruta mensal",
            "Rentabilidade mensal bruta",
            "Rentabilidade mensal líquida",
            "rentabilidadeMensalBruta: rM",
        ):
            self.assertIn(texto, self.fonte)

    def test_comparativo_explica_limite_vp_e_revenda_equivalente(self):
        for texto in (
            "Pre\\xE7o atual de compra",
            "Pre\\xE7o m\\xE1ximo de compra para VP zero",
            "Diferen\\xE7a at\\xE9 o limite",
            "Situa\\xE7\\xE3o no VP",
            "Pre\\xE7o m\\xEDnimo de revenda para igualar o lucro",
            "Pre\\xE7o dispon\\xEDvel na revenda",
            "Lucro l\\xEDquido estimado da revenda",
            "Melhor alternativa pelo lucro l\\xEDquido total",
            'label: "Revenda para igualar o lucro líquido"',
        ):
            self.assertIn(texto, self.fonte)
        self.assertIn("Tributo sobre a revenda (%)", self.fonte)
        self.assertIn("Outros encargos da revenda (%)", self.fonte)
        self.assertIn("const faturamentoBruto2 = arrobasVenda * precoVendaBruto2 * N", self.fonte)
        self.assertIn("const receita2 = faturamentoBruto2 - valorFunrural2 - valorFinpec2", self.fonte)
        self.assertIn('label: "A · Transporte na entrada"', self.fonte)
        self.assertIn('label: "B · Transporte na produção"', self.fonte)

    def test_referencia_de_transporte_nao_altera_resultado_economico(self):
        inicio = self.fonte.index("function calcCenario(")
        fim = self.fonte.index("function Tg(", inicio)
        calculo = self.fonte[inicio:fim]
        self.assertNotIn('sc.referenciaTransporte ===', calculo)
        self.assertIn('referenciaTransporte: sc.referenciaTransporte || "transporte_na_entrada"', calculo)


if __name__ == "__main__":
    unittest.main()
