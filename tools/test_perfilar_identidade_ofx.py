from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import perfilar_identidade_ofx as ofx


def bloco(bankid="B1", branchid="01", acctid="0007", accttype="CHECKING", curdef="BRL", transacoes=()):
    itens = []
    for fitid, data, valor, tipo, memo in transacoes:
        itens.append(f"<STMTTRN><TRNTYPE>{tipo}</TRNTYPE><DTPOSTED>{data}</DTPOSTED><TRNAMT>{valor}</TRNAMT><FITID>{fitid}</FITID><MEMO>{memo}</MEMO></STMTTRN>")
    return (
        "<STMTRS><CURDEF>" + curdef + "</CURDEF><BANKACCTFROM><BANKID>" + bankid
        + "</BANKID><BRANCHID>" + branchid + "</BRANCHID><ACCTID>" + acctid
        + "</ACCTID><ACCTTYPE>" + accttype + "</ACCTTYPE></BANKACCTFROM>"
        + "<BANKTRANLIST>" + "".join(itens) + "</BANKTRANLIST></STMTRS>"
    )


def documento(*blocos):
    texto = ("OFXHEADER:100\nDATA:OFXSGML\nVERSION:102\nENCODING:USASCII\nCHARSET:1252\n"
             "<OFX><BANKMSGSRSV1>" + "".join(blocos) + "</BANKMSGSRSV1></OFX>")
    # SGML real: folhas não fecham; agregados STMTRS/BANKTRANLIST delimitam blocos.
    return re.sub(r"</(?:BANKID|BRANCHID|ACCTID|ACCTTYPE|CURDEF|TRNTYPE|DTPOSTED|TRNAMT|FITID|MEMO)>", "", texto).encode("cp1252")


