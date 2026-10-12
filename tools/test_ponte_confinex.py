"""Testes do contrato operacional da Ponte VPS."""

import subprocess
import unittest
from pathlib import Path


RAIZ = Path(__file__).resolve().parents[1]
SCRIPT = RAIZ / "infra/ponte/ponte_confinex.sh"


class PonteConfinexTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fonte = SCRIPT.read_text()

    def test_script_tem_sintaxe_bash_valida(self):
        resultado = subprocess.run(
            ["bash", "-n", str(SCRIPT)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(resultado.returncode, 0, resultado.stderr)

    def test_carrega_ambiente_completo_antes_de_exigir_credencial(self):
        origem = self.fonte.index('. "$ENV_FILE"')
        exigencia = self.fonte.index("${ANTHROPIC_API_KEY:?")
        executor = self.fonte.index("timeout 1800 claude -p")
        self.assertLess(origem, exigencia)
        self.assertLess(exigencia, executor)

    def test_estado_final_usa_apenas_valores_aceitos_pelo_banco(self):
        self.assertIn("STATUS=concluido", self.fonte)
        self.assertIn("STATUS=erro", self.fonte)
        self.assertNotIn("erro_login", self.fonte)

    def test_relatorio_e_persistido_antes_do_patch_final(self):
        criacao = self.fonte.index('SPOOL=$(criar_spool')
        envio = self.fonte.index('patch_briefing "$ID" executando "$SPOOL"')
        self.assertLess(criacao, envio)
        self.assertIn('"$SPOOL_DIR"/*.json', self.fonte)

    def test_patch_confirma_transicao_e_captura_resposta_http(self):
        self.assertIn("--write-out '%{http_code}'", self.fonte)
        self.assertIn("status=eq.$estado_esperado", self.fonte)
        self.assertIn("Prefer: return=representation", self.fonte)


if __name__ == "__main__":
    unittest.main()
