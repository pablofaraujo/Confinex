#!/usr/bin/env python3
"""Testes sintéticos do coletor B3 somente leitura."""

from __future__ import annotations

import copy
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import coletar_previa_b3 as modulo


POS_ID = "11111111-1111-4111-8111-111111111111"
POS_ID_2 = "22222222-2222-4222-8222-222222222222"
ALOC_ID = "33333333-3333-4333-8333-333333333333"
OPERACAO_ID = "44444444-4444-4444-8444-444444444444"
ALOCACAO_CAMPOS_REAIS = (
    "id", "posicao_id", "operacao_id", "contratos_qtd",
    "resultado_creditado", "created_at",
)


def posicao(identificador=POS_ID, **alteracoes):
    base = {campo: None for campo in modulo.CAMPOS_TABELAS["posicoes_hedge"]}
    base.update({
        "id": identificador,
        "referencia_bolsa": "B3-26-001",
        "contrato": "BGIV26",
        "direcao": "vendido",
        "categoria": "hedge",
        "contratos_qtd": 2,
        "preco_entrada": 318.5,
        "data_entrada": "2026-09-01",
        "status": "aberta",
        "custo_corretagem": 12.5,
        "custo_finpec": 3.25,
        "termo": "bgp:identidade-gerenciada-a",
        "origem": "bgi-portfolio",
        "negocio_rateio": "CF-26-001 2 cts",
        "detalhes": {"tipo": "sintetico"},
        "obs": "fixture",
        "mes": "2026-10",
        "created_at": "2026-09-01T10:00:00Z",
        "updated_at": "2026-09-01T10:00:00Z",
    })
    base.update(alteracoes)
    return base


def alocacao(**alteracoes):
    # Fixture deliberadamente independente do contrato do coletor: representa
    # o schema real confirmado por probes GET limit=0.
    base = {
        "id": ALOC_ID,
        "posicao_id": POS_ID,
        "operacao_id": OPERACAO_ID,
        "contratos_qtd": 2,
        "resultado_creditado": None,
        "created_at": "2026-09-01T10:00:00Z",
    }
    base.update(alteracoes)
    return base


class LeitorPaginado:
    def __init__(self, ciclos=None, erro=None):
        self.ciclos = ciclos or [
            {"posicoes_hedge": [posicao()], "alocacoes_hedge": [alocacao()]},
            {"posicoes_hedge": [posicao()], "alocacoes_hedge": [alocacao()]},
        ]
        self.erro = erro
        self.rotas = []
        self.chamadas_por_tabela = {"posicoes_hedge": 0, "alocacoes_hedge": 0}

    def __call__(self, rota):
        self.rotas.append(rota)
        if self.erro:
            raise self.erro
        partes = urlsplit(rota)
        tabela = partes.path
        parametros = parse_qs(partes.query)
        limite = int(parametros["limit"][0])
        offset = int(parametros["offset"][0])
        ciclo = min(self.chamadas_por_tabela[tabela], len(self.ciclos) - 1)
        self.chamadas_por_tabela[tabela] += 1
        return copy.deepcopy(self.ciclos[ciclo][tabela][offset:offset + limite])


class LeitorSchemaReal(LeitorPaginado):
    def __call__(self, rota):
        partes = urlsplit(rota)
        if partes.path == "alocacoes_hedge":
            selecionados = tuple(parse_qs(partes.query)["select"][0].split(","))
            if any(campo not in ALOCACAO_CAMPOS_REAIS for campo in selecionados):
                self.rotas.append(rota)
                raise modulo.ColetaIndisponivel("postgresql_42703_coluna_inexistente")
        return super().__call__(rota)


def origem():
    return {
        "schema_version": modulo.SCHEMA_ORIGEM,
        "canal": "telegram",
        "conversa_ref": "grupo-fixture",
        "mensagem_ref": "mensagem-fixture",
        "timestamp": "2026-09-07T10:00:00Z",
    }


