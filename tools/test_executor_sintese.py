"""Testes offline do executor de síntese — nenhum teste toca rede.

A prova de ponta a ponta (fonte vazia → síntese conclui a investigação no
schema pós-0002) fica em tools/test_migracao_postgres.py (CI com
--obrigatorio).
"""

from __future__ import annotations

import json
import unittest

from tools import executor_sintese as executor

CHAVES_PENDENCIA = {
    "id_logico", "chave_idempotencia", "tipo", "campo", "fonte_tipo",
    "descricao_sanitizada", "estado",
}


def investigacao_base(**extra) -> dict:
    investigacao = {
        "id": "9a000000-0000-4000-8000-0000000000f1",
        "chave_idempotencia": "inv_00000000000000000000000000000001",
        "policy_version": "investigacao-v1",
        "campos_obrigatorios": ["data", "negocio", "quantidade",
                                "valor_total"],
        "plano_tarefas": [
            {"plano_item_ref": "pitem_fonte", "adaptador": "outro",
             "adaptador_version": "v1", "consulta_ref": "qref_fonte",
             "consulta_schema_version": "consulta-v1",
             "consulta_spec": {"tipo": "busca_operacional"},
             "consulta_canonico": "{}", "consulta_hash": "0" * 64},
            {"plano_item_ref": "pitem_sintese", "adaptador": "sintese",
             "adaptador_version": "investigacao-v1",
             "consulta_ref": "qref_sintese",
             "consulta_schema_version": "consulta-v1",
             "consulta_spec": {"tipo": "sintese"},
             "consulta_canonico": "{}", "consulta_hash": "1" * 64},
        ],
    }
    investigacao.update(extra)
    return investigacao


def fonte(estado="concluida", cobertura="vazio_com_cobertura") -> dict:
    return {"estado_execucao": estado, "estado_cobertura": cobertura}


class LinhaSinteseTest(unittest.TestCase):
    def test_linha_deterministica_e_fiel_ao_plano(self) -> None:
        investigacao = investigacao_base()
        um = executor.linha_tarefa_sintese(investigacao)
        dois = executor.linha_tarefa_sintese(investigacao)
        self.assertEqual(um, dois)
        self.assertEqual(um["adaptador"], "sintese")
        self.assertEqual(um["plano_item_ref"], "pitem_sintese")
        self.assertEqual(um["consulta_hash"], "1" * 64)
        self.assertEqual(um["investigacao_id"], investigacao["id"])

    def test_plano_sem_sintese_e_recusado(self) -> None:
        investigacao = investigacao_base()
        investigacao["plano_tarefas"] = investigacao["plano_tarefas"][:1]
        with self.assertRaises(ValueError):
            executor.linha_tarefa_sintese(investigacao)


class CoberturaTest(unittest.TestCase):
    def test_derivacao_replica_o_banco(self) -> None:
        self.assertEqual(
            executor.cobertura_das_fontes([fonte(cobertura="completa")]),
            "completa")
        self.assertEqual(
            executor.cobertura_das_fontes([fonte(), fonte()]),
            "vazio_com_cobertura")
        self.assertEqual(
            executor.cobertura_das_fontes(
                [fonte(cobertura="completa"), fonte()]),
            "completa")
        self.assertEqual(
            executor.cobertura_das_fontes(
                [fonte(cobertura="indisponivel"), fonte()]),
            "cobertura_incompleta")

    def test_fonte_nao_terminal_ou_sem_cobertura_aborta(self) -> None:
        with self.assertRaises(ValueError):
            executor.cobertura_das_fontes([fonte(estado="pendente")])
        with self.assertRaises(ValueError):
            executor.cobertura_das_fontes([fonte(cobertura=None)])
        with self.assertRaises(ValueError):
            executor.cobertura_das_fontes([])


