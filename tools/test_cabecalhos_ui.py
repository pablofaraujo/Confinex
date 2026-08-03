import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CabecalhosUiTests(unittest.TestCase):
    MODULOS_SEM_RESUMO_TECNICO = (
        "abate.html",
        "bb.html",
        "bgi.html",
        "confinados.html",
        "confinamento.html",
        "fazenda-ametista.html",
        "ops.html",
        "parceria-ricardo.html",
        "parceria-xande.html",
        "js/financeiro.js",
        "js/pendencias.js",
        "js/eventos.js",
        "revisoes.js",
    )

    def test_subtitulos_nao_repetem_horario_nem_contagem(self):
        for nome in self.MODULOS_SEM_RESUMO_TECNICO:
            fonte = (ROOT / nome).read_text(encoding="utf-8")
            self.assertNotRegex(
                fonte,
                r"(?:subtitle|el\('subtitle'\)).{0,100}(?:Atualizado|Dados atualizados)",
                msg=f"{nome} ainda usa o subtítulo como carimbo técnico",
            )

    def test_titulos_dos_modulos_nao_repetem_nome_pessoal(self):
        paginas = [
            ROOT / nome
            for nome in (
                "abate.html",
                "bb.html",
                "bgi.html",
                "confinamento.html",
                "fazenda-ametista.html",
                "parceria-ricardo.html",
                "parceria-xande.html",
                "parcerias.html",
                "revisoes.html",
                "ocr-pesagem.html",
                "painel-boi-gordo.html",
            )
        ]
        for pagina in paginas:
            fonte = pagina.read_text(encoding="utf-8")
            titulos = " ".join(re.findall(r"<h1[^>]*>(.*?)</h1>", fonte, re.S))
            self.assertNotIn("PABLO FERREIRA", titulos, msg=pagina.name)

    def test_excecoes_de_frescor_continuam_visiveis(self):
        self.assertIn("Atualizado", (ROOT / "index.html").read_text(encoding="utf-8"))
        self.assertIn("Atualizado em", (ROOT / "painel-boi-gordo.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