class ColetarPreviaB3TestCase(unittest.TestCase):
    def agora(self):
        return datetime(2026, 9, 7, 12, tzinfo=timezone.utc)

    def test_regressao_schema_real_seis_campos_nao_pede_coluna_inexistente(self):
        self.assertEqual(set(alocacao()), set(ALOCACAO_CAMPOS_REAIS))
        resultado = modulo.coletar_snapshot(LeitorSchemaReal(), agora=self.agora)
        self.assertEqual(resultado["snapshot"]["cobertura"]["estado"], "completa")

    def test_snapshot_valido_compativel_com_planejador_e_identidades_separadas(self):
        gerenciada_a = posicao(termo="bgp:a")
        gerenciada_b = posicao(POS_ID_2, termo="bgp:b", referencia_bolsa="B3-26-002")
        ciclos = [
            {"posicoes_hedge": [gerenciada_a, gerenciada_b], "alocacoes_hedge": [alocacao()]},
            {"posicoes_hedge": [gerenciada_a, gerenciada_b], "alocacoes_hedge": [alocacao()]},
        ]
        resultado = modulo.coletar_previa_b3(LeitorPaginado(ciclos), origem(), agora=self.agora)
        snapshot = resultado["snapshot"]
        self.assertEqual(snapshot["schema_version"], "snapshot-b3-v1")
        self.assertEqual(snapshot["cobertura"]["estado"], "completa")
        self.assertEqual(len(snapshot["posicoes"]), 2)
        self.assertEqual(len({item["id_opaco"] for item in snapshot["posicoes"]}), 2)
        alocacoes = [aloc for item in snapshot["posicoes"] for aloc in item["alocacoes"]]
        self.assertEqual(alocacoes[0]["operacao_id_privado"], OPERACAO_ID)
        self.assertNotIn("updated_at", alocacoes[0])
        self.assertEqual(alocacoes[0]["posicao_ref"], next(
            item["id_opaco"] for item in snapshot["posicoes"] if item["alocacoes"]
        ))
        self.assertTrue(snapshot["cobertura"]["atestado"])
        posicao_saida = next(item for item in snapshot["posicoes"] if item["alocacoes"])
        campos_posicao_integrais = {
            "id_opaco", "referencia_bolsa", "contrato", "direcao", "categoria",
            "contratos_qtd", "preco_entrada", "data_entrada", "status",
            "preco_saida", "data_saida", "resultado_realizado",
            "custo_corretagem", "custo_finpec", "termo", "origem",
            "negocio_rateio", "detalhes", "mes", "rolada_para_ref", "obs",
            "observacao", "created_at", "updated_at", "alocacoes",
        }
        self.assertEqual(set(posicao_saida), campos_posicao_integrais)
        self.assertFalse(resultado["metadados"]["autoriza_escrita"])
        self.assertEqual(resultado["metadados"]["auditoria_snapshot"]["atomicidade"], "nao_garantida")

    def test_timeout_e_falha_nao_viram_snapshot_vazio_completo(self):
        for erro in (TimeoutError("fixture"), OSError("fixture"), modulo.ColetaIndisponivel("fixture")):
            with self.subTest(erro=type(erro).__name__):
                resultado = modulo.coletar_snapshot(LeitorPaginado(erro=erro), agora=self.agora)
                self.assertEqual(resultado["snapshot"]["cobertura"]["estado"], "indisponivel")
                self.assertEqual(resultado["snapshot"]["posicoes"], [])
                self.assertNotEqual(resultado["auditoria"]["estado"], "estavel_em_duas_amostras_no_recorte")

    def test_http_nao_200_e_recusado_sem_expor_corpo(self):
        def executar(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 0, stdout='{"segredo":"x"}\nHTTP_STATUS:500', stderr="")
        ponte = modulo.PonteLeitura(executar=executar)
        with self.assertRaisesRegex(modulo.ColetaIndisponivel, "consulta_nao_confirmada"):
            ponte(modulo.construir_rota("posicoes_hedge", 10, 0))

    def test_erro_seguro_de_58_bytes_classifica_ponte_sem_leitura_b3(self):
        erro_seguro = '{"error":"recurso_leitura_nao_permitido:alocacoes_hedge"}\n'
        self.assertEqual(len(erro_seguro.encode("utf-8")), 58)

        def executar(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 2, stdout=erro_seguro, stderr="texto livre")

        ponte = modulo.PonteLeitura(executar=executar)
        with patch("urllib.request.urlopen") as http_direto:
            with self.assertRaisesRegex(modulo.ColetaIndisponivel, "^ponte_sem_leitura_b3$"):
                ponte(modulo.construir_rota("alocacoes_hedge", 10, 0))
        http_direto.assert_not_called()

    def test_erro_nao_textual_vira_falha_sanitizada_sem_typeerror(self):
        for valor in ([], {}):
            with self.subTest(valor=valor):
                stdout = json.dumps({"error": valor}) + "\n"

                def executar(argv, **kwargs):
                    return subprocess.CompletedProcess(
                        argv, 2, stdout=stdout, stderr="texto livre nao deve aparecer"
                    )

                ponte = modulo.PonteLeitura(executar=executar)
                with self.assertRaisesRegex(
                    modulo.ColetaIndisponivel, "^consulta_nao_confirmada$"
                ) as capturado:
                    ponte(modulo.construir_rota("posicoes_hedge", 10, 0))
                self.assertNotIn("texto livre", str(capturado.exception))

    def test_paginacao_truncada_e_id_duplicado_falham_fechado(self):
        duas = [posicao(), posicao(POS_ID_2, referencia_bolsa="B3-26-002")]
        ciclos = [{"posicoes_hedge": duas, "alocacoes_hedge": []}] * 2
        truncado = modulo.coletar_snapshot(LeitorPaginado(ciclos), limite_pagina=1, max_paginas=2, agora=self.agora)
        self.assertEqual(truncado["snapshot"]["cobertura"]["estado"], "indisponivel")
        repetidas = [posicao(), posicao()]
        ciclos_dup = [{"posicoes_hedge": repetidas, "alocacoes_hedge": []}] * 2
        duplicado = modulo.coletar_snapshot(LeitorPaginado(ciclos_dup), limite_pagina=5, agora=self.agora)
        self.assertEqual(duplicado["auditoria"]["motivo"], "identificador_repetido")

    def test_rota_indesejada_url_injection_select_e_parametros_extras(self):
        permitida = modulo.construir_rota("posicoes_hedge", 10, 0)
        modulo.validar_rota_fixa(permitida)
        ataques = (
            "https://invalido.test/posicoes_hedge?select=id&order=id.asc&limit=1&offset=0",
            "compras?select=id&order=id.asc&limit=1&offset=0",
            permitida + "&apikey=segredo",
            permitida.replace("select=id%2C", "select=*%2C"),
            permitida + "#fragmento",
        )
        for ataque in ataques:
            with self.subTest(ataque=ataque), self.assertRaises(modulo.ColetaIndisponivel):
                modulo.validar_rota_fixa(ataque)

    def test_ponte_usa_so_get_read_e_nao_herda_credenciais(self):
        chamada = {}
        def executar(argv, **kwargs):
            chamada.update(argv=argv, kwargs=kwargs)
            return subprocess.CompletedProcess(argv, 0, stdout="[]\nHTTP_STATUS:200", stderr="")
        with patch.dict(os.environ, {
            "SUPABASE_SERVICE_KEY": "nao-herdar",
            "CONFINEX_DB_KEY": "nao-herdar",
            "OPENAI_API_KEY": "nao-herdar",
        }):
            modulo.PonteLeitura(executar=executar)(modulo.construir_rota("posicoes_hedge", 10, 0))
        self.assertEqual(chamada["argv"][-2], "get_read")
        self.assertNotIn("SUPABASE_SERVICE_KEY", chamada["kwargs"]["env"])
        self.assertNotIn("CONFINEX_DB_KEY", chamada["kwargs"]["env"])
        self.assertNotIn("OPENAI_API_KEY", chamada["kwargs"]["env"])
        self.assertNotIn("--executar", chamada["argv"])

    def test_alocacao_orfa_invalida_todo_snapshot(self):
        orfa = alocacao(posicao_id=POS_ID_2)
        ciclos = [{"posicoes_hedge": [posicao()], "alocacoes_hedge": [orfa]}] * 2
        resultado = modulo.coletar_snapshot(LeitorPaginado(ciclos), agora=self.agora)
        self.assertEqual(resultado["snapshot"]["cobertura"]["estado"], "indisponivel")
        self.assertEqual(resultado["auditoria"]["motivo"], "alocacao_orfa")

    def test_antes_depois_identico_e_alteracao_de_custo_ou_estado(self):
        igual = modulo.coletar_snapshot(LeitorPaginado(), agora=self.agora)
        self.assertEqual(igual["auditoria"]["estado"], "estavel_em_duas_amostras_no_recorte")
        for campo, valor in (("custo_finpec", 99), ("status", "encerrada"), ("detalhes", {"novo": True})):
            with self.subTest(campo=campo):
                ciclos = [
                    {"posicoes_hedge": [posicao()], "alocacoes_hedge": [alocacao()]},
                    {"posicoes_hedge": [posicao(**{campo: valor})], "alocacoes_hedge": [alocacao()]},
                ]
                alterado = modulo.coletar_snapshot(LeitorPaginado(ciclos), agora=self.agora)
                self.assertTrue(alterado["auditoria"]["alteracao_concorrente"])
                self.assertEqual(alterado["snapshot"]["cobertura"]["estado"], "indisponivel")

    def test_alteracao_em_cada_campo_real_de_alocacao_invalida_snapshot(self):
        mudancas = {
            "id": "55555555-5555-4555-8555-555555555555",
            "posicao_id": POS_ID_2,
            "operacao_id": "66666666-6666-4666-8666-666666666666",
            "contratos_qtd": 3,
            "resultado_creditado": 125.75,
            "created_at": "2026-09-02T10:00:00Z",
        }
        for campo, valor in mudancas.items():
            with self.subTest(campo=campo):
                ciclos = [
                    {"posicoes_hedge": [posicao()], "alocacoes_hedge": [alocacao()]},
                    {"posicoes_hedge": [posicao()], "alocacoes_hedge": [alocacao(**{campo: valor})]},
                ]
                resultado = modulo.coletar_snapshot(LeitorPaginado(ciclos), agora=self.agora)
                self.assertTrue(resultado["auditoria"]["alteracao_concorrente"])
                self.assertNotEqual(
                    resultado["auditoria"]["assinaturas_antes"]["alocacoes_hedge"],
                    resultado["auditoria"]["assinaturas_depois"]["alocacoes_hedge"],
                )
                self.assertEqual(resultado["snapshot"]["cobertura"]["estado"], "indisponivel")

    def test_schema_de_alocacao_ausente_extra_ou_malformado_falha_fechado(self):
        casos = []
        ausente = alocacao()
        ausente.pop("created_at")
        casos.append(ausente)
        extra = alocacao()
        extra["updated_at"] = "2026-09-01T10:00:00Z"
        casos.append(extra)
        casos.append(["nao-e-objeto"])
        for valor in casos:
            with self.subTest(valor=valor):
                ciclos = [{
                    "posicoes_hedge": [posicao()], "alocacoes_hedge": [valor]
                }] * 2
                resultado = modulo.coletar_snapshot(LeitorPaginado(ciclos), agora=self.agora)
                self.assertEqual(resultado["snapshot"]["cobertura"]["estado"], "indisponivel")
                self.assertFalse(resultado["auditoria"]["alteracao_concorrente"])

    def test_campo_ausente_ou_extra_na_resposta_nao_tem_fallback(self):
        incompleta = posicao()
        incompleta.pop("updated_at")
        ciclos = [{"posicoes_hedge": [incompleta], "alocacoes_hedge": []}] * 2
        resultado = modulo.coletar_snapshot(LeitorPaginado(ciclos), agora=self.agora)
        self.assertEqual(resultado["auditoria"]["motivo"], "resposta_fora_do_select_declarado")

    def test_mensagens_so_export_normalizado_conversa_exata_periodo_e_limite(self):
        dados = {
            "schema_version": modulo.SCHEMA_MENSAGENS,
            "cobertura": {
                "estado": "completa", "atestado": True,
                "intervalo_inicio": "2026-09-01T00:00:00Z",
                "intervalo_fim": "2026-09-30T23:59:59Z",
            },
            "mensagens": [
                {"conversa_ref": "c1", "mensagem_ref": "m1", "timestamp": "2026-09-02T00:00:00Z", "texto": "fixture"},
                {"conversa_ref": "c1", "mensagem_ref": "m2", "timestamp": "2026-09-03T00:00:00Z", "texto": "fixture 2"},
            ],
        }
        normalizadas = modulo.normalizar_mensagens(
            dados, conversa_ref="c1", inicio="2026-09-01T00:00:00Z", fim="2026-09-30T23:59:59Z", limite=1
        )
        self.assertEqual(normalizadas["cobertura"]["estado"], "parcial")
        self.assertFalse(normalizadas["cobertura"]["captura_ativa_confirmada"])
        self.assertEqual(len(normalizadas["mensagens"]), 1)
        adulterado = copy.deepcopy(dados)
        adulterado["mensagens"][0]["conversa_ref"] = "outra"
        with self.assertRaisesRegex(ValueError, "conversa_exata"):
            modulo.normalizar_mensagens(
                adulterado, conversa_ref="c1", inicio="2026-09-01T00:00:00Z", fim="2026-09-30T23:59:59Z"
            )

    def test_mensagens_preservam_correcoes_mesmo_id_e_ids_distintos(self):
        dados = {
            "schema_version": modulo.SCHEMA_MENSAGENS,
            "cobertura": {
                "estado": "completa", "atestado": True,
                "intervalo_inicio": "2026-09-01T00:00:00Z",
                "intervalo_fim": "2026-09-30T23:59:59Z",
            },
            "mensagens": [
                {"conversa_ref": "c1", "mensagem_ref": "m1", "timestamp": "2026-09-02T10:00:00-03:00", "texto": "Fechei  B3-26-001."},
                {"conversa_ref": "c1", "mensagem_ref": "m1", "timestamp": "2026-09-02T10:01:00-03:00", "texto": "Correção: rolei B3-26-001."},
                {"conversa_ref": "c1", "mensagem_ref": "m2", "timestamp": "2026-09-02T10:02:00-03:00", "texto": "Fechei  B3-26-001."},
            ],
        }
        resultado = modulo.normalizar_mensagens(
            dados, conversa_ref="c1", inicio="2026-09-01T00:00:00Z",
            fim="2026-09-30T23:59:59Z"
        )
        self.assertEqual(len(resultado["mensagens"]), 3)
        self.assertEqual(resultado["mensagens"][0]["texto"], "Fechei B3-26-001.")
        self.assertTrue(resultado["cobertura"]["atestado"])

    def test_mensagens_sem_id_iguais_em_datas_distintas_nao_sao_deduplicadas(self):
        dados = {
            "schema_version": modulo.SCHEMA_MENSAGENS,
            "cobertura": {
                "estado": "completa", "atestado": True,
                "intervalo_inicio": "2026-09-01T00:00:00Z",
                "intervalo_fim": "2026-09-30T23:59:59Z",
            },
            "mensagens": [
                {"conversa_ref": "c1", "timestamp": "2026-09-02T10:00:00Z", "texto": "Fechei B3-26-001."},
                {"conversa_ref": "c1", "timestamp": "2026-09-03T10:00:00Z", "texto": "Fechei B3-26-001."},
            ],
        }
        resultado = modulo.normalizar_mensagens(
            dados, conversa_ref="c1", inicio="2026-09-01T00:00:00Z",
            fim="2026-09-30T23:59:59Z"
        )
        self.assertEqual(len(resultado["mensagens"]), 2)
        self.assertEqual(
            [item["timestamp"] for item in resultado["mensagens"]],
            ["2026-09-02T10:00:00Z", "2026-09-03T10:00:00Z"],
        )
        self.assertEqual(resultado["cobertura"]["estado"], "parcial")
        self.assertTrue(resultado["cobertura"]["identidade_pendente"])
        self.assertEqual(
            resultado["cobertura"]["motivo"], "mensagem_sem_identidade_comprovada"
        )

    def test_timestamp_ausente_invalido_ou_intervalo_invertido_nao_atesta(self):
        dados = {
            "schema_version": modulo.SCHEMA_MENSAGENS,
            "cobertura": {
                "estado": "completa", "atestado": True,
                "intervalo_inicio": "2026-09-01T00:00:00Z",
                "intervalo_fim": "2026-09-30T23:59:59Z",
            },
            "mensagens": [{"conversa_ref": "c1", "texto": "fixture"}],
        }
        resultado = modulo.normalizar_mensagens(
            dados, conversa_ref="c1", inicio="2026-09-01T00:00:00Z",
            fim="2026-09-30T23:59:59Z"
        )
        self.assertEqual(resultado["cobertura"]["estado"], "parcial")
        self.assertFalse(resultado["cobertura"]["atestado"])
        with self.assertRaisesRegex(ValueError, "invertido"):
            modulo.normalizar_mensagens(
                dados, conversa_ref="c1", inicio="2026-10-01T00:00:00Z",
                fim="2026-09-01T00:00:00Z"
            )

    def test_integracao_offline_coletor_para_planejador(self):
        from planejar_atualizacao_b3 import gerar_plano

        mensagens = modulo.normalizar_mensagens({
            "schema_version": modulo.SCHEMA_MENSAGENS,
            "cobertura": {
                "estado": "completa", "atestado": True,
                "intervalo_inicio": "2026-09-01T00:00:00Z",
                "intervalo_fim": "2026-09-07T12:00:00Z",
            },
            "mensagens": [{
                "conversa_ref": "mesa-fixture", "mensagem_ref": "m1",
                "timestamp": "2026-09-07T10:00:00Z",
                "texto": "Fechei B3-26-001.",
            }],
        }, conversa_ref="mesa-fixture", inicio="2026-09-01T00:00:00Z",
           fim="2026-09-07T12:00:00Z")
        pacote = modulo.coletar_previa_b3(
            LeitorPaginado(), origem(), mensagens=mensagens, agora=self.agora
        )
        plano = gerar_plano(pacote["snapshot"], pacote["mensagens"], pacote["origem"])
        self.assertEqual(plano["schema_version"], "previa-atualizacao-b3-v1")
        self.assertEqual(plano["resumo"]["posicoes_com_pistas"], 1)
        self.assertEqual(plano["controles"]["escritas_operacionais"], 0)

    def test_integracao_cache_normalizacao_plano_preserva_correcao_e_diagnostico(self):
        from ler_cache_wey_b3 import SCHEMA_MANIFESTO, ler_cache_wey_b3
        from planejar_atualizacao_b3 import gerar_plano

        with tempfile.TemporaryDirectory() as pasta_texto:
            pasta = Path(pasta_texto).resolve()
            banco = pasta / "wey.db"
            conexao = sqlite3.connect(banco)
            conexao.executescript("""
                CREATE TABLE chats (jid TEXT PRIMARY KEY);
                CREATE TABLE messages (
                    rowid INTEGER PRIMARY KEY, chat_jid TEXT, msg_id TEXT, ts INTEGER,
                    text TEXT, display_text TEXT, media_caption TEXT, media_type TEXT,
                    revoked INTEGER, deleted_for_me INTEGER, deleted_at INTEGER,
                    payload_purged_at INTEGER, edited INTEGER, edited_ts INTEGER
                );
            """)
            jid = "5511999999999@s.whatsapp.net"
            conexao.execute("INSERT INTO chats(jid) VALUES (?)", (jid,))
            linhas = (
                (1, "corrigida", "B3-26-001 encerrada", 0, None),
                (2, "corrigida", "Correção: B3-26-001 foi rolada", 1, 1788250200),
                (3, "sem-ref", "Mensagem operacional sem referência", 0, None),
                (4, "revogada", "Encerrar B3-26-001", 0, None),
            )
            for rowid, msg_id, texto, editada, editada_em in linhas:
                conexao.execute(
                    "INSERT INTO messages(rowid,chat_jid,msg_id,ts,text,edited,edited_ts,revoked) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (rowid, jid, msg_id, 1788250000 + rowid, texto, editada, editada_em,
                     1 if msg_id == "revogada" else 0),
                )
            conexao.commit()
            conexao.close()
            banco.chmod(0o600)
            manifesto = pasta / "manifesto.json"
            manifesto.write_text(json.dumps({
                "schema_version": SCHEMA_MANIFESTO, "db_path": str(banco), "chat_jid": jid,
            }), encoding="utf-8")
            manifesto.chmod(0o600)
            leitura = ler_cache_wey_b3(
                manifesto, inicio="2026-09-01T00:00:00Z", fim="2026-09-06T23:59:59Z"
            )
            mensagens = modulo.normalizar_mensagens(
                leitura["documento"], conversa_ref=leitura["conversa_ref"],
                inicio="2026-09-01T00:00:00Z", fim="2026-09-06T23:59:59Z",
            )
            pacote = modulo.coletar_previa_b3(
                LeitorPaginado(), origem(), mensagens=mensagens, agora=self.agora
            )
            plano = gerar_plano(pacote["snapshot"], pacote["mensagens"], pacote["origem"])

        self.assertEqual(len(mensagens["mensagens"]), 3)
        refs_correcao = [m for m in mensagens["mensagens"] if m["mensagem_ref"] == mensagens["mensagens"][0]["mensagem_ref"]]
        self.assertEqual(len(refs_correcao), 2)
        self.assertGreaterEqual(len(plano["mensagens_sem_referencia"]), 1)
        detalhe = plano["cobertura"]["mensagens_whatsapp"]["detalhe_sanitizado"]
        self.assertIn("omitidas_por_estado=1", detalhe)
        self.assertIn("editadas_sem_historico=1", detalhe)
        self.assertNotIn("Encerrar B3-26-001", json.dumps(plano, ensure_ascii=False))

    def test_cli_fontes_mutuamente_exclusivas_e_origem_validada_primeiro(self):
        parser = modulo.construir_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args([
                "--origem-json", "origem.json", "--mensagens-json", "mensagens.json",
                "--cache-wey-manifesto", "manifesto.json",
            ])
        with patch.object(sys, "argv", ["coletar_previa_b3.py", "--origem-json", "origem.json"]), \
             patch.object(modulo, "_ler_json_limitado", return_value={}), \
             patch.object(modulo, "PonteLeitura", side_effect=AssertionError("ponte acessada")):
            self.assertEqual(modulo.main(), 2)

    def test_aba_nao_e_prometida_como_atomicidade(self):
        resultado = modulo.coletar_snapshot(LeitorPaginado(), agora=self.agora)
        self.assertEqual(resultado["snapshot"]["cobertura"]["estado"], "completa")
        self.assertEqual(resultado["snapshot"]["cobertura"]["atomicidade"], "nao_garantida")
        self.assertEqual(resultado["auditoria"]["atomicidade"], "nao_garantida")

    def test_hash_estavel_nao_depende_do_horario_da_coleta(self):
        primeiro = modulo.coletar_snapshot(
            LeitorPaginado(), agora=lambda: datetime(2026, 9, 7, 12, tzinfo=timezone.utc)
        )
        segundo = modulo.coletar_snapshot(
            LeitorPaginado(), agora=lambda: datetime(2026, 9, 8, 15, tzinfo=timezone.utc)
        )
        self.assertNotEqual(
            primeiro["snapshot"]["gerado_em"], segundo["snapshot"]["gerado_em"]
        )
        self.assertEqual(
            primeiro["auditoria"]["snapshot_hash"], segundo["auditoria"]["snapshot_hash"]
        )

    def test_conta_invocacoes_reais_inclusive_falha(self):
        leitor = LeitorPaginado(erro=TimeoutError("fixture"))
        resultado = modulo.coletar_snapshot(leitor, agora=self.agora)
        self.assertEqual(resultado["auditoria"]["consultas"], 1)

    def test_cli_nao_expoe_snapshot_privado_no_stdout(self):
        pacote = modulo.coletar_previa_b3(LeitorPaginado(), origem(), agora=self.agora)
        resumo = {
            "estado": pacote["snapshot"]["cobertura"]["estado"],
            "posicoes": len(pacote["snapshot"]["posicoes"]),
            "hash": pacote["metadados"]["auditoria_snapshot"]["snapshot_hash"],
        }
        serializado = json.dumps(resumo)
        self.assertNotIn("BGIV26", serializado)
        self.assertNotIn(OPERACAO_ID, serializado)


if __name__ == "__main__":
    unittest.main()
