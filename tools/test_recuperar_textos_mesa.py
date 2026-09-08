#!/usr/bin/env python3
"""Testes puros da recuperação textual da mesa."""

from __future__ import annotations

import copy
import hashlib
import inspect
import unittest

import recuperar_textos_mesa as modulo


def mensagem(ref: str | None, timestamp: str, texto: str, autoria: str = "nao_informada") -> dict:
    return {
        "conversa_ref": "conv-opaca",
        "mensagem_ref": ref,
        "timestamp": timestamp,
        "texto": texto,
        "hash_conteudo": hashlib.sha256(texto.encode("utf-8")).hexdigest(),
        "origem_autoria": autoria,
    }


def documento(mensagens: list[dict], estado: str = "parcial") -> dict:
    return {
        "schema_version": "mensagens-whatsapp-normalizadas-v1",
        "cobertura": {
            "estado": estado,
            "atestado": False,
            "diagnostico_sanitizado": {
                "omitidas_sem_texto": 0,
                "omitidas_tamanho": 0,
                "omitidas_anexo_sem_texto": 2,
                "omitidas_por_estado": 0,
                "editadas_sem_historico": 0,
                "truncada_quantidade": False,
                "truncada_bytes": False,
            },
        },
        "mensagens": mensagens,
    }


class RecuperarTextosMesaTestCase(unittest.TestCase):
    inicio = "2026-09-01T00:00:00Z"
    fim = "2026-09-08T02:00:00Z"

    def recuperar(self, dados: dict, **opcoes: object) -> dict:
        return modulo.recuperar_textos_mesa(
            dados, conversa_ref="conv-opaca", inicio=self.inicio, fim=self.fim,
            limite=opcoes.pop("limite", 100), **opcoes,
        )

    def test_preserva_todo_corpus_sem_hit_em_cronologia_e_autoria(self) -> None:
        dados = documento([
            mensagem("m2", "2026-09-04T12:34:56-00:00", "segunda", "titular"),
            mensagem("m1", "2026-09-04T10:00:00-03:00", "primeira", "interlocutor"),
        ])
        resultado = self.recuperar(dados)
        self.assertEqual([item["texto"] for item in resultado["textos"]], ["segunda", "primeira"])
        self.assertEqual([item["origem_autoria"] for item in resultado["textos"]], ["titular", "interlocutor"])
        self.assertEqual(resultado["blocos"], [])
        self.assertEqual(resultado["estado"], "texto_para_revisao")
        self.assertFalse(resultado["autoriza_escrita"])

    def test_conversa_e_janela_sao_exatas_e_inclusivas(self) -> None:
        dados = documento([
            mensagem("i", self.inicio, "início"), mensagem("f", self.fim, "fim"),
            mensagem("fora", "2026-08-31T23:59:59Z", "fora"),
        ])
        self.assertEqual([i["texto"] for i in self.recuperar(dados)["textos"]], ["início", "fim"])
        errado = copy.deepcopy(dados)
        errado["mensagens"][0]["conversa_ref"] = "outra"
        with self.assertRaisesRegex(ValueError, "fora_da_conversa"):
            self.recuperar(errado)

    def test_realces_informais_nao_classificam_nem_vinculam(self) -> None:
        resultado = self.recuperar(documento([
            mensagem("m1", "2026-09-08T01:00:00Z", "Fechamos e rolamos a mesa ontem")
        ]))
        self.assertEqual({r["termo"] for r in resultado["textos"][0]["realces"]}, {"fechamos", "rolamos"})
        serializado = repr(resultado)
        self.assertNotIn("acao_confirmada", serializado)
        self.assertNotIn("posicao_ref", serializado)
        self.assertFalse(resultado["atualizacao_operacional"])

    def test_literal_fornecido_realca_sem_excluir_mensagens(self) -> None:
        dados = documento([
            mensagem("m1", "2026-09-08T01:00:00Z", "Conversei com a mesa"),
            mensagem("m2", "2026-09-08T01:01:00Z", "Sem palavra especial"),
        ])
        resultado = self.recuperar(dados, termos=("mesa",))
        self.assertEqual(len(resultado["textos"]), 2)
        self.assertEqual(resultado["textos"][0]["realces"], [{"termo": "mesa", "origem": "fornecido"}])

    def test_vizinhos_blocos_mesclam_intervalos_sem_duplicar_textos(self) -> None:
        dados = documento([
            mensagem(f"m{i}", f"2026-09-08T01:0{i}:00Z", "zerei" if i in {2, 4} else f"contexto {i}")
            for i in range(7)
        ])
        resultado = self.recuperar(dados, contexto_adjacente=2)
        self.assertEqual(len(resultado["blocos"]), 1)
        self.assertEqual(len(resultado["blocos"][0]["texto_refs"]), 7)
        self.assertEqual(len(set(resultado["blocos"][0]["texto_refs"])), 7)
        self.assertEqual(len(resultado["textos"]), 7)

    def test_limite_corta_n_mais_um_sem_elevar_cobertura(self) -> None:
        dados = documento([
            mensagem(f"m{i}", f"2026-09-08T01:0{i}:00Z", f"texto {i}") for i in range(3)
        ], estado="completa")
        resultado = self.recuperar(dados, limite=2)
        self.assertEqual(len(resultado["textos"]), 2)
        self.assertEqual(resultado["diagnostico"]["cortadas_por_limite"], 1)
        self.assertTrue(resultado["cobertura"]["corte_por_limite"])
        self.assertEqual(resultado["cobertura"]["estado"], "parcial")
        self.assertNotIn("texto 2", repr(resultado))

    def test_omissao_de_audio_e_herdada_com_contagens_sem_transcricao(self) -> None:
        resultado = self.recuperar(documento([
            mensagem("m1", "2026-09-08T01:00:00Z", "texto humano")
        ]))
        self.assertEqual(resultado["diagnostico"]["fonte"]["omitidas_anexo_sem_texto"], 2)
        self.assertEqual(resultado["cobertura"]["estado"], "parcial")
        self.assertFalse(resultado["modelo_acionado"])

    def test_cobertura_preserva_somente_metadados_fechados(self) -> None:
        dados = documento([mensagem("m1", "2026-09-08T01:00:00Z", "texto")])
        dados["cobertura"].update({
            "identidade_pendente": True, "captura_ativa_confirmada": False,
            "truncada": True, "motivo": "limite_de_mensagens",
            "detalhe_livre": "nao copiar",
        })
        cobertura = self.recuperar(dados)["cobertura"]
        self.assertTrue(cobertura["identidade_pendente"])
        self.assertTrue(cobertura["truncada"])
        self.assertEqual(cobertura["motivo"], "limite_de_mensagens")
        self.assertNotIn("detalhe_livre", cobertura)
        self.assertEqual(cobertura["diagnostico_sanitizado"]["omitidas_anexo_sem_texto"], 2)

    def test_dedup_exato_e_conflito_de_autoria(self) -> None:
        item = mensagem("m1", "2026-09-08T01:00:00Z", "texto", "titular")
        self.assertEqual(len(self.recuperar(documento([item, copy.deepcopy(item)]))["textos"]), 1)
        conflitante = copy.deepcopy(item)
        conflitante["origem_autoria"] = "interlocutor"
        with self.assertRaisesRegex(ValueError, "autoria_conflitante"):
            self.recuperar(documento([item, conflitante]))

    def test_ordem_empate_e_hash_sao_deterministicos(self) -> None:
        a = mensagem("a", "2026-09-08T01:00:00Z", "A")
        b = mensagem("b", "2026-09-08T01:00:00Z", "B")
        primeiro = self.recuperar(documento([b, a]))
        segundo = self.recuperar(documento([a, b]))
        self.assertEqual(primeiro, segundo)
        self.assertEqual([i["mensagem_ref"] for i in primeiro["textos"]], ["a", "b"])

    def test_hash_muda_com_conteudo_autoria_intervalo_e_diagnostico(self) -> None:
        base = documento([mensagem("m", "2026-09-08T01:00:00Z", "texto")])
        hash_base = self.recuperar(base)["recuperacao_hash"]
        variacoes = []
        autoria = copy.deepcopy(base); autoria["mensagens"][0]["origem_autoria"] = "titular"; variacoes.append(self.recuperar(autoria))
        conteudo = documento([mensagem("m", "2026-09-08T01:00:00Z", "outro")]); variacoes.append(self.recuperar(conteudo))
        diagnostico = copy.deepcopy(base); diagnostico["cobertura"]["diagnostico_sanitizado"]["omitidas_sem_texto"] = 1; variacoes.append(self.recuperar(diagnostico))
        variacoes.append(modulo.recuperar_textos_mesa(base, conversa_ref="conv-opaca", inicio=self.inicio, fim="2026-09-08T03:00:00Z", limite=100))
        self.assertTrue(all(item["recuperacao_hash"] != hash_base for item in variacoes))

    def test_shape_overflow_fuso_e_tipos_invalidos_falham_fechado(self) -> None:
        base = documento([mensagem("m", "2026-09-08T01:00:00Z", "texto")])
        for opcoes in ({"limite": True}, {"contexto_adjacente": -1}, {"termos": (x for x in ("a",))}):
            with self.subTest(opcoes=repr(opcoes)), self.assertRaises(ValueError):
                self.recuperar(base, **opcoes)
        with self.assertRaises(ValueError):
            modulo.recuperar_textos_mesa(base, conversa_ref="conv-opaca", inicio="2026-01-01T00:00:00Z", fim=self.fim, limite=10)
        hash_lista = copy.deepcopy(base); hash_lista["mensagens"][0]["hash_conteudo"] = []
        with self.assertRaisesRegex(ValueError, "hash_invalido"):
            self.recuperar(hash_lista)
        autoria = copy.deepcopy(base); autoria["mensagens"][0]["origem_autoria"] = {}
        with self.assertRaisesRegex(ValueError, "autoria_invalida"):
            self.recuperar(autoria)

    def test_fonte_indisponivel_inconsistente_e_completa_nao_atestada(self) -> None:
        base = documento([mensagem("m", "2026-09-08T01:00:00Z", "texto")], estado="indisponivel")
        with self.assertRaisesRegex(ValueError, "fonte_indisponivel"):
            self.recuperar(base)
        completa = documento([mensagem("m", "2026-09-08T01:00:00Z", "texto")], estado="completa")
        self.assertEqual(self.recuperar(completa)["cobertura"]["estado"], "parcial")

    def test_helper_nao_importa_rede_subprocesso_ou_escrita(self) -> None:
        fonte = inspect.getsource(modulo)
        for proibido in ("import socket", "import subprocess", "requests", "urlopen", "open(", "sqlite3"):
            self.assertNotIn(proibido, fonte)


if __name__ == "__main__":
    unittest.main()
