"""Testes offline do planejador de investigações — nenhum teste toca rede.

A prova contra o schema real (INSERTs no PostgreSQL efêmero com as migrações
0001+0002 aplicadas) fica em tools/test_migracao_postgres.py, que o CI roda
com --obrigatorio.
"""

from __future__ import annotations

import io
import json
import unittest
import urllib.error

from tools import investigacoes_revisao as biblioteca
from tools import planejador_investigacoes as planejador


def draft_base(**extra) -> dict:
    draft = {
        "id": "77777777-7777-4777-8777-777777777771",
        "status": "aguardando_confirmacao",
        "atualizado_em": "2026-09-01T12:00:00+00:00",
        "entidade_final_tipo": "compras",
        "tipo_operacao": "compra_gado",
        "codigo_sugerido": "NEG-26-041",
        "contexto_nome": "Compra de Gado",
        "origem_canal": "telegram",
        "origem_conversa_id": "-100200300",
        "origem_mensagem_id": "555",
        "dados_extraidos": {"quantidade": 12},
        "campos_pendentes": ["valor_total"],
        "escopo": "revisoes",
    }
    draft.update(extra)
    return draft


class PlanoDoDraftTest(unittest.TestCase):
    def test_plano_deterministico_e_completo(self) -> None:
        um = planejador.plano_do_draft(draft_base())
        dois = planejador.plano_do_draft(draft_base())
        self.assertEqual(um["plano_hash"], dois["plano_hash"])
        self.assertEqual(um["chaves"], dois["chaves"])
        outro = planejador.plano_do_draft(
            draft_base(id="77777777-7777-4777-8777-777777777772")
        )
        self.assertNotEqual(um["chaves"]["investigacao"],
                            outro["chaves"]["investigacao"])
        # Forma canônica exigida pelo gate do banco (jsonb_build_object).
        canonico = json.loads(um["plano_canonico"])
        self.assertEqual(
            set(canonico), {"campos_obrigatorios", "policy_schema_hash",
                            "tarefas"}
        )
        self.assertEqual(canonico["policy_schema_hash"],
                         biblioteca.HASH_SCHEMA_POLITICAS)
        self.assertEqual(canonico["tarefas"], um["tarefas"])

    def test_tarefas_ordenadas_fonte_e_sintese(self) -> None:
        item = planejador.plano_do_draft(draft_base())
        self.assertEqual(len(item["tarefas"]), 2)
        refs = [t["plano_item_ref"] for t in item["tarefas"]]
        self.assertEqual(refs, sorted(refs))
        adaptadores = {t["adaptador"] for t in item["tarefas"]}
        self.assertEqual(adaptadores, {planejador.ADAPTADOR_FONTE, "sintese"})
        for tarefa in item["tarefas"]:
            # A consulta persistida precisa se reconstruir com integridade.
            spec = biblioteca.resolver_consulta_tarefa(tarefa)
            self.assertEqual(spec, tarefa["consulta_spec"])

    def test_politica_de_campos_segue_entidade(self) -> None:
        compras = planejador.plano_do_draft(draft_base())
        self.assertEqual(
            tuple(compras["campos_obrigatorios"]),
            biblioteca.POLITICAS_CAMPOS_OBRIGATORIOS["compra"],
        )
        generico = planejador.plano_do_draft(
            draft_base(entidade_final_tipo="extrato_bancario")
        )
        self.assertEqual(tuple(generico["campos_obrigatorios"]),
                         ("decisao_humana",))

    def test_pergunta_nunca_carrega_uuid_do_rascunho(self) -> None:
        sem_codigo = draft_base(codigo_sugerido="", contexto_nome="")
        item = planejador.plano_do_draft(sem_codigo)
        fonte = next(t for t in item["tarefas"]
                     if t["adaptador"] == planejador.ADAPTADOR_FONTE)
        self.assertNotIn(str(sem_codigo["id"]),
                         fonte["consulta_spec"]["pergunta"])
        self.assertIn("sem codigo sugerido",
                      fonte["consulta_spec"]["pergunta"])
        self.assertEqual(fonte["consulta_spec"]["termos"], [])
        com_codigo = planejador.plano_do_draft(draft_base())
        fonte_codigo = next(t for t in com_codigo["tarefas"]
                            if t["adaptador"] == planejador.ADAPTADOR_FONTE)
        self.assertIn("neg-26-041", fonte_codigo["consulta_spec"]["termos"])


