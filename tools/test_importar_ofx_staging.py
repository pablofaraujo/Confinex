import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.importar_ofx_staging import EscritorStaging, ler_ofx, montar_plano


OFX = """OFXHEADER:100
<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><BANKACCTFROM>
<BANKID>756<ACCTID>12345</BANKACCTFROM><BANKTRANLIST>
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260814000000<FITID>A1<TRNAMT>-100.50<NAME>PIX<MEMO>teste
</STMTTRN></BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>"""


class ImportarOfxStagingTest(unittest.TestCase):
    def arquivo(self):
        temporario = tempfile.NamedTemporaryFile(suffix=".ofx", delete=False)
        temporario.write(OFX.encode("latin-1"))
        temporario.close()
        return Path(temporario.name)

    def test_plano_novo_e_idempotente(self):
        ofx = ler_ofx(self.arquivo())
        plano = montar_plano(ofx, [], [])
        self.assertEqual(plano["resumo"]["transacoes_novas"], 1)
        existente = [{"conta": "756:12345", "fitid": "A1"}]
        repetido = montar_plano(ofx, [plano["fonte"]], existente)
        self.assertEqual(repetido["resumo"]["transacoes_novas"], 0)
        self.assertTrue(repetido["fonte_existe"])

    def test_escritor_bloqueia_tabela_operacional(self):
        escritor = EscritorStaging("https://exemplo.invalid", "segredo")
        with self.assertRaisesRegex(ValueError, "escrita não permitida"):
            escritor.inserir("transacoes_banco", {})

    def test_ofx_vazio_e_transacao_duplicada(self):
        vazio = OFX.replace(
            "<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260814000000<FITID>A1<TRNAMT>-100.50<NAME>PIX<MEMO>teste\n</STMTTRN>",
            "",
        )
        arquivo_vazio = tempfile.NamedTemporaryFile(suffix=".ofx", delete=False)
        arquivo_vazio.write(vazio.encode("latin-1"))
        arquivo_vazio.close()
        self.assertEqual(ler_ofx(Path(arquivo_vazio.name))["transacoes"], [])

        duplicado = OFX.replace("</BANKTRANLIST>", OFX.split("<STMTTRN>", 1)[1].split("</BANKTRANLIST>", 1)[0].join(("<STMTTRN>", "</BANKTRANLIST>")))
        arquivo_duplicado = tempfile.NamedTemporaryFile(suffix=".ofx", delete=False)
        arquivo_duplicado.write(duplicado.encode("latin-1"))
        arquivo_duplicado.close()
        self.assertEqual(len(ler_ofx(Path(arquivo_duplicado.name))["transacoes"]), 1)

    @mock.patch("tools.importar_ofx_staging.urllib.request.urlopen")
    def test_timeout_nao_repete_post(self, urlopen):
        urlopen.side_effect = TimeoutError("timeout simulado")
        escritor = EscritorStaging("https://exemplo.invalid", "segredo")
        with self.assertRaises(TimeoutError):
            escritor.inserir("fontes_importacao", {})
        self.assertEqual(urlopen.call_count, 1)

    def test_fonte_nao_repete_escrita(self):
        fonte = Path(__file__).with_name("importar_ofx_staging.py").read_text()
        self.assertNotIn("while tentativa", fonte)
        self.assertNotIn("method=\"PATCH\"", fonte)
        self.assertNotIn("method=\"DELETE\"", fonte)


if __name__ == "__main__":
    unittest.main()
