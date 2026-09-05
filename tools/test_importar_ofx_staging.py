import tempfile
import copy
import json
import unittest
import urllib.error
import uuid
import contextlib
import io
from pathlib import Path
from unittest import mock

from tools.importar_ofx_staging import EscritorStaging, NAMESPACE, executar, ler_ofx, montar_plano, main


OFX = """OFXHEADER:100
DATA:OFXSGML
CHARSET:1252
<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><CURDEF>BRL<BANKACCTFROM>
<BANKID>756<BRANCHID>01<ACCTID>12345<ACCTTYPE>CHECKING</BANKACCTFROM><BANKTRANLIST>
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260814000000<FITID>A1<TRNAMT>-100.50<NAME>PIX<MEMO>teste
</STMTTRN></BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>"""


class ImportarOfxStagingTest(unittest.TestCase):
    def arquivo(self, conteudo=OFX):
        temporario = tempfile.NamedTemporaryFile(suffix=".ofx", delete=False)
        temporario.write(conteudo.encode("cp1252"))
        temporario.close()
        self.addCleanup(Path(temporario.name).unlink, missing_ok=True)
        return Path(temporario.name)

    def test_plano_novo_e_idempotente(self):
        ofx = ler_ofx(self.arquivo())
        plano = montar_plano(ofx, [], [])
        self.assertEqual(plano["resumo"]["transacoes_novas"], 1)
        repetido = montar_plano(ofx, [plano["fonte"]], plano["transacoes"])
        self.assertEqual(repetido["resumo"]["transacoes_novas"], 0)
        self.assertTrue(repetido["fonte_existe"])
        self.assertTrue(repetido["executavel"])
        self.assertEqual(repetido["resumo"]["transacoes_ja_no_staging"], 1)
        self.assertEqual(plano["transacoes"][0]["id"], str(uuid.uuid5(NAMESPACE, "transacao:756:12345:A1")))

    def test_escritor_bloqueia_tabela_operacional(self):
        escritor = EscritorStaging("https://exemplo.invalid", "segredo")
        with self.assertRaisesRegex(ValueError, "escrita não permitida"):
            escritor.inserir("transacoes_banco", {})

    def test_ofx_vazio_e_transacao_duplicada(self):
        vazio = OFX.replace(
            "<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260814000000<FITID>A1<TRNAMT>-100.50<NAME>PIX<MEMO>teste\n</STMTTRN>",
            "",
        )
        self.assertEqual(ler_ofx(self.arquivo(vazio))["transacoes"], [])

        duplicado = OFX.replace("</BANKTRANLIST>", OFX.split("<STMTTRN>", 1)[1].split("</BANKTRANLIST>", 1)[0].join(("<STMTTRN>", "</BANKTRANLIST>")))
        leitura = ler_ofx(self.arquivo(duplicado))
        self.assertEqual(len(leitura["transacoes"]), 2)
        plano = montar_plano(leitura, [], [])
        self.assertEqual(len(plano["transacoes"]), 1)
        self.assertEqual(len(plano["transacoes"][0]["dados_origem"]["ofx"]["ocorrencias"]), 2)
        self.assertEqual(len(plano["fonte"]["metadados"]["ofx"]["ocorrencias"]), 2)

    def test_zero_e_precisao_decimal_nao_sao_descartados_ou_arredondados(self):
        for valor in ("0", "-100.501", "1234567890123456.78"):
            ofx = ler_ofx(self.arquivo(OFX.replace("-100.50", valor)))
            plano = montar_plano(ofx, [], [])
            self.assertTrue(plano["executavel"])
            self.assertEqual(plano["transacoes"][0]["valor"], valor)

    def test_campos_ausentes_e_decimal_invalido_bloqueiam_sem_omissao(self):
        for campo in ("<FITID>A1", "<TRNAMT>-100.50", "<DTPOSTED>20260814000000", "<BRANCHID>01"):
            ofx = ler_ofx(self.arquivo(OFX.replace(campo, "")))
            self.assertEqual(len(ofx["transacoes"]), 1)
            plano = montar_plano(ofx, [], [])
            self.assertFalse(plano["executavel"])
            escritor = mock.Mock()
            with self.assertRaisesRegex(ValueError, "bloqueada"):
                executar(plano, escritor)
            escritor.inserir.assert_not_called()
        for valor in ("NaN", "infinito", "1,23"):
            with self.assertRaisesRegex(ValueError, "decimal_invalido"):
                ler_ofx(self.arquivo(OFX.replace("-100.50", valor)))

    def test_mesma_chave_memo_horario_valor_ou_tipo_distinto_nao_ignora(self):
        original = ler_ofx(self.arquivo())
        plano = montar_plano(original, [], [])
        for antes, depois in (("teste", "PIX descrição diferente"), ("000000", "120000"),
                              ("-100.50", "100.50"), ("DEBIT", "CREDIT")):
            novo = ler_ofx(self.arquivo(OFX.replace(antes, depois)))
            diferente = montar_plano(novo, [plano["fonte"]], plano["transacoes"])
            self.assertFalse(diferente["executavel"])
            self.assertEqual(diferente["resumo"]["transacoes_ja_no_staging"], 0)
            self.assertIn("conteudo_divergente_no_staging", diferente["resumo"]["bloqueios_por_motivo"])

    def test_duas_identidades_fitid_igual_e_colisao_de_agencia(self):
        trecho = OFX.split("<STMTRS>")[1].split("</STMTRS>")[0]
        for antes, depois, permitido in (("<ACCTID>12345", "<ACCTID>999", True),
                                        ("<BRANCHID>01", "<BRANCHID>02", False)):
            segundo = "<STMTRS>" + trecho.replace(antes, depois) + "</STMTRS>"
            plano = montar_plano(ler_ofx(self.arquivo(OFX.replace("</STMTTRNRS>", segundo + "</STMTTRNRS>"))), [], [])
            self.assertEqual(plano["executavel"], permitido)
            if permitido:
                self.assertEqual(len(plano["transacoes"]), 2)
                self.assertEqual(len({t["conta"] for t in plano["transacoes"]}), 2)

    def test_legado_sem_prova_ambiguidade_e_uuid_colidido_bloqueiam(self):
        ofx = ler_ofx(self.arquivo())
        base = montar_plano(ofx, [], [])
        novo = base["transacoes"][0]
        for registros in ([{"id": "antigo", "conta": "apelido", "fitid": "A1"}],
                          [novo, {**novo, "id": "outro"}],
                          [{**novo, "fitid": "OUTRO", "conta": "999:000"}]):
            plano = montar_plano(ofx, [base["fonte"]], registros)
            self.assertFalse(plano["executavel"])
            self.assertEqual(plano["resumo"]["transacoes_novas"], 0)

    def test_uuid_legado_com_separador_colide_e_nao_cria_duas_linhas(self):
        ofx = ler_ofx(self.arquivo())
        um = ofx["transacoes"][0]
        dois = copy.deepcopy(um)
        um["conta"], um["fitid"] = "B:A:C", "F"
        dois["conta"], dois["fitid"] = "B:A", "C:F"
        ofx["transacoes"] = [um, dois]
        plano = montar_plano(ofx, [], [])
        self.assertFalse(plano["executavel"])
        self.assertEqual(plano["resumo"]["bloqueios_por_motivo"], {"colisao_uuid_entre_candidatas": 2})

    @mock.patch("tools.importar_ofx_staging.LeitorSupabase")
    def test_cli_snapshot_sem_rede_sem_segredos_e_sem_execucao(self, leitor):
        caminho = self.arquivo()
        with tempfile.TemporaryDirectory() as pasta:
            snapshot = Path(pasta) / "snapshot.json"
            snapshot.write_text(json.dumps({"tabelas": {"fontes_importacao": [], "transacoes_banco_staging": []}}))
            argv = ["importador", "--ofx", str(caminho), "--snapshot", str(snapshot)]
            saida = io.StringIO()
            with mock.patch("sys.argv", argv), contextlib.redirect_stdout(saida):
                main()
            resumo = json.loads(saida.getvalue())
            self.assertTrue(resumo["executavel"])
            self.assertEqual(resumo["transacoes_criadas"], 0)
            self.assertNotIn("12345", saida.getvalue())
            self.assertNotIn("memo", saida.getvalue())
            with mock.patch("sys.argv", argv + ["--executar"]), self.assertRaises(SystemExit):
                main()
            snapshot.write_text('{"tabelas": {"fontes_importacao": []}}')
            with mock.patch("sys.argv", argv), self.assertRaisesRegex(ValueError, "snapshot incompleto"):
                main()
        leitor.assert_not_called()

    def test_identidade_provada_preserva_id_e_rotulo_existentes(self):
        ofx = ler_ofx(self.arquivo())
        base = montar_plano(ofx, [], [])
        antiga = {**base["transacoes"][0], "id": "id-preservado", "conta": "rótulo preservado"}
        antes = copy.deepcopy(antiga)
        plano = montar_plano(ofx, [base["fonte"]], [antiga])
        self.assertTrue(plano["executavel"])
        self.assertEqual(plano["transacoes"], [])
        self.assertEqual(antiga, antes)

    def test_plano_vinculado_ao_conteudo_e_snapshot_e_sem_mutar_entrada(self):
        ofx = ler_ofx(self.arquivo())
        antes = copy.deepcopy(ofx)
        plano = montar_plano(ofx, [], [])
        self.assertEqual(ofx, antes)
        outro = montar_plano(ofx, [], [{"id": "x", "fitid": "outra"}])
        self.assertNotEqual(plano["plano_id"], outro["plano_id"])
        plano["transacoes"][0]["valor"] = "999"
        escritor = mock.Mock()
        with self.assertRaisesRegex(ValueError, "plano alterado"):
            executar(plano, escritor)
        escritor.inserir.assert_not_called()

    @mock.patch("tools.importar_ofx_staging.urllib.request.urlopen")
    def test_resposta_comprovada_nao_aceita_ignore_duplicates_ou_payload_diferente(self, urlopen):
        escritor = EscritorStaging("https://exemplo.invalid", "segredo")
        resposta = urlopen.return_value.__enter__.return_value
        resposta.status = 201
        resposta.read.return_value = b'[{"id":"x","valor":1.25}]'
        escritor.inserir("transacoes_banco_staging", {"id": "x", "valor": "1.25"})
        self.assertEqual(urlopen.call_args.args[0].get_header("Prefer"), "return=representation")
        for corpo in (b'[]', b'[{"id":"outro","valor":1.25}]', b'[{"id":"x","valor":2}]'):
            resposta.read.return_value = corpo
            with self.assertRaisesRegex(RuntimeError, "comprovado"):
                escritor.inserir("transacoes_banco_staging", {"id": "x", "valor": "1.25"})

    @mock.patch("tools.importar_ofx_staging.urllib.request.urlopen")
    def test_colisao_concorrente_409_nao_repete_nem_declara_criacao(self, urlopen):
        urlopen.side_effect = urllib.error.HTTPError("https://exemplo.invalid", 409, "conflito", {}, None)
        escritor = EscritorStaging("https://exemplo.invalid", "segredo")
        with self.assertRaisesRegex(RuntimeError, "HTTP 409"):
            executar(montar_plano(ler_ofx(self.arquivo()), [], []), escritor)
        self.assertEqual(urlopen.call_count, 1)

    @mock.patch("tools.importar_ofx_staging.urllib.request.urlopen")
    def test_timeout_nao_repete_post(self, urlopen):
        urlopen.side_effect = TimeoutError("timeout simulado")
        escritor = EscritorStaging("https://exemplo.invalid", "segredo")
        with self.assertRaises(TimeoutError):
            escritor.inserir("fontes_importacao", {})
        self.assertEqual(urlopen.call_count, 1)

    def test_fonte_nao_repete_escrita(self):
        fonte = Path(__file__).with_name("importar_ofx_staging.py").read_text()
        self.assertNotIn("analisar_extrato_ofx", fonte)
        self.assertNotIn("while tentativa", fonte)
        self.assertNotIn("method=\"PATCH\"", fonte)
        self.assertNotIn("method=\"DELETE\"", fonte)


if __name__ == "__main__":
    unittest.main()
