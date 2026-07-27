import tempfile
import unittest
from datetime import date
from pathlib import Path

from tools.analisar_extrato_ofx import gerar_plano, ler_ofx


OFX_BASE = """OFXHEADER:100
DATA:OFXSGML
<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><BANKTRANLIST>
<STMTTRN><DTPOSTED>{data1}<TRNAMT>-10.00<FITID>{fitid1}<MEMO>Teste
<STMTTRN><DTPOSTED>{data2}<TRNAMT>-20.00<FITID>{fitid2}<MEMO>Teste
</BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>
"""


class AnalisarExtratoOfxTest(unittest.TestCase):
    def criar_ofx(self, conteudo: str) -> Path:
        arquivo = tempfile.NamedTemporaryFile(suffix=".ofx", delete=False)
        arquivo.write(conteudo.encode("latin-1"))
        arquivo.close()
        self.addCleanup(Path(arquivo.name).unlink, missing_ok=True)
        return Path(arquivo.name)

    def test_separa_existente_de_novo_sem_escrita(self):
        caminho = self.criar_ofx(OFX_BASE.format(
            data1="20260724000000", fitid1="ja-existe",
            data2="20260726000000", fitid2="novo",
        ))
        plano = gerar_plano(
            ler_ofx(caminho),
            [{"id_externo": "ja-existe", "data": "2026-07-24"}],
            date(2026, 7, 26),
        )
        self.assertEqual(plano["cruzamento"]["ja_presentes"], 1)
        self.assertEqual(plano["cruzamento"]["novos"], 1)
        self.assertEqual(plano["escritas_executadas"], 0)
        self.assertNotIn("extrato_nao_chega_a_data_de_referencia", plano["pendencias"])

    def test_detecta_corte_anterior_e_fitid_duplicado(self):
        caminho = self.criar_ofx(OFX_BASE.format(
            data1="20260724000000", fitid1="duplicado",
            data2="20260724010000", fitid2="duplicado",
        ))
        plano = gerar_plano(ler_ofx(caminho), [], date(2026, 7, 26))
        self.assertEqual(plano["arquivo"]["duplicidades_internas"], 1)
        self.assertIn("extrato_nao_chega_a_data_de_referencia", plano["pendencias"])
        self.assertIn("extrato_possui_identificadores_duplicados", plano["pendencias"])

    def test_rejeita_arquivo_sem_estrutura_ofx(self):
        caminho = self.criar_ofx("conteúdo inválido")
        with self.assertRaisesRegex(ValueError, "estrutura OFX"):
            ler_ofx(caminho)


if __name__ == "__main__":
    unittest.main()
