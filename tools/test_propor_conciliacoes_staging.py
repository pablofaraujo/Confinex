import unittest
import json
import urllib.error
from unittest import mock

from tools.identidade_bancaria import assinatura
from tools.test_identidade_bancaria import transacao_identificada, operacional

from tools.propor_conciliacoes_staging import (
    EscritorConciliacoes,
    id_conciliacao,
    planejar,
    executar,
    chave_duplicidade_transacao,
    decimal_positivo,
)


def transacao(**mudancas):
    base = {
        **transacao_identificada(),
        "id": "11111111-1111-1111-1111-111111111111", "fitid": "F1",
        "conta": "756:123",
        "data": "2026-08-10", "valor": "-1000", "descricao": "PIX Fornecedor Alfa",
        "memo": "Acerto Alfa", "estado": "nao_revisada",
    }
    return {**base, **mudancas}


def candidato(**mudancas):
    base = {
        "id": "22222222-2222-2222-2222-222222222222", "data_base": "2026-08-01",
        "valor_total": "1000", "nome": "Fornecedor Alfa", "codigo_fonte": "NEG-26-900",
        "contexto": "Compras Fazenda", "estado": "em_revisao",
    }
    return {**base, **mudancas}


class ProporConciliacoesStagingTest(unittest.TestCase):
    def test_valor_data_texto_unicos_geram_forte(self):
        plano = planejar([transacao()], [candidato()], [], [], [])
        self.assertEqual(plano["resumo"]["propostas"], 1)
        self.assertEqual(plano["resumo"]["por_classificacao"], {"forte": 1})
        self.assertEqual(plano["resumo"]["tabelas_operacionais_alteradas"], 0)

    def test_valor_data_sem_texto_gera_provavel(self):
        plano = planejar([transacao(descricao="PIX", memo="")], [candidato()], [], [], [])
        self.assertEqual(plano["propostas"][0]["classificacao"], "provavel")

    def test_texto_unico_pode_indicar_pagamento_parcial(self):
        plano = planejar([transacao(valor="-400")], [candidato()], [], [], [])
        self.assertEqual(plano["propostas"][0]["classificacao"], "possivel")
        self.assertEqual(plano["propostas"][0]["valor_alocado"], "400.00")

    def test_multiplos_alvos_e_duplicidade_sao_preservados(self):
        ambiguo = planejar(
            [transacao()], [candidato(), candidato(id="33333333-3333-3333-3333-333333333333")],
            [], [], [],
        )
        self.assertEqual(ambiguo["resumo"]["propostas"], 0)
        self.assertEqual(ambiguo["resumo"]["ambiguidades_preservadas"]["mais_de_um_alvo_compativel"], 1)
        duplicado = planejar(
            [transacao(), transacao(id="44444444-4444-4444-4444-444444444444", fitid="F2")],
            [candidato()], [], [], [],
        )
        self.assertEqual(duplicado["resumo"]["propostas"], 0)
        self.assertEqual(duplicado["resumo"]["ambiguidades_preservadas"]["duplicidade_aparente_entre_fontes"], 2)

    def test_fitid_operacional_e_proposta_existente_nao_repetem(self):
        legado = planejar([transacao()], [candidato()], [], [], [{"id_externo": "F1"}])
        self.assertEqual(legado["resumo"]["propostas"], 0)
        self.assertEqual(legado["resumo"]["ambiguidades_preservadas"], {"identidade_pendente": 1})
        presente = planejar([transacao()], [candidato()], [], [], [operacional(transacao())])
        self.assertEqual(presente["resumo"]["ignoradas_por_motivo"], {"ja_existe_no_banco_operacional": 1})
        existente = [{
            "transacao_staging_id": transacao()["id"],
            "negocio_candidato_id": candidato()["id"], "fluxo_caixa_id": None,
        }]
        repetido = planejar([transacao()], [candidato()], [], existente, [])
        self.assertEqual(repetido["resumo"]["propostas"], 0)

    def test_id_deterministico_e_escritor_bloqueia_operacional(self):
        self.assertEqual(id_conciliacao("t", "negocio", "n"), id_conciliacao("t", "negocio", "n"))
        escritor = EscritorConciliacoes("https://exemplo.invalid", "segredo")
        with self.assertRaisesRegex(ValueError, "escrita não permitida"):
            escritor.inserir("transacoes_banco", {})

    def test_conta_distinta_nao_suprime_proposta_e_sinal_nao_deduplica(self):
        outro = operacional(transacao())
        dados = outro["dados_origem"]["ofx"]
        dados["identidade"]["BRANCHID"] = "02"
        dados["identidade_sha256"] = assinatura(dados["identidade"])
        plano = planejar([transacao()], [candidato()], [], [], [outro])
        self.assertEqual(len(plano["propostas"]), 1)
        credito = transacao(id="credito", fitid="F2", valor="1000", tipo="CREDIT")
        self.assertNotEqual(chave_duplicidade_transacao(transacao()), chave_duplicidade_transacao(credito))
        plano = planejar([transacao(), credito], [candidato()], [], [], [])
        self.assertEqual(len(plano["propostas"]), 1)  # Crédito não paga uma compra.

    def test_conflito_de_conteudo_e_mesma_chave_com_memo_diferente_bloqueiam(self):
        plano = planejar([transacao()], [candidato()], [], [], [operacional(transacao(), valor="1000")])
        self.assertEqual(plano["resumo"]["ambiguidades_preservadas"], {"conflito_de_conteudo": 1})
        plano = planejar([transacao(), transacao(id="t2", memo="diferente")], [candidato()], [], [], [])
        self.assertEqual(plano["resumo"]["propostas"], 0)
        self.assertEqual(plano["resumo"]["ambiguidades_preservadas"], {"mesma_identidade_e_fitid_em_varias_linhas": 2})

    def test_valor_invalido_zero_subcentavo_e_fluxo_com_sinal_oposto_nao_propoem(self):
        for valor in (None, "NaN", "Infinity", "0", "-0.001"):
            self.assertEqual(planejar([transacao(valor=valor)], [candidato()], [], [], [])["propostas"], [])
        fluxo = {"id": "f", "data": "2026-08-10", "valor": "1000", "tipo": "entrada"}
        self.assertEqual(planejar([transacao()], [], [fluxo], [], [])["propostas"], [])
        fluxo["tipo"] = "saida"
        self.assertEqual(len(planejar([transacao()], [], [fluxo], [], [])["propostas"]), 1)

    def test_fracao_depois_de_28_digitos_nao_e_arredondada_antes_de_validar(self):
        for valor in ("1.000000000000000000000000000001", "-1.000000000000000000000000000001"):
            self.assertIsNone(decimal_positivo(valor))
            self.assertEqual(planejar([transacao(valor=valor)], [candidato()], [], [], [])["propostas"], [])

    def test_snapshot_vincula_confirmacao_e_mudanca_de_payload_bloqueia(self):
        plano = planejar([transacao()], [candidato()], [], [], [])
        outro = planejar([transacao()], [candidato(nome="Outro")], [], [], [])
        self.assertNotEqual(plano["plano_id"], outro["plano_id"])
        plano["propostas"][0]["valor_alocado"] = "999"
        escritor = mock.Mock()
        with self.assertRaisesRegex(ValueError, "plano alterado"):
            executar(plano, escritor, 1)
        escritor.inserir.assert_not_called()

    @mock.patch("tools.propor_conciliacoes_staging.urllib.request.urlopen")
    def test_writer_concorrencia_timeout_e_resposta_divergente_nao_contam_criacao(self, urlopen):
        escritor = EscritorConciliacoes("https://exemplo.invalid", "segredo")
        plano = planejar([transacao()], [candidato()], [], [], [])
        for falha in (TimeoutError(), urllib.error.HTTPError("https://exemplo.invalid", 409, "conflito", {}, None)):
            urlopen.reset_mock()
            urlopen.side_effect = falha
            with self.assertRaises((TimeoutError, RuntimeError)):
                executar(plano, escritor, 1)
            self.assertEqual(urlopen.call_count, 1)
        urlopen.side_effect = None
        resposta = urlopen.return_value.__enter__.return_value
        resposta.status = 201
        resposta.read.return_value = b'[]'
        with self.assertRaisesRegex(RuntimeError, "comprovado"):
            executar(plano, escritor, 1)
        resposta.read.return_value = json.dumps(plano["propostas"]).encode()
        self.assertEqual(executar(plano, escritor, 1), 1)
        self.assertEqual(urlopen.call_args.args[0].get_header("Prefer"), "return=representation")


if __name__ == "__main__":
    unittest.main()
