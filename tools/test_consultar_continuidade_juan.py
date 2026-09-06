#!/usr/bin/env python3
"""Regressões adversariais para a consulta somente leitura do Juan."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import copy
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import consultar_continuidade_juan as modulo


GRUPO = "agent:juan:telegram:group:-700001"
OUTRO = "agent:juan:telegram:group:-700002"
ID_DRAFT = "11111111-1111-4111-8111-111111111111"
ID_PENDING = "22222222-2222-4222-8222-222222222222"
ID_COMPRA_A = "33333333-3333-4333-8333-333333333333"
ID_COMPRA_B = "44444444-4444-4444-8444-444444444444"


def draft(**campos):
    base = {
        "id": ID_DRAFT, "tipo_operacao": "compra", "status": "rascunho",
        "codigo_sugerido": "LOTE-SINTETICO", "dados_extraidos": {"fornecedor": "Fornecedor fictício"},
        "campos_pendentes": [], "pending_action_id": None, "entidade_final_tipo": None,
        "entidade_final_id": None, "origem_canal": "telegram", "origem_conversa_id": "-700001",
        "contexto_canonico": "telegram:grupo:-700001", "contexto_nome": "Grupo fictício",
        "escopo": "grupo",
    }
    base.update(campos)
    return base


def pending(**campos):
    base = {
        "id": ID_PENDING, "status": "aguardando", "acao_tipo": "consultar",
        "entidade_tipo": "compras", "entidade_id": None, "payload": {}, "resultado": {},
        "origem_canal": "telegram", "origem_conversa_id": "-700001", "canal": "telegram",
        "conversa_id": "-700001", "contexto_canonico": "telegram:grupo:-700001",
        "contexto_nome": "Grupo fictício", "escopo": "grupo",
    }
    base.update(campos)
    return base


def compra(identificador=ID_COMPRA_A):
    return {
        "id": identificador, "operacao_id": "55555555-5555-4555-8555-555555555555",
        "data": "2026-09-01", "quantidade": 12, "peso_total_kg": 480,
        "valor_total": 12345.67, "pago": False, "data_pagamento": None,
        "created_at": "2026-09-01T10:00:00Z", "updated_at": "2026-09-01T10:00:00Z",
    }


class LeitorSpy:
    def __init__(self, respostas=None, erro=None):
        self.respostas = respostas or {}
        self.erro = erro
        self.rotas = []

    def __call__(self, rota):
        self.rotas.append(rota)
        if self.erro:
            raise self.erro
        tabela = rota.split("?", 1)[0]
        resposta = self.respostas.get(tabela, [])
        return resposta() if callable(resposta) else resposta


class ConsultarContinuidadeTestCase(unittest.TestCase):
    def test_complemento_preserva_base_e_nao_declara_parecidos_duplicados(self):
        dados = [draft(dados_extraidos={'valor_total': 1200, 'quantidade': 12}),
                 draft(id=ID_COMPRA_B, dados_extraidos={'valor_total': 1300, 'quantidade': 12})]
        antes = copy.deepcopy(dados)
        saida = modulo.consultar(GRUPO, LeitorSpy({'operation_drafts': dados}))
        self.assertEqual(dados, antes)
        self.assertEqual([c['dados']['valor_total'] for c in saida['candidatos']], ['1200', '1300'])
        self.assertTrue(all(c['relacao_com_pedido'] == 'a_confirmar' for c in saida['candidatos']))
        self.assertIn('não duplicidades comprovadas', saida['orientacao'])
        self.assertIn('preservar a base da compra já conferida', saida['orientacao'])
        self.assertIn('candidato estruturado é interno', saida['orientacao'])
        self.assertFalse(saida['consultas_adicionais_permitidas'])
        self.assertEqual(saida['proxima_etapa'], 'responder_com_candidatos_e_pendencias_sem_nova_ferramenta')
        self.assertIn('não autoriza procurar credenciais', saida['orientacao'])
        self.assertFalse(saida['autoriza_escrita'])

    def test_grupo_exato_e_compra_somente_por_target_explicito(self):
        leitor = LeitorSpy({
            "operation_drafts": [draft(pending_action_id=ID_PENDING, entidade_final_tipo="compras", entidade_final_id=ID_COMPRA_A)],
            "pending_actions": [], "compras": [compra()],
        })
        resultado = modulo.consultar(GRUPO, leitor)
        self.assertEqual(resultado["candidatos"][0]["situacao"], "compra_localizada_por_vinculo_do_rascunho")
        self.assertEqual(resultado["candidatos"][0]["compra"]["id"], ID_COMPRA_A)
        compras = [r for r in leitor.rotas if r.startswith("compras?")]
        self.assertEqual(len(compras), 1)
        self.assertIn(ID_COMPRA_A, compras[0])

    def test_compra_sem_target_nao_e_lida_mesmo_com_mesmo_grupo(self):
        leitor = LeitorSpy({"operation_drafts": [draft()], "pending_actions": [], "compras": [compra()]})
        resultado = modulo.consultar(GRUPO, leitor)
        self.assertEqual(resultado["candidatos"][0]["situacao"], "rascunho_localizado_compra_nao_comprovada")
        self.assertFalse(any(r.startswith("compras?") for r in leitor.rotas))

    def test_pending_legado_so_entra_por_id_explicito_do_draft(self):
        legado = pending(contexto_canonico=None, origem_conversa_id=None, canal=None, conversa_id=None, escopo=None)
        leitor = LeitorSpy({
            "operation_drafts": [draft(pending_action_id=ID_PENDING)],
            "pending_actions": [legado], "compras": [],
        })
        resultado = modulo.consultar(GRUPO, leitor)
        self.assertEqual(resultado["candidatos"][0]["status_pendencia"], "aguardando")
        self.assertTrue(any("id=in." in rota for rota in leitor.rotas if rota.startswith("pending_actions?")))

    def test_pending_legado_sem_draft_nao_e_promovido(self):
        legado = pending(contexto_canonico=None, origem_conversa_id=None, canal=None, conversa_id=None, escopo=None)
        leitor = LeitorSpy({"operation_drafts": [], "pending_actions": [legado], "compras": []})
        resultado = modulo.consultar(GRUPO, leitor)
        self.assertEqual(resultado["pendencias_sem_rascunho_no_recorte"], [])

    def test_target_draft_pending_divergente_e_conflito_sem_get_compras(self):
        leitor = LeitorSpy({
            "operation_drafts": [draft(pending_action_id=ID_PENDING, entidade_final_tipo="compras", entidade_final_id=ID_COMPRA_A)],
            "pending_actions": [pending(resultado={"target_table": "compras", "target_record_id": ID_COMPRA_B})],
            "compras": [compra(ID_COMPRA_A), compra(ID_COMPRA_B)],
        })
        resultado = modulo.consultar(GRUPO, leitor)
        self.assertEqual(resultado["candidatos"][0]["situacao"], "vinculos_divergentes")
        self.assertFalse(any(r.startswith("compras?") for r in leitor.rotas))

    def test_contexto_contraditorio_nao_vaza_por_nome_ou_fornecedor(self):
        leitor = LeitorSpy({
            "operation_drafts": [draft(origem_conversa_id="-700002", contexto_canonico="telegram:grupo:-700002")],
            "pending_actions": [], "compras": [],
        })
        resultado = modulo.consultar(GRUPO, leitor)
        self.assertEqual(resultado["candidatos"], [])
        self.assertTrue(resultado["cobertura"]["parcial"])
        self.assertIn("contexto_contraditorio_ou_nao_comprovado", resultado["cobertura"]["motivos"])

    def test_limite_por_tabela_quarenta_marca_parcial_sem_paginacao(self):
        rows = [draft(id=f"aaaaaaaa-aaaa-4aaa-8aaa-{i:012d}") for i in range(41)]
        leitor = LeitorSpy({"operation_drafts": rows, "pending_actions": [], "compras": []})
        resultado = modulo.consultar(GRUPO, leitor, limite=40)
        self.assertEqual(len(resultado["candidatos"]), 40)
        self.assertIn("limite_de_linhas_operation_drafts", resultado["cobertura"]["motivos"])
        self.assertFalse(any("offset=" in rota for rota in leitor.rotas))

    def test_topico_sem_representacao_falha_fechado_sem_leitura(self):
        leitor = LeitorSpy()
        resultado = modulo.consultar(GRUPO + ":topic:8", leitor)
        self.assertEqual(resultado["candidatos"], [])
        self.assertTrue(resultado["cobertura"]["parcial"])
        self.assertEqual(leitor.rotas, [])

    def test_timeout_dns_ou_http_invalido_nao_vira_lista_vazia(self):
        for erro in (TimeoutError("timeout sintético"), OSError("dns sintético"), ValueError("http inválido")):
            with self.subTest(erro=type(erro).__name__):
                leitor = LeitorSpy(erro=erro)
                resultado = modulo.consultar(GRUPO, leitor)
                self.assertTrue(resultado["cobertura"]["parcial"])
                self.assertTrue(any(item["status"] == "indisponivel" for item in resultado["consultas"]))

    def test_resposta_invalida_ids_repetidos_e_dados_nao_sao_ingeridos(self):
        leitor = LeitorSpy({"operation_drafts": [{"id": "não-uuid"}], "pending_actions": [], "compras": []})
        resultado = modulo.consultar(GRUPO, leitor)
        self.assertEqual(resultado["candidatos"], [])
        self.assertIn("consulta_indisponivel_operation_drafts", resultado["cobertura"]["motivos"])

    def test_ids_repetidos_reais_na_resposta_sao_rejeitados(self):
        repetido = draft()
        leitor = LeitorSpy({"operation_drafts": [repetido, dict(repetido)], "pending_actions": [], "compras": []})
        resultado = modulo.consultar(GRUPO, leitor)
        self.assertEqual(resultado["candidatos"], [])
        self.assertIn("consulta_indisponivel_operation_drafts", resultado["cobertura"]["motivos"])

    def test_chave_invalida_nao_consulta(self):
        leitor = LeitorSpy()
        with self.assertRaises(ValueError):
            modulo.consultar("agent:juan:telegram:group:sem-grupo", leitor)
        self.assertEqual(leitor.rotas, [])

    def test_target_ausente_mantem_compra_nao_comprovada(self):
        leitor = LeitorSpy({
            "operation_drafts": [draft(entidade_final_tipo="compras", entidade_final_id=ID_COMPRA_A)],
            "pending_actions": [], "compras": [],
        })
        resultado = modulo.consultar(GRUPO, leitor)
        self.assertEqual(resultado["candidatos"][0]["situacao"], "rascunho_localizado_compra_nao_comprovada")

    def test_status_realizado_sem_target_nao_faz_get_compras(self):
        leitor = LeitorSpy({"operation_drafts": [draft(status="realizado")], "pending_actions": [], "compras": [compra()]})
        resultado = modulo.consultar(GRUPO, leitor)
        self.assertEqual(resultado["candidatos"][0]["situacao"], "rascunho_localizado_compra_nao_comprovada")
        self.assertFalse(any(rota.startswith("compras?") for rota in leitor.rotas))

    def test_erro_pos_gravacao_com_target_mostra_compra_e_auditoria(self):
        leitor = LeitorSpy({
            "operation_drafts": [draft(pending_action_id=ID_PENDING)],
            "pending_actions": [pending(status="erro_pos_gravacao", resultado={"target_table": "compras", "target_record_id": ID_COMPRA_A})],
            "compras": [compra(ID_COMPRA_A)],
        })
        resultado = modulo.consultar(GRUPO, leitor)
        candidato = resultado["candidatos"][0]
        self.assertEqual(candidato["situacao"], "compra_localizada_por_vinculo_do_rascunho")
        self.assertIn("auditoria_pendente_de_conferencia", candidato["alertas"])
        self.assertEqual(candidato["compra"]["id"], ID_COMPRA_A)

    def test_duas_execucoes_sao_deterministicas_e_nao_mutam_fixtures(self):
        respostas = {"operation_drafts": [draft(entidade_final_tipo="compras", entidade_final_id=ID_COMPRA_A)], "pending_actions": [], "compras": [compra()]}
        antes = copy.deepcopy(respostas)
        primeiro = modulo.consultar(GRUPO, LeitorSpy(respostas))
        segundo = modulo.consultar(GRUPO, LeitorSpy(respostas))
        self.assertEqual(primeiro, segundo)
        self.assertEqual(respostas, antes)

    def test_ponte_leitura_usa_somente_get_read_env_sanitizado_e_status_http(self):
        chamadas = []

        def executar(argv, **kwargs):
            chamadas.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, stdout='[]\nHTTP_STATUS:200', stderr="")

        ponte = modulo.PonteLeitura(caminho=Path("/tmp/ponte-ficticia.py"), segundos=45, executar=executar)
        self.assertEqual(ponte("operation_drafts?select=id"), [])
        argv, kwargs = chamadas[0]
        self.assertIn("get_read", argv)
        self.assertNotIn("shell", argv)
        self.assertEqual(set(kwargs["env"]), {"PATH", "LANG", "PYTHONDONTWRITEBYTECODE"})
        self.assertNotIn("SECRET", json.dumps(kwargs["env"]).upper())

    def test_ponte_rejeita_status_http_ou_timeout_sem_lista_vazia(self):
        def resposta_ruim(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 0, stdout='[]\nHTTP_STATUS:500', stderr="")

        ponte = modulo.PonteLeitura(caminho=Path("/tmp/ponte-ficticia.py"), executar=resposta_ruim)
        with self.assertRaises(modulo.ConsultaIndisponivel):
            ponte("operation_drafts?select=id")

    def test_ponte_prazo_vencido_nao_executa(self):
        chamadas = []
        ponte = modulo.PonteLeitura(segundos=0, executar=lambda *args, **kwargs: chamadas.append(args))
        with self.assertRaises(modulo.ConsultaIndisponivel):
            ponte("operation_drafts?select=id")
        self.assertEqual(chamadas, [])

    def test_ponte_timeout_do_subprocesso_e_indisponibilidade(self):
        def expirar(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd=args[0], timeout=1)

        ponte = modulo.PonteLeitura(executar=expirar)
        with self.assertRaises(modulo.ConsultaIndisponivel):
            ponte("operation_drafts?select=id")

    def test_ponte_rejeita_401_500_corpo_parcial_e_codigo_nao_zero(self):
        respostas = [
            "[]\nHTTP_STATUS:401",
            "[]\nHTTP_STATUS:500",
            "[{\"id\":",
        ]
        for corpo in respostas:
            with self.subTest(corpo=corpo):
                def executar(*args, _corpo=corpo, **kwargs):
                    return subprocess.CompletedProcess(args[0], 0, stdout=_corpo, stderr="")
                ponte = modulo.PonteLeitura(executar=executar)
                with self.assertRaises(modulo.ConsultaIndisponivel):
                    ponte("operation_drafts?select=id")

        def retorno_falha(*args, **kwargs):
            return subprocess.CompletedProcess(args[0], 1, stdout="[]\nHTTP_STATUS:200", stderr="falha")
        with self.assertRaises(modulo.ConsultaIndisponivel):
            modulo.PonteLeitura(executar=retorno_falha)("operation_drafts?select=id")

    def test_ponte_recusa_rota_invalida(self):
        ponte = modulo.PonteLeitura(executar=lambda *args, **kwargs: self.fail("não deveria executar"))
        for rota in ("shell?cmd=ls", "https://exemplo.invalid", "outra_tabela?select=id"):
            with self.subTest(rota=rota), self.assertRaises(modulo.ConsultaIndisponivel):
                ponte(rota)

    def test_consulta_nao_executa_mutacoes_nem_carrega_ambiente_secreto(self):
        leitor = LeitorSpy({"operation_drafts": [], "pending_actions": [], "compras": []})
        with patch.dict(os.environ, {"SUPABASE_SERVICE_ROLE_KEY": "segredo-ficticio"}, clear=False):
            resultado = modulo.consultar(GRUPO, leitor)
        self.assertEqual(resultado["escritas"], 0)
        self.assertFalse(resultado["autoriza_escrita"])
        self.assertTrue(all("POST" not in rota and "PATCH" not in rota and "DELETE" not in rota for rota in leitor.rotas))

    def test_resultado_operation_draft_id_distinto_e_conflito_mesmo_sem_source(self):
        leitor = LeitorSpy({
            "operation_drafts": [draft(pending_action_id=ID_PENDING, entidade_final_tipo="compras", entidade_final_id=ID_COMPRA_A)],
            "pending_actions": [pending(resultado={"operation_draft_id": "66666666-6666-4666-8666-666666666666", "target_table": "compras", "target_record_id": ID_COMPRA_A})],
            "compras": [compra(ID_COMPRA_A)],
        })
        resultado = modulo.consultar(GRUPO, leitor)
        self.assertEqual(resultado["candidatos"][0]["situacao"], "vinculos_divergentes")
        self.assertFalse(any(rota.startswith("compras?") for rota in leitor.rotas))

    def test_pending_de_operation_drafts_com_entidade_distinta_e_conflito(self):
        leitor = LeitorSpy({
            "operation_drafts": [draft(pending_action_id=ID_PENDING)],
            "pending_actions": [pending(entidade_tipo="operation_drafts", entidade_id="77777777-7777-4777-8777-777777777777")],
            "compras": [],
        })
        resultado = modulo.consultar(GRUPO, leitor)
        self.assertEqual(resultado["candidatos"][0]["situacao"], "vinculos_divergentes")
        self.assertFalse(any(rota.startswith("compras?") for rota in leitor.rotas))

    def test_pending_por_id_de_outro_grupo_com_target_do_draft_nao_consulta_compras(self):
        leitor = LeitorSpy({
            "operation_drafts": [draft(pending_action_id=ID_PENDING, entidade_final_tipo="compras", entidade_final_id=ID_COMPRA_A)],
            "pending_actions": [pending(origem_conversa_id="-700002", conversa_id="-700002", contexto_canonico="telegram:grupo:-700002")],
            "compras": [compra(ID_COMPRA_A)],
        })
        resultado = modulo.consultar(GRUPO, leitor)
        self.assertEqual(resultado["candidatos"][0]["situacao"], "vinculos_divergentes")
        self.assertFalse(any(rota.startswith("compras?") for rota in leitor.rotas))

    def test_registro_com_grupo_sem_canal_nem_canonico_nao_e_aceito(self):
        sem_prova = draft(origem_canal=None, origem_conversa_id="-700001", contexto_canonico=None)
        leitor = LeitorSpy({"operation_drafts": [sem_prova], "pending_actions": [], "compras": []})
        resultado = modulo.consultar(GRUPO, leitor)
        self.assertEqual(resultado["candidatos"], [])
        self.assertIn("contexto_contraditorio_ou_nao_comprovado", resultado["cobertura"]["motivos"])

    def test_dois_drafts_com_mesmo_pending_id_sao_ambiguos_sem_get_compras(self):
        segundo = draft(id="88888888-8888-4888-8888-888888888888", pending_action_id=ID_PENDING,
                        entidade_final_tipo="compras", entidade_final_id=ID_COMPRA_B)
        primeiro = draft(pending_action_id=ID_PENDING, entidade_final_tipo="compras", entidade_final_id=ID_COMPRA_A)
        leitor = LeitorSpy({
            "operation_drafts": [primeiro, segundo],
            "pending_actions": [pending(resultado={"target_table": "compras", "target_record_id": ID_COMPRA_A})],
            "compras": [compra(ID_COMPRA_A), compra(ID_COMPRA_B)],
        })
        resultado = modulo.consultar(GRUPO, leitor)
        self.assertTrue(all(c["situacao"] == "vinculos_divergentes" for c in resultado["candidatos"]))
        self.assertFalse(any(rota.startswith("compras?") for rota in leitor.rotas))


if __name__ == "__main__":
    unittest.main()
