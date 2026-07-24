import json
import tempfile
import unittest
from pathlib import Path

from auditar_ecossistema import auditar_estatico, validar_descoberta


SHELL_BASE = """
var NAV = [
  { href:'./', rotulo:'Geral', icone:'x' },
  { href:'./financeiro.html', rotulo:'Financeiro', icone:'x' },
  { href:'https://externo.test/', rotulo:'Portfolio B3', icone:'x' }
];
"""


class AuditoriaEcossistemaTests(unittest.TestCase):
    def preparar(self, html: dict[str, str], shell: str = SHELL_BASE):
        pasta = tempfile.TemporaryDirectory()
        root = Path(pasta.name)
        (root / "js").mkdir()
        (root / "js" / "cfagro-shell.js").write_text(shell, encoding="utf-8")
        for nome, conteudo in html.items():
            (root / nome).write_text(conteudo, encoding="utf-8")
        self.addCleanup(pasta.cleanup)
        return root

    def test_positivo_aprova_arquivos_destinos_e_politica_de_janela(self):
        root = self.preparar(
            {
                "index.html": "<main id='inicio'></main>",
                "financeiro.html": "<main></main>",
            }
        )
        resultados, inventario = auditar_estatico(root)
        falhas = [item.id for item in resultados if item.status == "falhou"]
        self.assertEqual([], falhas)
        self.assertEqual(2, len(inventario["paginas"]))

    def test_negativo_detecta_arquivo_ancora_e_nova_janela_incorretos(self):
        shell = """
        var NAV = [
          { href:'./ausente.html', rotulo:'Ausente', icone:'x' },
          { href:'./#fluxo', rotulo:'Financeiro', icone:'x' },
          { href:'https://externo.test/', rotulo:'Portfolio B3', icone:'x', ext:true }
        ];
        """
        root = self.preparar({"index.html": "<main></main>"}, shell)
        resultados, _ = auditar_estatico(root)
        falhas = {item.id for item in resultados if item.status == "falhou"}
        self.assertIn("menu:Ausente:arquivo", falhas)
        self.assertIn("menu:Financeiro:destino_real", falhas)
        self.assertIn("menu:Portfolio B3:mesma_janela", falhas)

    def test_vazio_falha_com_evidencia_explicita(self):
        root = self.preparar({}, "var NAV = [];")
        resultados, inventario = auditar_estatico(root)
        por_id = {item.id: item for item in resultados}
        self.assertEqual("falhou", por_id["inventario:paginas"].status)
        self.assertEqual("falhou", por_id["inventario:menu"].status)
        self.assertEqual([], inventario["paginas"])

    def test_falha_do_modo_descoberta_distingue_ausente_e_inesperada(self):
        root = self.preparar({"index.html": "<main></main>"})
        resultados, _ = auditar_estatico(root)
        with tempfile.TemporaryDirectory() as pasta:
            config = Path(pasta) / "falhas.json"
            config.write_text(
                json.dumps(
                    {
                        "falhas_esperadas": ["defeito:que:sumiu"],
                        "falhas_derivadas_permitidas": [],
                    }
                ),
                encoding="utf-8",
            )
            ok, detalhe = validar_descoberta(resultados, config)
        self.assertFalse(ok)
        self.assertIn("não detectadas", detalhe)


if __name__ == "__main__":
    unittest.main()