class PerfilarIdentidadeOfxTests(unittest.TestCase):
    def test_xml_multiplos_demonstrativos_identidade_original_e_fitid_repetido(self):
        dados = ("<?xml version='1.0'?><OFX><BANKMSGSRSV1>"
                 + bloco(transacoes=(("0001", "20260901120000-0300", "-10.00", "DEBIT", "segredo"),))
                 + bloco(bankid="B2", branchid="", acctid="009", accttype="SAVINGS", curdef="USD",
                         transacoes=(("0001", "20260902", "0", "CREDIT", "outro"),))
                 + "</BANKMSGSRSV1></OFX>").encode()
        perfil = ofx.perfilar_ofx(dados)
        self.assertEqual(len(perfil["demonstrativos"]), 2)
        self.assertEqual(perfil["demonstrativos"][0]["identidade"]["BANKID"], "B1")
        self.assertEqual(perfil["demonstrativos"][1]["identidade"]["CURDEF"], "USD")
        self.assertEqual(perfil["demonstrativos"][1]["faltantes"], ["BRANCHID"])
        tx = perfil["demonstrativos"][0]["transacoes"][0]
        self.assertEqual(tx["fitid"], "0001")
        self.assertEqual(tx["data"], "2026-09-01T12:00:00-03:00")
        self.assertEqual(tx["valor"], "-10.00")
        self.assertEqual(tx["trntype"], "DEBIT")
        # A segunda identidade é incompleta; não comparar parcial com
        # completa como colisão entre identidades.
        self.assertEqual(perfil["resumo"]["fitids_multiplas_identidades"], 0)
        texto = json.dumps(perfil, ensure_ascii=False)
        self.assertNotIn("segredo", texto)
        self.assertNotIn("outro", texto)

    def test_sgml_e_preservacao_de_zeros_debito_e_zero(self):
        perfil = ofx.perfilar_ofx(documento(bloco(
            transacoes=(("00001", "20260901", "0.00", "DEBIT", "memo"),)
        )))
        tx = perfil["demonstrativos"][0]["transacoes"][0]
        self.assertEqual(tx["fitid"], "00001")
        self.assertEqual(tx["valor"], "0.00")
        self.assertEqual(tx["trntype"], "DEBIT")

    def test_data_ofx_com_fuso_sgml_vira_iso_sem_reinterpretar_calendario(self):
        perfil = ofx.perfilar_ofx(documento(bloco(
            transacoes=(("F-TZ", "20260901120000.000[-3:BRT]", "1", "CREDIT", "memo"),)
        )))
        self.assertEqual(perfil["demonstrativos"][0]["transacoes"][0]["data"], "2026-09-01T12:00:00.000-03:00")

    def test_repeticao_identica_conflito_por_memo_e_hash_de_fonte(self):
        primeiro = bloco(transacoes=(("F1", "20260901", "1.00", "CREDIT", "memo original"),))
        segundo = bloco(transacoes=(("F1", "20260901", "1.00", "CREDIT", "memo original"),))
        perfil = ofx.perfilar_ofx(documento(primeiro, segundo))
        self.assertEqual(perfil["resumo"]["repeticoes_identicas"], 1)
        self.assertEqual(perfil["resumo"]["conflitos_conteudo"], 0)
        conflitante = ofx.perfilar_ofx(documento(primeiro, bloco(transacoes=(("F1", "20260901", "1.00", "CREDIT", "memo alterado"),))))
        self.assertEqual(conflitante["resumo"]["repeticoes_identicas"], 0)
        self.assertEqual(conflitante["resumo"]["conflitos_conteudo"], 1)
        self.assertNotEqual(perfil["demonstrativos"][0]["transacoes"][0]["stmttrn_sha256"],
                            conflitante["demonstrativos"][1]["transacoes"][0]["stmttrn_sha256"])
        self.assertEqual(perfil["sha256"], hashlib.sha256(documento(primeiro, segundo)).hexdigest())

    def test_conjunto_classifica_conflito_mesma_identidade_com_refs_de_arquivo(self):
        um = ofx.perfilar_ofx(documento(bloco(transacoes=(("F1", "20260901", "1.00", "CREDIT", "a"),))))
        dois = ofx.perfilar_ofx(documento(bloco(transacoes=(("F1", "20260901", "1.00", "CREDIT", "b"),))))
        conjunto = ofx.perfilar_conjunto([um, dois])
        self.assertEqual(conjunto["resumo"]["conflitos_conteudo"], 1)
        refs = conjunto["conflitos_conteudo"][0]["referencias"]
        self.assertEqual({ref["arquivo"] for ref in refs}, {1, 2})
        self.assertEqual({ref["demonstrativo"] for ref in refs}, {1})
        self.assertEqual({ref["transacao"] for ref in refs}, {1})

    def test_mesma_identidade_multiblocos_e_identidade_incompleta_nao_provam_duplicata(self):
        mesma = ofx.perfilar_ofx(documento(
            bloco(transacoes=(("SAME", "20260901", "1", "CREDIT", "a"),)),
            bloco(transacoes=(("SAME", "20260901", "1", "CREDIT", "a"),)),
        ))
        self.assertEqual(mesma["resumo"]["repeticoes_identicas"], 1)
        self.assertEqual(mesma["resumo"]["fitids_multiplas_identidades"], 0)
        incompleta = ofx.perfilar_ofx(documento(
            bloco(branchid="", transacoes=(("SAME", "20260901", "1", "CREDIT", "a"),)),
            bloco(branchid="", transacoes=(("SAME", "20260901", "1", "CREDIT", "b"),)),
        ))
        self.assertEqual(incompleta["resumo"]["repeticoes_identicas"], 0)
        # A incompletude é um fato de cada demonstrativo, mesmo quando os
        # demonstrativos repetem a mesma identidade parcial.
        self.assertEqual(incompleta["resumo"]["identidades_incompletas"], 2)
        self.assertEqual(
            [item["demonstrativo"] for item in incompleta["identidades_incompletas"]],
            [1, 2],
        )
        parcial_e_completa = ofx.perfilar_ofx(documento(
            bloco(branchid="", transacoes=(("MIX", "20260901", "1", "CREDIT", "a"),)),
            bloco(branchid="02", transacoes=(("MIX", "20260901", "1", "CREDIT", "a"),)),
        ))
        self.assertEqual(parcial_e_completa["resumo"]["identidades_incompletas"], 1)
        self.assertEqual(parcial_e_completa["resumo"]["repeticoes_identicas"], 0)
        self.assertEqual(parcial_e_completa["resumo"]["fitids_multiplas_identidades"], 0)

    def test_sgml_nao_confunde_cabecalho_dentro_de_transacao_e_fatid_vazio(self):
        sgml = documento(bloco(transacoes=(("", "20260901", "1", "CREDIT", "memo"),)))
        sgml = sgml.replace(b"<FITID>", b"<BANKID>FAKE<FITID>", 1)
        perfil = ofx.perfilar_ofx(sgml)
        self.assertEqual(perfil["demonstrativos"][0]["identidade"]["BANKID"], "B1")
        self.assertEqual(perfil["resumo"]["fitids_ausentes"], 1)

    def test_campos_de_transacao_contraditorios_e_xml_malformado_sao_bloqueados(self):
        xml = ("<OFX><BANKMSGSRSV1><STMTRS><BANKACCTFROM><BANKID>B</BANKID>"
               "<BRANCHID>1</BRANCHID><ACCTID>A</ACCTID><ACCTTYPE>CHECKING</ACCTTYPE></BANKACCTFROM>"
               "<CURDEF>BRL</CURDEF><BANKTRANLIST><STMTTRN><FITID>A</FITID><FITID>B</FITID>"
               "<DTPOSTED>20260901</DTPOSTED><TRNAMT>1</TRNAMT><TRNTYPE>CREDIT</TRNTYPE>"
               "</STMTTRN></BANKTRANLIST></STMTRS></BANKMSGSRSV1></OFX>").encode()
        with self.assertRaisesRegex(ValueError, "transacao_campo_contraditorio"):
            ofx.perfilar_ofx(xml)
        xml_malformado = b"<?xml version='1.0'?><OFX><STMTRS></OFX>"
        with self.assertRaisesRegex(ValueError, "xml_malformado"):
            ofx.perfilar_ofx(xml_malformado)

    def test_codificacao_declarada_latin1_e_data_hora_invalida(self):
        latin1 = documento(bloco(transacoes=(("F-L", "20260901", "1", "CREDIT", "não devolver"),)))
        # MEMO não é exportado; o parser ainda deve aceitar a codificação declarada.
        perfil = ofx.perfilar_ofx(latin1)
        self.assertEqual(perfil["resumo"]["transacoes"], 1)
        xml_latin1 = ("<?xml version='1.0' encoding='ISO-8859-1'?><OFX><STMTRS><BANKACCTFROM>"
                      "<BANKID>B</BANKID><BRANCHID>1</BRANCHID><ACCTID>A</ACCTID><ACCTTYPE>C</ACCTTYPE>"
                      "</BANKACCTFROM><CURDEF>BRL</CURDEF><BANKTRANLIST><STMTTRN><FITID>L</FITID>"
                      "<DTPOSTED>2026-09-01</DTPOSTED><TRNAMT>1</TRNAMT><TRNTYPE>CREDIT</TRNTYPE>"
                      "<MEMO>não importar</MEMO></STMTTRN></BANKTRANLIST></STMTRS></OFX>").encode("iso-8859-1")
        self.assertEqual(ofx.perfilar_ofx(xml_latin1)["resumo"]["transacoes"], 1)
        invalida = documento(bloco(transacoes=(("F-D", "20261301126000+2500", "1", "CREDIT", "x"),)))
        with self.assertRaises(ValueError):
            ofx.perfilar_ofx(invalida)
        iso = ("<?xml version='1.0' encoding='UTF-8'?><OFX><STMTRS><BANKACCTFROM>"
               "<BANKID>B</BANKID><BRANCHID>1</BRANCHID><ACCTID>A</ACCTID><ACCTTYPE>C</ACCTTYPE>"
               "</BANKACCTFROM><CURDEF>BRL</CURDEF><BANKTRANLIST><STMTTRN><FITID>I</FITID>"
               "<DTPOSTED>2026-08-03T00:00:00.123456Z</DTPOSTED><TRNAMT>1</TRNAMT><TRNTYPE>CREDIT</TRNTYPE>"
               "</STMTTRN></BANKTRANLIST></STMTRS></OFX>").encode()
        tx = ofx.perfilar_ofx(iso)["demonstrativos"][0]["transacoes"][0]
        self.assertEqual(tx["data_formato"], "iso8601")
        self.assertEqual(tx["data_ofx_original"], "2026-08-03T00:00:00.123456Z")

    def test_fracao_ofx_sem_hora_e_bloqueada(self):
        invalida = documento(bloco(transacoes=(
            ("F-FRAC", "20260901.123", "1", "CREDIT", "x"),
        )))
        with self.assertRaisesRegex(ValueError, "data_ofx_invalida"):
            ofx.perfilar_ofx(invalida)

    def test_envelopes_truncados_nao_caem_em_fallback_sgml(self):
        # SGML permite folhas abertas, mas todos os agregados, inclusive o
        # envelope OFX, devem fechar em ordem.
        sgml_sem_fechamento = documento(bloco()).replace(b"</OFX>", b"")
        with self.assertRaisesRegex(ValueError, "agregados_sgml_nao_fechados"):
            ofx.perfilar_ofx(sgml_sem_fechamento)

        # BOM identifica XML mesmo sem declaração; o parse truncado não pode
        # ser reinterpretado como SGML permissivo.
        xml_bom_truncado = b"\xef\xbb\xbf<OFX><STMTRS></OFX>"
        with self.assertRaisesRegex(ValueError, "xml_malformado"):
            ofx.perfilar_ofx(xml_bom_truncado)

        # OFX 2/OFXXML é XML declarado pelo cabeçalho e também deve falhar
        # quando o envelope está truncado.
        ofx200_truncado = b"OFXHEADER:200\nDATA:OFXXML\nVERSION:220\n<OFX><STMTRS>"
        with self.assertRaisesRegex(ValueError, "xml_malformado"):
            ofx.perfilar_ofx(ofx200_truncado)

    def test_conjunto_multiplas_identidades_tem_refs_de_ocorrencia(self):
        um = ofx.perfilar_ofx(documento(
            bloco(bankid="B1", transacoes=(("MESMO", "20260901", "1", "CREDIT", "a"),)),
            bloco(bankid="B2", transacoes=(("MESMO", "20260901", "1", "CREDIT", "b"),)),
        ))
        conjunto = ofx.perfilar_conjunto([um])
        achado = conjunto["fitids_multiplas_identidades"][0]
        self.assertEqual(achado["fitid"], "MESMO")
        self.assertEqual(achado["demonstrativos"], [
            {"arquivo": 1, "demonstrativo": 1, "transacao": 1},
            {"arquivo": 1, "demonstrativo": 2, "transacao": 1},
        ])

    def test_cabecalho_contraditorio_faltante_valor_invalido_e_estrutura_maliciosa(self):
        contraditorio = ("<OFX><BANKMSGSRSV1><STMTRS><BANKACCTFROM><BANKID>A</BANKID>"
                         "<BANKID>B</BANKID></BANKACCTFROM></STMTRS></BANKMSGSRSV1></OFX>").encode()
        with self.assertRaisesRegex(ValueError, "cabecalho_contraditorio"):
            ofx.perfilar_ofx(contraditorio)
        invalido = documento(bloco(transacoes=(("F", "20260901", "1,2", "DEBIT", "x"),)))
        with self.assertRaisesRegex(ValueError, "valor_decimal_invalido"):
            ofx.perfilar_ofx(invalido)
        for malicioso in (b"<!DOCTYPE OFX [ <!ENTITY x 'secret'> ]><OFX></OFX>",
                          b"<!ENTITY x 'secret'><OFX></OFX>", b"", b"fora"):
            with self.subTest(malicioso=malicioso), self.assertRaises(ValueError):
                ofx.perfilar_ofx(malicioso)

    def test_limites_e_cli_privada_sem_vazamento_ou_sobrescrita(self):
        dados = documento(bloco(transacoes=(("F", "20260901", "1.00", "DEBIT", "memo privado"),)))
        with self.assertRaisesRegex(ValueError, "ofx_acima_do_limite"):
            ofx.perfilar_ofx(dados + b"x" * (ofx.MAX_BYTES - len(dados) + 1))
        with tempfile.TemporaryDirectory() as temporario:
            fonte = Path(temporario) / "fonte.ofx"
            fonte.write_bytes(dados)
            saida = Path(temporario) / "saida"
            processo = subprocess.run(
                [sys.executable, str(Path(__file__).resolve().parent / "perfilar_identidade_ofx.py"),
                 "--ofx", str(fonte), "--ofx", str(fonte), "--saida", str(saida)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(processo.returncode, 0, processo.stderr)
            self.assertNotIn("memo privado", processo.stdout)
            resumo = json.loads(processo.stdout)["resumo"]
            self.assertEqual(resumo["fontes"], 2)
            self.assertEqual((saida / "analise.json").stat().st_mode & 0o777, 0o600)
            self.assertEqual((saida / "analise.md").stat().st_mode & 0o777, 0o600)
            with self.assertRaises(subprocess.CalledProcessError):
                subprocess.run(
                    [sys.executable, str(Path(__file__).resolve().parent / "perfilar_identidade_ofx.py"),
                     "--ofx", str(fonte), "--saida", str(saida)],
                    capture_output=True, text=True, check=True,
                )
            self.assertEqual(fonte.read_bytes(), dados)


if __name__ == "__main__":
    unittest.main()
