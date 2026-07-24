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
        bgi = (ROOT / "bgi.html").read_text(encoding="utf-8")
        link = 'href="https://pablofaraujo.github.io/boi-gordo-portfolio/"'
        self.assertIn(link, bgi)
        trecho = bgi[bgi.index(link):bgi.index(link) + 180]
        self.assertNotIn('target="_blank"', trecho)

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
                "cfagro-shell.js?v=20260723-3",
                fonte,
                msg=f"{pagina.name} ainda pode carregar um menu antigo do cache",
            )

    def test_falha_auditoria_estatica_estrita_fica_sem_regressao(self):
        resultados, _ = auditar_estatico()
        falhas = [item.id for item in resultados if item.status == "falhou"]
        self.assertEqual([], falhas)


if __name__ == "__main__":
    unittest.main()