class PlanejarTest(unittest.TestCase):
    def test_limite_fechado(self) -> None:
        for limite in (0, 11, -1):
            with self.assertRaises(ValueError):
                planejador.planejar([draft_base()], set(), limite)

    def test_ignora_status_nao_planejavel(self) -> None:
        plano = planejador.planejar(
            [draft_base(status="executado"), draft_base(status="cancelado")],
            set(), 5,
        )
        self.assertEqual(plano["itens"], [])
        self.assertEqual(plano["drafts_ignorados"], 2)

    def test_chave_existente_completa_e_pulada(self) -> None:
        item = planejador.plano_do_draft(draft_base())
        plano = planejador.planejar(
            [draft_base()], {item["chaves"]["investigacao"]}, 5,
            {item["chaves"]["tarefa"]},
        )
        self.assertEqual(plano["itens"], [])
        self.assertEqual(plano["ja_investigados"], 1)
        # Compatibilidade: sem o conjunto de tarefas, o comportamento antigo
        # (pular toda chave existente) permanece.
        antigo = planejador.planejar(
            [draft_base()], {item["chaves"]["investigacao"]}, 5,
        )
        self.assertEqual(antigo["ja_investigados"], 1)

    def test_investigacao_sem_tarefa_vira_reparo(self) -> None:
        item = planejador.plano_do_draft(draft_base())
        plano = planejador.planejar(
            [draft_base()], {item["chaves"]["investigacao"]}, 5, set(),
        )
        self.assertEqual(len(plano["itens"]), 1)
        self.assertEqual(plano["itens"][0]["modo"], "reparar_tarefa")
        novo = planejador.planejar([draft_base()], set(), 5, set())
        self.assertEqual(novo["itens"][0]["modo"], "criar")
        # O modo participa da confirmação: reparar e criar nunca se confundem.
        self.assertNotEqual(plano["confirmacao"], novo["confirmacao"])

    def test_limite_corta_o_lote(self) -> None:
        drafts = [
            draft_base(id=f"77777777-7777-4777-8777-77777777777{n}")
            for n in range(1, 6)
        ]
        plano = planejador.planejar(drafts, set(), 2, set())
        self.assertEqual(len(plano["itens"]), 2)

    def test_resumo_sanitizado_sem_payload(self) -> None:
        sensivel = draft_base(
            dados_extraidos={"observacao": "conteudo-privado-nao-imprimivel"}
        )
        plano = planejador.planejar([sensivel], set(), 1, set())
        resumo = json.dumps(planejador.resumo_sanitizado(plano),
                            ensure_ascii=False)
        self.assertNotIn("dados_extraidos", resumo)
        self.assertNotIn("conteudo-privado-nao-imprimivel", resumo)
        self.assertIn('"modo": "criar"', resumo)


class ClienteTest(unittest.TestCase):
    def test_allowlist_de_escrita(self) -> None:
        cliente = planejador.ClientePlanejador("https://exemplo.invalid", "x")
        for tabela in ("operation_drafts", "pending_actions", "compras",
                       "eventos", "investigacao_evidencias"):
            with self.assertRaises(ValueError):
                cliente.inserir(tabela, {})

    def test_conflito_idempotente_vira_ja_existia(self) -> None:
        cliente = planejador.ClientePlanejador("https://exemplo.invalid", "x")
        erro = urllib.error.HTTPError(
            "https://exemplo.invalid", 409, "conflict", {},  # type: ignore[arg-type]
            io.BytesIO(b"{}"),
        )

        def urlopen_falso(requisicao, timeout=0):
            raise erro

        original = planejador.urllib.request.urlopen
        planejador.urllib.request.urlopen = urlopen_falso
        try:
            resultado = cliente.inserir("investigacoes_revisao", {"x": 1})
        finally:
            planejador.urllib.request.urlopen = original
        self.assertEqual(resultado, "ja_existia")


if __name__ == "__main__":
    unittest.main()
