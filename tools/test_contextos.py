from __future__ import annotations

import unittest

from contexto_canonico import contexto_canonico, montar_contexto, origem_conversa_tecnica
from normalizar_contextos import (
    build_plan,
    candidate_draft,
    candidate_plan_id,
    create_candidate_draft,
    execute_plan,
)


MAP = [{
    "contexto_canonico": "telegram:grupo:-1234567890",
    "contexto_nome": "Grupo Operacional",
    "origem_canal": "telegram",
    "origem_conversa_id": "-1234567890",
    "escopo": "grupo",
    "aliases": ["telegram:-1234567890", "Nome Histórico"],
}]


class FakeClient:
    def __init__(self):
        self.rows = {
            "operation_drafts": [{"id": "d1", "origem_canal": "telegram", "origem_conversa_id": "telegram:-1234567890", "origem_mensagem_id": "m1", "agente": "juan"}],
            "pending_actions": [{"id": "p1", "canal": "telegram", "conversa_id": "Nome Histórico", "mensagem_id": "m2", "agente": "juan"}],
            "eventos": [{"id": "e1", "origem_canal": "telegram", "origem_conversa_id": "sem mapa", "origem_mensagem_id": "m3", "agente": "juan"}],
            "memorias_agentes": [],
            "contextos_canais": [],
            "contexto_handoff": [{"id": "h1", "status": "aberto"}],
        }
        self.updates = []
        self.inserts = []

    def select(self, table, **_kwargs):
        return self.rows[table]

    def update(self, table, filters, payload):
        self.updates.append((table, filters, payload))
        return [{"id": filters["id"][3:]}]

    def insert(self, table, payload):
        self.inserts.append((table, payload))
        return {"id": f"{table}-novo"}


class ContextoCanonicoTests(unittest.TestCase):
    def test_preserva_id_tecnico_e_separa_nome(self):
        context = montar_contexto(
            contexto_nome="Grupo Operacional",
            origem_canal="telegram",
            origem_conversa_id="telegram:-1234567890",
            origem_mensagem_id="m1",
            agente="Juan",
        )
        self.assertEqual(context["origem_conversa_id"], "-1234567890")
        self.assertEqual(context["contexto_nome"], "Grupo Operacional")
        self.assertEqual(context["contexto_canonico"], "telegram:grupo:-1234567890")
        self.assertEqual(context["escopo"], "grupo")
        self.assertEqual(origem_conversa_tecnica("Grupo Operacional"), None)
        self.assertEqual(contexto_canonico("telegram", "Grupo Operacional"), None)

    def test_dry_run_nao_escreve_e_isola_ambiguos(self):
        client = FakeClient()
        plan = build_plan(client, MAP)
        self.assertEqual(plan["total_alteracoes"], 2)
        self.assertEqual(plan["total_ignorados"], 1)
        self.assertEqual(plan["escritas_realizadas"], 0)
        self.assertEqual(len(plan["handoffs_para_triagem"]), 1)
        self.assertEqual(client.updates, [])
        self.assertEqual(plan["alteracoes"][0]["depois"]["contexto_nome"], "Grupo Operacional")

    def test_execucao_exige_frase_vinculada_ao_plano(self):
        client = FakeClient()
        plan = build_plan(client, MAP)
        with self.assertRaisesRegex(Exception, "use exatamente"):
            execute_plan(client, plan, "NORMALIZAR CONTEXTOS errado")
        result = execute_plan(client, plan, f"NORMALIZAR CONTEXTOS {plan['plano_id']}")
        self.assertEqual(result["escritas_realizadas"], 3)

    def test_lacuna_de_venda_vira_apenas_rascunho(self):
        import json
        import tempfile
        from pathlib import Path

        payload = {
            "tipo_operacao": "venda_abate_para_revisao",
            "origem_canal": "telegram",
            "origem_conversa_id": "-1234567890",
            "origem_mensagem_id": "m-real",
            "agente": "juan",
            "dados_extraidos": {"cabecas": 13},
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "candidato.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            draft = candidate_draft(path, MAP)
        self.assertEqual(draft["status"], "rascunho")
        self.assertEqual(draft["entidade_final_tipo"], "vendas")
        self.assertFalse(draft["inferencias"]["confirmacao_suficiente"])
        self.assertNotIn("vendas", getattr(FakeClient(), "updates", []))
        client = FakeClient()
        client.rows["operation_drafts"] = []
        with self.assertRaisesRegex(Exception, "use exatamente"):
            create_candidate_draft(client, draft, "")
        result = create_candidate_draft(
            client,
            draft,
            f"CRIAR RASCUNHO {candidate_plan_id(draft)}",
        )
        self.assertEqual(result["tabelas_operacionais_alteradas"], 0)
        self.assertEqual([table for table, _ in client.inserts], ["operation_drafts", "eventos"])


if __name__ == "__main__":
    unittest.main()
