"""Testes offline do worker de fonte 'outro' — nenhum teste toca rede/socket.

A prova de ponta a ponta (assumir → montar → publicar aceito pelo schema
pós-0002) fica em tools/test_migracao_postgres.py, que o CI roda com
--obrigatorio.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools import investigacoes_revisao as biblioteca
from tools import worker_fonte_outro as worker

SEGREDO_TESTE = bytes.fromhex("d" * 64)  # vetor público; jamais credencial


def tarefa_base(**extra) -> dict:
    contrato = biblioteca.contrato_consulta({
        "tipo": "busca_operacional",
        "pergunta": "evidencias documentais para a revisao NEG-26-903",
        "termos": ["NEG-26-903"],
        "campos": ["data", "negocio", "quantidade", "valor_total"],
        "limite": 10,
        "cobertura_esperada": "contexto_completo",
    })
    tarefa = {
        "id": "9a000000-0000-4000-8000-0000000000aa",
        "investigacao_id": "9a000000-0000-4000-8000-0000000000bb",
        "adaptador": "outro",
        "adaptador_version": "v1",
        "lease_token": "9a000000-0000-4000-8000-0000000000cc",
        "lease_chave_id": "key_teste",
        "fencing_token": 3,
        **contrato,
    }
    tarefa.update(extra)
    return tarefa


def snapshot_base() -> dict:
    return {
        "modo": "somente_leitura",
        "gerado_em": "2026-09-01T12:00:00+00:00",
        "tabelas": {"negocios_candidatos": [
            {"codigo_fonte": "NEG-26-903", "chave_rastreio": "rastreio-903",
             "nome": "Fornecedor Sintetico", "data_base": "2026-08-20",
             "quantidade": 12, "valor_total": 123456.78},
            {"codigo_fonte": "NEG-26-777", "chave_rastreio": "rastreio-777",
             "nome": "Outro Fornecedor", "data_base": "2026-08-21",
             "quantidade": 5, "valor_total": 1000},
        ]},
    }


def leitura_base(snapshot: dict | None = None) -> dict:
    snap = snapshot if snapshot is not None else snapshot_base()
    return {"ok": True, "snapshot": snap, "hash": "ab" * 32}


class SnapshotTest(unittest.TestCase):
    def test_carregar_snapshot_ausente_e_ilegivel(self) -> None:
        faltando = worker.carregar_snapshot("/caminho/que/nao/existe.json")
        self.assertEqual(faltando, {"ok": False,
                                    "erro_codigo": "snapshot_indisponivel"})
        with tempfile.NamedTemporaryFile("w", suffix=".json") as arquivo:
            arquivo.write("{isto nao e json")
            arquivo.flush()
            quebrado = worker.carregar_snapshot(arquivo.name)
        self.assertEqual(quebrado["erro_codigo"], "snapshot_ilegivel")
        with tempfile.NamedTemporaryFile("w", suffix=".json") as arquivo:
            json.dump({"gerado_em": "2026-09-01T12:00:00+00:00",
                       "tabelas": {}}, arquivo)
            arquivo.flush()
            sem_tabela = worker.carregar_snapshot(arquivo.name)
        self.assertEqual(sem_tabela["erro_codigo"], "snapshot_sem_tabela_fonte")

    def test_carregar_snapshot_valido_tem_hash_do_arquivo(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".json",
                                         delete=False) as arquivo:
            json.dump(snapshot_base(), arquivo)
        leitura = worker.carregar_snapshot(arquivo.name)
        Path(arquivo.name).unlink()
        self.assertTrue(leitura["ok"])
        self.assertEqual(len(leitura["hash"]), 64)

    def test_idade_do_snapshot(self) -> None:
        snap = snapshot_base()
        self.assertTrue(worker.snapshot_dentro_da_idade(
            snap, "2026-09-01T13:00:00+00:00"))
        self.assertFalse(worker.snapshot_dentro_da_idade(
            snap, "2026-09-03T13:00:00+00:00"))
        # snapshot "do futuro" também é rejeitado
        self.assertFalse(worker.snapshot_dentro_da_idade(
            snap, "2026-09-01T11:00:00+00:00"))


class BuscaTest(unittest.TestCase):
    def test_correspondencia_exata_por_codigo(self) -> None:
        spec = biblioteca.normalizar_consulta({
            "termos": ["NEG-26-903"],
            "campos": ["data", "negocio", "quantidade", "valor_total"],
        })
        candidatos = worker.buscar_candidatos(spec, snapshot_base())
        self.assertEqual(len(candidatos), 4)  # um por campo, mesma linha
        self.assertEqual({c["chave_natural"]["codigo_fonte"]
                          for c in candidatos}, {"NEG-26-903"})
        self.assertEqual({c["tipo_correspondencia"] for c in candidatos},
                         {"nome"})

    def test_sem_termos_nao_busca(self) -> None:
        spec = biblioteca.normalizar_consulta({
            "termos": [], "campos": ["data"],
        })
        self.assertEqual(worker.buscar_candidatos(spec, snapshot_base()), [])

    def test_campo_sem_coluna_e_ignorado(self) -> None:
        spec = biblioteca.normalizar_consulta({
            "termos": ["NEG-26-903"], "campos": ["decisao_humana"],
        })
        self.assertEqual(worker.buscar_candidatos(spec, snapshot_base()), [])

    def test_politica_da_consulta(self) -> None:
        self.assertEqual(worker.politica_da_consulta(
            ["data", "negocio", "quantidade", "valor_total"]), "compra")
        self.assertEqual(worker.politica_da_consulta(["campo_desconhecido"]),
                         "revisao")


class MontagemTest(unittest.TestCase):
    def test_pista_agrupada_em_uma_evidencia(self) -> None:
        resultado = worker.montar_resultado(tarefa_base(), leitura_base())
        self.assertEqual(resultado["estado_cobertura"], "completa")
        self.assertEqual(resultado["estado_resultado"],
                         "evidencia_insuficiente")
        evidencias = resultado["bundle"]["evidencias"]
        self.assertEqual(len(evidencias), 1)
        self.assertEqual(
            set(evidencias[0]["fatos_normalizados"]),
            {"data", "negocio", "quantidade", "valor_total"},
        )
        self.assertEqual(resultado["cobertura"]["registros_confirmados"], 1)
        # A fonte nunca declara alternativas, pendências ou ligações.
        self.assertEqual(resultado["bundle"]["alternativas"], [])
        self.assertEqual(resultado["bundle"]["pendencias"], [])
        self.assertEqual(resultado["bundle"]["ligacoes"], [])

    def test_montagem_deterministica(self) -> None:
        um = worker.montar_resultado(tarefa_base(), leitura_base())
        dois = worker.montar_resultado(tarefa_base(), leitura_base())
        self.assertEqual(json.dumps(um, sort_keys=True, default=str),
                         json.dumps(dois, sort_keys=True, default=str))

    def test_busca_vazia_com_snapshot_integro(self) -> None:
        snap = snapshot_base()
        snap["tabelas"]["negocios_candidatos"] = []
        resultado = worker.montar_resultado(tarefa_base(), leitura_base(snap))
        self.assertEqual(resultado["estado_cobertura"], "vazio_com_cobertura")
        self.assertEqual(resultado["bundle"]["evidencias"], [])
        self.assertEqual(resultado["cobertura"]["registros_confirmados"], 0)

    def test_snapshot_indisponivel_publica_falha_honesta(self) -> None:
        resultado = worker.montar_resultado(
            tarefa_base(), {"ok": False, "erro_codigo": "snapshot_indisponivel"}
        )
        self.assertEqual(resultado["estado_cobertura"], "indisponivel")
        self.assertEqual(resultado["estado_resultado"], "cobertura_incompleta")
        self.assertEqual(resultado["erro_codigo"], "snapshot_indisponivel")
        self.assertEqual(resultado["cobertura"]["artefato_cobertura_tipo"],
                         "erro_pre_resposta")

    def test_snapshot_velho_e_falha(self) -> None:
        resultado = worker.montar_resultado(
            tarefa_base(), leitura_base(),
            agora_iso="2026-09-03T12:00:01+00:00",
        )
        self.assertEqual(resultado["erro_codigo"],
                         "snapshot_fora_da_idade_maxima")

    def test_tarefa_de_outro_adaptador_e_recusada(self) -> None:
        with self.assertRaises(ValueError):
            worker.montar_resultado(tarefa_base(adaptador="wey"),
                                    leitura_base())

    def test_consulta_adulterada_aborta(self) -> None:
        tarefa = tarefa_base()
        tarefa["consulta_hash"] = "0" * 64  # linha durável não bate
        with self.assertRaises(ValueError):
            worker.montar_resultado(tarefa, leitura_base())


class PublicacaoTest(unittest.TestCase):
    def test_pedido_assinado_e_completo(self) -> None:
        tarefa = tarefa_base()
        resultado = worker.montar_resultado(tarefa, leitura_base())
        pedido = worker.montar_pedido_publicacao(
            tarefa, resultado, segredo=SEGREDO_TESTE,
            chave_id="key_teste", artefato_hash="c" * 64,
        )
        self.assertEqual(pedido["op"], "publicar")
        atestado = pedido["p_atestado_cobertura"]
        self.assertEqual(len(atestado["hmac"]), 64)
        self.assertEqual(atestado["chave_id"], "key_teste")
        self.assertEqual(atestado["fencing_token"], "3")
        self.assertEqual(atestado["registros_confirmados"], 1)

    def test_falha_de_cobertura_tambem_e_atestavel(self) -> None:
        tarefa = tarefa_base()
        resultado = worker.montar_resultado(
            tarefa, {"ok": False, "erro_codigo": "snapshot_indisponivel"}
        )
        pedido = worker.montar_pedido_publicacao(
            tarefa, resultado, segredo=SEGREDO_TESTE,
            chave_id="key_teste", artefato_hash="c" * 64,
        )
        self.assertEqual(pedido["p_estado_cobertura"], "indisponivel")
        self.assertEqual(pedido["p_bundle"]["evidencias"], [])

    def test_resumo_para_terminal_nao_vaza_fatos_nem_hmac(self) -> None:
        resultado = worker.montar_resultado(tarefa_base(), leitura_base())
        resumo = json.dumps(worker.resumo_para_terminal(resultado),
                            ensure_ascii=False)
        self.assertNotIn("123456.78", resumo)
        self.assertNotIn("hmac", resumo)
        self.assertIn('"evidencias": 1', resumo)

    def test_ler_segredo(self) -> None:
        with tempfile.NamedTemporaryFile("w", delete=False) as arquivo:
            arquivo.write("d" * 64 + "\n")
        segredo = worker.ler_segredo(arquivo.name)
        Path(arquivo.name).unlink()
        self.assertEqual(segredo, SEGREDO_TESTE)
        with tempfile.NamedTemporaryFile("w", delete=False) as curto:
            curto.write("d" * 16)
        with self.assertRaises(ValueError):
            worker.ler_segredo(curto.name)
        Path(curto.name).unlink()
        with tempfile.NamedTemporaryFile("w", delete=False) as naohex:
            naohex.write("z" * 64)
        with self.assertRaises(ValueError):
            worker.ler_segredo(naohex.name)
        Path(naohex.name).unlink()


if __name__ == "__main__":
    unittest.main()
