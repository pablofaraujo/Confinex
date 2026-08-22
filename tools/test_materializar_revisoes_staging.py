import unittest

from tools.materializar_revisoes_staging import (
    EscritorRevisao,
    montar_registros,
    planejar,
)


def candidato(**mudancas):
    base = {
        "id": "11111111-1111-1111-1111-111111111111",
        "estado": "em_revisao", "prioridade": "alta", "campos_faltantes": [],
        "codigo_fonte": "NEG-26-900", "chave_rastreio": "fonte-900",
        "nome": "Fornecedor teste", "contexto": "Compras Fazenda",
        "quantidade": 10, "peso_total_kg": "3000", "preco_arroba": "300",
        "valor_total": "30000", "data_base": "2026-08-01",
        "dados_origem": {"confirmado_na_planilha": True},
    }
    return {**base, **mudancas}


class MaterializarRevisoesStagingTest(unittest.TestCase):
    def test_planeja_tripla_sem_operacao(self):
        plano = planejar([candidato()], [], [], [], [])
        self.assertEqual(plano["resumo"]["revisoes_planejadas"], 1)
        self.assertEqual(plano["resumo"]["tabelas_operacionais_alteradas"], 0)
        registros = plano["registros"][0]
        self.assertEqual(set(registros), {"operation_drafts", "pending_actions", "eventos"})

    def test_preserva_duplicidade_e_referencia_operacional(self):
        duplicados = [candidato(), candidato(id="22222222-2222-2222-2222-222222222222")]
        plano = planejar(duplicados, [], [], [], [])
        self.assertEqual(plano["resumo"]["revisoes_planejadas"], 0)
        operacional = planejar([candidato()], [{"id": "o1", "codigo": "NEG-26-900"}], [], [], [])
        self.assertEqual(operacional["resumo"]["revisoes_planejadas"], 0)

    def test_ids_sao_deterministicos_e_pendente_preserva_confirmacao(self):
        primeiro = montar_registros(candidato())
        segundo = montar_registros(candidato())
        self.assertEqual(primeiro, segundo)
        self.assertIn(
            "confirmar vínculo com negócio operacional existente ou novo",
            primeiro["operation_drafts"]["campos_pendentes"],
        )
        self.assertFalse(primeiro["eventos"]["dados"]["promovido_para_operacional"])

    def test_conjunto_parcial_e_completado_sem_duplicar(self):
        registros = montar_registros(candidato())
        plano = planejar(
            [candidato()], [], [registros["operation_drafts"]], [], [],
        )
        self.assertEqual(plano["resumo"]["revisoes_planejadas"], 1)
        self.assertEqual(
            plano["resumo"]["ignorados_por_motivo"]["conjunto_parcial_a_completar"],
            1,
        )

        completo = planejar(
            [candidato()], [], [registros["operation_drafts"]],
            [registros["pending_actions"]], [registros["eventos"]],
        )
        self.assertEqual(completo["resumo"]["revisoes_planejadas"], 0)

    def test_escritor_bloqueia_operacional(self):
        escritor = EscritorRevisao("https://exemplo.invalid", "segredo")
        with self.assertRaisesRegex(ValueError, "escrita não permitida"):
            escritor.inserir("compras", {})


if __name__ == "__main__":
    unittest.main()
