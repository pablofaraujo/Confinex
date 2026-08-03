import unittest
from pathlib import Path

from auditar_ecossistema import auditar_estatico, extrair_menu


ROOT = Path(__file__).resolve().parents[1]


class RegressaoNavegacaoTests(unittest.TestCase):
    def setUp(self):
        self.menu = {item.rotulo: item for item in extrair_menu()}

    def test_positivo_destinos_reais_estao_no_menu(self):
        self.assertEqual("./financeiro.html", self.menu["Financeiro"].href)
        self.assertEqual("./pendencias.html", self.menu["Pendências"].href)
        self.assertEqual("./eventos.html", self.menu["Eventos"].href)
        for arquivo in ("financeiro.html", "pendencias.html", "eventos.html"):
            self.assertTrue((ROOT / arquivo).is_file())

    def test_negativo_portfolio_nao_abre_nova_janela(self):
        self.assertFalse(self.menu["Portfolio B3"].externo)
        shell = (ROOT / "js" / "cfagro-shell.js").read_text(encoding="utf-8")
        self.assertIn("!portfolio && !n.ext", shell)
        self.assertEqual(
            "https://pablofaraujo.github.io/boi-gordo-portfolio/",
            self.menu["Portfolio B3"].href,
        )
        self.assertIn("onPortfolio", shell)
        self.assertNotIn("visao=portfolio", shell)
        self.assertNotIn("Portfólio B3</a>", (ROOT / "bgi.html").read_text(encoding="utf-8"))

    def test_vazio_nao_usa_ancoras_como_destino_de_modulo(self):
        for rotulo in ("Financeiro", "Pendências", "Eventos"):
            self.assertFalse(self.menu[rotulo].href.startswith("./#"))

    def test_cache_do_shell_e_invalidado_em_todas_as_paginas_ativas(self):
        paginas_ativas = [
            path
            for path in ROOT.glob("*.html")
            if "cfagro-shell.js" in path.read_text(encoding="utf-8")
        ]
        self.assertGreater(len(paginas_ativas), 0)
        for pagina in paginas_ativas:
            fonte = pagina.read_text(encoding="utf-8")
            self.assertIn(
                "cfagro-shell.js?v=20260803-1",
                fonte,
                msg=f"{pagina.name} ainda pode carregar um menu antigo do cache",
            )

    def test_falha_auditoria_estatica_estrita_fica_sem_regressao(self):
        resultados, _ = auditar_estatico()
        falhas = [item.id for item in resultados if item.status == "falhou"]
        self.assertEqual([], falhas)

    def test_shell_usa_icones_lineares_locais_sem_emoji(self):
        shell = (ROOT / "js" / "cfagro-shell.js").read_text(encoding="utf-8")
        self.assertIn("function iconSvg", shell)
        self.assertIn('stroke-width="1.75"', shell)
        for emoji in ("🧮", "🐮", "🌾", "📈", "⚖️", "💰", "📅", "⚙️"):
            self.assertNotIn(emoji, shell)

    def test_shell_usa_rotulos_e_icones_humanos_aprovados(self):
        shell = (ROOT / "js" / "cfagro-shell.js").read_text(encoding="utf-8")
        self.assertIn("rotulo:'Visão Geral'", shell)
        self.assertIn("rotulo:'Confinamento', icone:'curral'", shell)
        self.assertIn("rotulo:'Fazenda Ametista', icone:'porteira'", shell)
        self.assertIn("rotulo:'Ricardo', icone:'pessoa'", shell)
        self.assertIn("rotulo:'Xande',   icone:'pessoa'", shell)
        self.assertNotIn("icone:'cow'", shell)

    def test_acoes_globais_ficam_somente_na_visao_geral_com_sessao(self):
        shell = (ROOT / "js" / "cfagro-shell.js").read_text(encoding="utf-8")
        self.assertIn("var ehVisaoGeral = !onPortfolio && aqui === 'index.html'", shell)
        self.assertIn("(ehVisaoGeral ? '<div class=", shell)
        self.assertIn("data-shell-atualizar hidden", shell)
        self.assertIn("data-shell-sair hidden", shell)
        self.assertIn("if(!atualizar || !sair) return", shell)
        self.assertIn("window.db.auth.getSession()", shell)
        self.assertIn("resultado.data.session", shell)
        paginas_sem_acoes_duplicadas = (
            "index.html",
            "confinados.html",
            "bb.html",
            "bgi.html",
            "abate.html",
            "ops.html",
            "fazenda-ametista.html",
            "financeiro.html",
            "eventos.html",
            "pendencias.html",
            "confinamento.html",
            "parceria-ricardo.html",
            "parceria-xande.html",
            "revisoes.html",
            "ocr-pesagem.html",
            "painel-boi-gordo.html",
        )
        for nome in paginas_sem_acoes_duplicadas:
            pagina = (ROOT / nome).read_text(encoding="utf-8")
            self.assertNotIn('onclick="sair()"', pagina)
            self.assertNotIn('onclick="carregar()">↻ Atualizar', pagina)
            self.assertNotIn('id="atualizarBtn"', pagina)
            self.assertNotIn('id="sairBtn"', pagina)


if __name__ == "__main__":
    unittest.main()
