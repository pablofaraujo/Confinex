import unittest

from tools.propor_conciliacoes_staging import (
    EscritorConciliacoes,
    id_conciliacao,
    planejar,
)


def transacao(**mudancas):
    base = {
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
        operacional = planejar([transacao()], [candidato()], [], [], [{"id_externo": "F1"}])
        self.assertEqual(operacional["resumo"]["propostas"], 0)
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


if __name__ == "__main__":
    unittest.main()
