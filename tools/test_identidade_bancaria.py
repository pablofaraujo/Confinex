"""Fixtures fictícias; nunca consulta a base nem usa identidade bancária real."""

import copy
import unittest

from tools.identidade_bancaria import (
    assinatura, avaliar_presenca, chave_logica, comparar_conteudo,
    decimal_assinado, identidade_completa,
)


def transacao_identificada(**mudancas):
    identidade = {"BANKID": "B1", "BRANCHID": "01", "ACCTID": "0007", "ACCTTYPE": "CHECKING", "CURDEF": "BRL"}
    return {
        "id": "tx-ficticia", "fitid": "F1", "conta": "B1:0007", "data": "2026-08-10",
        "valor": "-1000", "tipo": "DEBIT", "descricao": "PIX Fornecedor Alfa", "memo": "Acerto Alfa",
        "estado": "nao_revisada", "dados_origem": {"ofx": {
            "versao": 1, "identidade": identidade, "identidade_sha256": assinatura(identidade),
            "data_ofx_original": "20260810120000[-3:BRT]", "stmttrn_sha256": "a" * 64,
        }}, **mudancas,
    }


def operacional(item=None, **mudancas):
    dado = copy.deepcopy(item or transacao_identificada())
    dado["id_externo"] = dado.pop("fitid")
    dado.update(mudancas)
    return dado


class IdentidadeBancariaTest(unittest.TestCase):
    def test_identidade_textual_preserva_zeros_caixa_e_moeda(self):
        tx = transacao_identificada()
        chave = chave_logica(tx)
        self.assertEqual(chave[0][2], "0007")
        for campo, valor in (("ACCTID", "7"), ("BANKID", "b1"), ("BRANCHID", "02"), ("CURDEF", "USD")):
            novo = copy.deepcopy(tx)
            identidade = novo["dados_origem"]["ofx"]["identidade"]
            identidade[campo] = valor
            novo["dados_origem"]["ofx"]["identidade_sha256"] = assinatura(identidade)
            self.assertNotEqual(chave, chave_logica(novo))
        identidade = tx["dados_origem"]["ofx"]["identidade"]
        self.assertIsNone(identidade_completa({**identidade, "ACCTID": 7}))
        self.assertIsNone(identidade_completa({**identidade, "CURDEF": ""}))

    def test_rotulo_de_conta_nao_substitui_prova(self):
        tx = transacao_identificada()
        self.assertEqual(avaliar_presenca(tx, [{"conta": tx["conta"], "id_externo": "F1"}]), "identidade_pendente")
        legado = {**tx, "dados_origem": {}}
        self.assertEqual(avaliar_presenca(legado, []), "identidade_pendente")
        tx["dados_origem"]["ofx"]["identidade_sha256"] = "corrompido"
        self.assertIsNone(chave_logica(tx))

    def test_mesma_fitid_outra_conta_nao_comprova_presenca(self):
        tx = transacao_identificada()
        outro = operacional(tx)
        ident = outro["dados_origem"]["ofx"]["identidade"]
        ident["BRANCHID"] = "02"
        outro["dados_origem"]["ofx"]["identidade_sha256"] = assinatura(ident)
        self.assertEqual(avaliar_presenca(tx, [outro]), "ausente_na_amostra")
        self.assertEqual(avaliar_presenca(tx, [operacional(tx)]), "presente_por_identidade")
        self.assertEqual(avaliar_presenca(tx, [operacional(tx), operacional(tx, id="outro")]), "referencia_ambigua")

    def test_conteudo_sinal_horario_memo_e_ausencias_nao_sao_iguais(self):
        tx = transacao_identificada()
        for campo, valor in (("valor", "1000"), ("data", "2026-08-11"), ("memo", "outro"), ("tipo", "CREDIT")):
            self.assertEqual(comparar_conteudo(tx, {**tx, campo: valor}), "divergente")
        outro = copy.deepcopy(tx)
        outro["dados_origem"]["ofx"]["data_ofx_original"] = "20260810000000"
        self.assertEqual(comparar_conteudo(tx, outro), "divergente")
        self.assertEqual(comparar_conteudo(tx, {**tx, "dados_origem": {}}), "incompleto")
        self.assertEqual(comparar_conteudo(tx, {**tx, "valor": "-1000.00"}), "igual")

    def test_vinculo_explicito_exige_alvo_unico_e_dados_compativeis(self):
        tx = transacao_identificada(transacao_banco_id="destino")
        outro = operacional(tx, id="destino")
        self.assertEqual(avaliar_presenca(tx, [outro]), "presente_por_vinculo")
        self.assertEqual(avaliar_presenca(tx, []), "vinculo_nao_comprovado")
        self.assertEqual(avaliar_presenca(tx, [outro, outro]), "vinculo_nao_comprovado")
        self.assertEqual(avaliar_presenca(tx, [{**outro, "valor": "1000"}]), "conflito_de_conteudo")
        self.assertEqual(avaliar_presenca(tx, [{**outro, "data": None}]), "vinculo_nao_comprovado")

    def test_numeros_nao_finitos_nao_equivalem_a_zero(self):
        for valor in (None, True, "NaN", "Infinity", "abc"):
            self.assertIsNone(decimal_assinado(valor))
        self.assertEqual(decimal_assinado("0.00"), 0)
        self.assertLess(decimal_assinado("-0.01"), 0)


if __name__ == "__main__":
    unittest.main()