class MontagemTest(unittest.TestCase):
    def test_sem_evidencias_gera_pendencia_por_campo(self) -> None:
        investigacao = investigacao_base()
        resultado = executor.montar_resultado_sintese(
            investigacao, [fonte()], [], "9a000000-0000-4000-8000-0000000000f2",
        )
        self.assertEqual(resultado["estado_cobertura"], "vazio_com_cobertura")
        self.assertEqual(resultado["estado_resultado"],
                         "evidencia_insuficiente")
        pendencias = resultado["bundle"]["pendencias"]
        self.assertEqual({p["campo"] for p in pendencias},
                         set(investigacao["campos_obrigatorios"]))
        for pendencia in pendencias:
            self.assertEqual(set(pendencia), CHAVES_PENDENCIA)
            self.assertEqual(pendencia["estado"], "aberta")
            self.assertIsNone(pendencia["fonte_tipo"])
        self.assertEqual(resultado["bundle"]["alternativas"], [])
        self.assertEqual(resultado["bundle"]["evidencias"], [])
        self.assertEqual(resultado["bundle"]["ligacoes"], [])

    def test_cobertura_falha_vira_resultado_incompleto(self) -> None:
        resultado = executor.montar_resultado_sintese(
            investigacao_base(), [fonte(cobertura="indisponivel")], [],
            "9a000000-0000-4000-8000-0000000000f2",
        )
        self.assertEqual(resultado["estado_cobertura"], "cobertura_incompleta")
        self.assertEqual(resultado["estado_resultado"], "cobertura_incompleta")
        # A cobertura entregue à geração de pendências é a GERAL derivada
        # ('cobertura_incompleta'), não a da fonte individual.
        tipos = {p["tipo"] for p in resultado["bundle"]["pendencias"]}
        self.assertIn("cobertura_incompleta", tipos)
        self.assertIn("dado_ausente", tipos)

    def test_evidencias_presentes_exigem_v2(self) -> None:
        with self.assertRaises(ValueError) as contexto:
            executor.montar_resultado_sintese(
                investigacao_base(), [fonte(cobertura="completa")],
                [{"id": "qualquer"}], "9a000000-0000-4000-8000-0000000000f2",
            )
        self.assertIn("EVIDENCIAS_EXIGEM_SINTESE_V2", str(contexto.exception))

    def test_montagem_deterministica(self) -> None:
        um = executor.montar_resultado_sintese(
            investigacao_base(), [fonte()], [],
            "9a000000-0000-4000-8000-0000000000f2")
        dois = executor.montar_resultado_sintese(
            investigacao_base(), [fonte()], [],
            "9a000000-0000-4000-8000-0000000000f2")
        self.assertEqual(json.dumps(um, sort_keys=True),
                         json.dumps(dois, sort_keys=True))


class PlanoEClienteTest(unittest.TestCase):
    def test_plano_tem_hash_e_modo_de_materializacao(self) -> None:
        investigacao = investigacao_base()
        linha = executor.linha_tarefa_sintese(investigacao)
        resultado = executor.montar_resultado_sintese(
            investigacao, [fonte()], [], linha["id"])
        novo = executor.plano_execucao(investigacao, linha, False, resultado)
        repetido = executor.plano_execucao(investigacao, linha, True, resultado)
        self.assertTrue(novo["materializar"])
        self.assertFalse(repetido["materializar"])
        self.assertNotEqual(novo["confirmacao"], repetido["confirmacao"])
        self.assertEqual(len(novo["confirmacao"]), 64)

    def test_allowlists_fechadas(self) -> None:
        cliente = executor.ClienteSintese("https://exemplo.invalid", "x")
        for tabela in ("investigacoes_revisao", "investigacao_evidencias",
                       "pending_actions", "compras"):
            with self.assertRaises(ValueError):
                cliente.inserir(tabela, {})
        for rpc in ("materializar_revisao_investigada",
                    "preparar_promocao_revisao_investigada",
                    "decidir_promocao_operacional"):
            with self.assertRaises(ValueError):
                cliente.rpc(rpc, {})


if __name__ == "__main__":
    unittest.main()
