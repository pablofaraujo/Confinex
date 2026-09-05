from copy import deepcopy
import hashlib
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from tools.analisar_extrato_ofx import gerar_plano, ler_ofx


OFX_BASE = """OFXHEADER:100
DATA:OFXSGML
<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><CURDEF>BRL</CURDEF>
<BANKACCTFROM><BANKID>BANCO-TESTE</BANKID><BRANCHID>01</BRANCHID><ACCTID>0001</ACCTID><ACCTTYPE>CHECKING</ACCTTYPE></BANKACCTFROM>
<BANKTRANLIST>
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>{data1}<TRNAMT>-10.00<FITID>{fitid1}<MEMO>Teste</STMTTRN>
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>{data2}<TRNAMT>-20.00<FITID>{fitid2}<MEMO>Teste</STMTTRN>
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
        ofx = ler_ofx(caminho)
        plano = gerar_plano(ofx, [ofx["transacoes"][0]], date(2026, 7, 26))
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
        with self.assertRaisesRegex(ValueError, "estrutura OFX|codificacao_ofx_invalida"):
            ler_ofx(caminho)

    def test_fitid_igual_em_outra_identidade_completa_nao_e_presenca(self):
        caminho = self.criar_ofx(OFX_BASE.format(
            data1="20260724000000", fitid1="mesmo-fitid",
            data2="20260726000000", fitid2="outro",
        ))
        ofx = ler_ofx(caminho)
        outro = deepcopy(ofx["transacoes"][0])
        identidade = outro["dados_origem"]["ofx"]["identidade"]
        identidade["BANKID"] = "OUTRO-BANCO"
        outro["dados_origem"]["ofx"]["identidade_sha256"] = _assinatura_local(identidade)
        plano = gerar_plano(ofx, [outro], date(2026, 7, 26))
        self.assertEqual(plano["cruzamento"]["ja_presentes"], 0)
        self.assertEqual(plano["cruzamento"]["novos"], 2)
        self.assertEqual(plano["cruzamento"]["indeterminados"], 0)
        self.assertEqual(plano["arquivo"]["identificadores_unicos"], 2)

    def test_legado_sem_prova_de_identidade_fica_indeterminado(self):
        caminho = self.criar_ofx(OFX_BASE.format(
            data1="20260724000000", fitid1="legado",
            data2="20260726000000", fitid2="novo",
        ))
        ofx = ler_ofx(caminho)
        plano = gerar_plano(ofx, [{"id_externo": "legado", "data": "2026-07-24"}], date(2026, 7, 26))
        self.assertEqual(plano["cruzamento"]["ja_presentes"], 0)
        self.assertEqual(plano["cruzamento"]["novos"], 1)
        self.assertEqual(plano["cruzamento"]["indeterminados"], 1)
        self.assertEqual(plano["cruzamento"]["indeterminados_por_motivo"], {"identidade_pendente": 1})

    def test_conteudo_divergente_na_mesma_chave_nao_e_novo_nem_presente(self):
        dados = OFX_BASE.format(
            data1="20260724000000", fitid1="duplicado",
            data2="20260724010000", fitid2="duplicado",
        ).replace("<MEMO>Teste", "<MEMO>Outro", 1)
        plano = gerar_plano(ler_ofx(self.criar_ofx(dados)), [], date(2026, 7, 26))
        self.assertEqual(plano["cruzamento"]["ja_presentes"], 0)
        self.assertEqual(plano["cruzamento"]["novos"], 0)
        self.assertEqual(plano["cruzamento"]["indeterminados"], 1)
        self.assertEqual(plano["cruzamento"]["indeterminadas_ocorrencias"], 2)
        self.assertEqual(plano["cruzamento"]["repeticoes_conteudo_divergente"], 1)

    def test_repeticao_identica_preserva_contagem_e_um_caso_de_conferencia(self):
        dados = OFX_BASE.format(
            data1="20260724000000", fitid1="repetido",
            data2="20260724000000", fitid2="repetido",
        ).replace("-20.00", "-10.00", 1)
        plano = gerar_plano(ler_ofx(self.criar_ofx(dados)), [], date(2026, 7, 24))
        self.assertEqual(plano["arquivo"]["transacoes"], 2)
        self.assertEqual(plano["cruzamento"]["novos"], 1)
        self.assertEqual(plano["cruzamento"]["novas_ocorrencias"], 2)
        self.assertEqual(plano["cruzamento"]["repeticoes_identicas"], 1)

    def test_horario_diferente_no_mesmo_dia_e_conteudo_pendente(self):
        original = ler_ofx(self.criar_ofx(OFX_BASE.format(
            data1="20260724000000", fitid1="horario", data2="20260726000000", fitid2="outro")))
        alterado = ler_ofx(self.criar_ofx(OFX_BASE.format(
            data1="20260724010000", fitid1="horario", data2="20260726000000", fitid2="outro")))
        plano = gerar_plano(alterado, [original["transacoes"][0]], date(2026, 7, 26))
        self.assertEqual(plano["cruzamento"]["ja_presentes"], 0)
        self.assertEqual(plano["cruzamento"]["indeterminados"], 1)
        self.assertEqual(plano["cruzamento"]["indeterminados_por_motivo"], {"conflito_de_conteudo": 1})

    def test_zero_nao_omitido_e_entradas_nao_sao_mutadas_ou_expostas(self):
        ofx = ler_ofx(self.criar_ofx(OFX_BASE.format(
            data1="20260724000000", fitid1="zero", data2="20260726000000", fitid2="outro"
        ).replace("-10.00", "0.00", 1)))
        antes = deepcopy(ofx)
        plano = gerar_plano(ofx, [], date(2026, 7, 26))
        self.assertEqual(plano["arquivo"]["transacoes"], 2)
        self.assertEqual(plano["cruzamento"]["novos"], 2)
        self.assertEqual(ofx, antes)
        texto = json.dumps(plano, ensure_ascii=False)
        for privado in ("BANCO-TESTE", "zero", "-20.00", "Teste"):
            self.assertNotIn(privado, texto)
        self.assertEqual(len(plano["comparacao"]["dados_comparados_sha256"]), 64)

    def test_snapshot_invalido_nao_vira_lista_vazia(self):
        caminho = self.criar_ofx(OFX_BASE.format(
            data1="20260724000000", fitid1="F1",
            data2="20260726000000", fitid2="F2",
        ))
        with self.assertRaisesRegex(ValueError, "snapshot inválido"):
            gerar_plano(ler_ofx(caminho), None, date(2026, 7, 26))

    def test_plano_id_e_estavel_e_muda_com_dado_comparado(self):
        caminho = self.criar_ofx(OFX_BASE.format(
            data1="20260724000000", fitid1="F1",
            data2="20260726000000", fitid2="F2",
        ))
        ofx = ler_ofx(caminho)
        primeiro = gerar_plano(ofx, [], date(2026, 7, 26))
        segundo = gerar_plano(ofx, [], date(2026, 7, 26))
        alterado = gerar_plano(ofx, [{"id_externo": "legado"}], date(2026, 7, 26))
        self.assertEqual(primeiro["plano_id"], segundo["plano_id"])
        self.assertNotEqual(primeiro["plano_id"], alterado["plano_id"])


def _assinatura_local(valor):
    """Mesma assinatura de identidade_bancaria sem expor o helper em teste."""
    return hashlib.sha256(json.dumps(valor, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


if __name__ == "__main__":
    unittest.main()
