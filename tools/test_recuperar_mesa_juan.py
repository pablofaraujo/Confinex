#!/usr/bin/env python3
"""Testes sintéticos do adaptador de recuperação textual para Juan."""

from __future__ import annotations

import ast
import copy
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import recuperar_mesa_juan as modulo
from recuperar_textos_mesa import recuperar_textos_mesa


def telegram(**mudancas: object) -> dict[str, object]:
    base = {
        "senderId": "remetente-ficticio", "chatId": "chat-ficticio",
        "kind": "direct", "threadId": None, "accountId": "conta-ficticia",
        "routeSessionKey": "agent:juan:telegram:main",
        "mainSessionKey": "agent:juan:main",
    }
    base.update(mudancas)
    return base


def vinculo(**mudancas: object) -> dict[str, object]:
    base = {
        "alias": "Contato Fictício", "telegram": telegram(), "janela_dias": 7,
        "wey": {"db_path": "/privado/cache-ficticio.db", "chat_jid": "5511999999999@s.whatsapp.net"},
    }
    base.update(mudancas)
    return base


def documento(mensagens: list[dict], diagnostico: dict | None = None) -> dict:
    return {
        "schema_version": "mensagens-whatsapp-normalizadas-v1",
        "cobertura": {
            "estado": "parcial", "atestado": False,
            "intervalo_inicio": "2026-09-01T00:00:00Z",
            "intervalo_fim": "2026-09-08T00:00:00Z",
            "diagnostico_sanitizado": diagnostico or {
                "omitidas_sem_texto": 0, "omitidas_tamanho": 0,
                "omitidas_anexo_sem_texto": 0, "omitidas_por_estado": 0,
                "editadas_sem_historico": 0, "truncada_quantidade": False,
                "truncada_bytes": False,
            },
        },
        "mensagens": mensagens,
    }


def mensagem(indice: int, texto: str, autoria: str = "interlocutor") -> dict:
    return {
        "conversa_ref": "conv-ficticia", "mensagem_ref": f"m{indice:03}",
        "timestamp": f"2026-09-{1 + indice // 24:02}T{indice % 24:02}:00:00Z",
        "texto": texto, "origem_autoria": autoria,
    }


def recuperacao(mensagens: list[dict], diagnostico: dict | None = None) -> dict:
    return recuperar_textos_mesa(
        documento(mensagens, diagnostico), conversa_ref="conv-ficticia",
        inicio="2026-09-01T00:00:00Z", fim="2026-09-08T00:00:00Z",
        limite=1_000, contexto_adjacente=2,
    )


class RecuperarMesaJuanTestCase(unittest.TestCase):
    def test_gatilho_exige_alias_dominio_e_fonte_exatos(self) -> None:
        self.assertEqual(
            modulo.selecionar_alias_pedido(
                "Busque no WhatsApp o portfólio B3 do Contato Fictício",
                ["Contato Fictício"],
            ),
            "Contato Fictício",
        )
        for texto in (
            "Veja B3 do Contato Fictício", "Veja conversa do Contato Fictício",
        ):
            self.assertIsNone(modulo.selecionar_alias_pedido(texto, ["Contato Fictício"]))
        self.assertIsNone(modulo.selecionar_alias_pedido(
            "Veja conversa e B3 da Mesada", ["Mesa"]
        ))
        with self.assertRaisesRegex(modulo.RecuperacaoMesaInvalida, "contato_ambiguo"):
            modulo.selecionar_alias_pedido(
                "WhatsApp B3 Contato Fictício e Outra Mesa",
                ["Contato Fictício", "Outra Mesa"],
            )

    def test_identidade_compara_todos_campos_e_nao_o_texto(self) -> None:
        regras = modulo.validar_configuracao({
            "schema_version": modulo.SCHEMA_CONFIG, "vinculos": [vinculo()],
        })
        entrada = {
            "chave_sessao": "agent:juan:telegram:main",
            "texto": "Busque no WhatsApp o hedge do Contato Fictício",
            "telegram": telegram(),
        }
        self.assertIsNotNone(modulo.selecionar_vinculo(entrada, regras))
        for chave in modulo.CHAVES_TELEGRAM:
            alterada = copy.deepcopy(entrada)
            if chave == "kind":
                alterada["telegram"][chave] = "group"
            else:
                alterada["telegram"][chave] = "outro" if chave != "threadId" else "topico"
            try:
                resultado = modulo.selecionar_vinculo(alterada, regras)
            except modulo.RecuperacaoMesaInvalida:
                resultado = None
            self.assertIsNone(resultado)
        citado = copy.deepcopy(entrada)
        citado["telegram"]["senderId"] = "nao-autorizado"
        citado["texto"] += " remetente-ficticio chat-ficticio"
        self.assertIsNone(modulo.selecionar_vinculo(citado, regras))

    def test_selecao_reserva_recentes_mesmo_com_muitos_realces_antigos(self) -> None:
        mensagens = [mensagem(i, f"Fechamos B3 antigo {i}") for i in range(12)]
        mensagens += [mensagem(i, f"esclarecimento recente {i}", "titular") for i in range(12, 20)]
        resultado = modulo.selecionar_trechos(recuperacao(mensagens), limite_trechos=6)
        textos = [item["texto"] for item in resultado["trechos"]]
        self.assertTrue({"esclarecimento recente 17", "esclarecimento recente 18", "esclarecimento recente 19"} <= set(textos))
        self.assertEqual(textos, [item["texto"] for item in sorted(
            resultado["trechos"], key=lambda item: (item["timestamp"], item["texto_ref"])
        )])
        self.assertEqual(resultado["diagnostico"]["textos_lidos"], 20)
        self.assertEqual(resultado["diagnostico"]["trechos_transmitidos"], 6)
        self.assertEqual(resultado["diagnostico"]["omitidos_selecao"], 14)

    def test_sem_hit_entrega_recentes_e_bloco_longo_e_valido(self) -> None:
        sem_hit = modulo.selecionar_trechos(
            recuperacao([mensagem(i, f"texto comum {i}") for i in range(20)]),
            limite_trechos=4,
        )
        self.assertEqual([item["texto"] for item in sem_hit["trechos"]], [
            "texto comum 16", "texto comum 17", "texto comum 18", "texto comum 19",
        ])
        longo = recuperacao([mensagem(i, f"Fechamos B3 {i}") for i in range(30)])
        self.assertGreater(len(longo["blocos"][0]["texto_refs"]), 21)
        self.assertEqual(len(modulo.selecionar_trechos(longo)["trechos"]), 16)

    def test_string_grande_nao_e_truncada_e_limite_bytes_e_explicito(self) -> None:
        grande = mensagem(1, "x" * 1_801)
        normal = mensagem(2, "texto recente")
        resultado = modulo.selecionar_trechos(
            recuperacao([grande, normal]), limite_trechos=2, limite_bytes_total=20,
        )
        self.assertEqual([item["texto"] for item in resultado["trechos"]], ["texto recente"])
        self.assertEqual(resultado["diagnostico"]["omitidos_string"], 1)
        self.assertTrue(resultado["cobertura"]["corte_selecao"])
        self.assertTrue(all(
            len(valor.encode("utf-8")) <= 1_800
            for item in resultado["trechos"] for valor in item.values()
        ))

    def test_omissoes_de_estado_e_intervalo_chegam_ao_contexto(self) -> None:
        diag = {
            "omitidas_sem_texto": 0, "omitidas_tamanho": 0,
            "omitidas_anexo_sem_texto": 2, "omitidas_por_estado": 3,
            "editadas_sem_historico": 0, "truncada_quantidade": False,
            "truncada_bytes": False,
        }
        resultado = modulo.selecionar_trechos(recuperacao([mensagem(1, "ativa")], diag))
        self.assertEqual(resultado["cobertura"]["diagnostico_fonte"]["omitidas_por_estado"], 3)
        self.assertEqual(resultado["cobertura"]["diagnostico_fonte"]["omitidas_anexo_sem_texto"], 2)
        self.assertEqual(resultado["cobertura"]["fonte"]["intervalo_inicio"], "2026-09-01T00:00:00Z")
        self.assertEqual([item["texto"] for item in resultado["trechos"]], ["ativa"])

    def test_contrato_completo_rejeita_hash_ref_realce_cobertura_e_overflow(self) -> None:
        base = recuperacao([mensagem(1, "texto")])
        casos = []
        hash_ruim = copy.deepcopy(base); hash_ruim["textos"][0]["hash_conteudo"] = "0" * 64; casos.append(hash_ruim)
        ref_ruim = copy.deepcopy(base); ref_ruim["textos"][0]["mensagem_ref"] = {}; casos.append(ref_ruim)
        realce_ruim = copy.deepcopy(base); realce_ruim["textos"][0]["realces"] = [{"termo": []}]; casos.append(realce_ruim)
        cobertura_ruim = copy.deepcopy(base); cobertura_ruim["cobertura"] = []; casos.append(cobertura_ruim)
        for caso in casos:
            with self.assertRaises(modulo.RecuperacaoMesaInvalida):
                modulo.selecionar_trechos(caso)
        muitos = copy.deepcopy(base)
        muitos["textos"] = muitos["textos"] * 1_001
        with self.assertRaisesRegex(modulo.RecuperacaoMesaInvalida, "recuperacao_invalida"):
            modulo.selecionar_trechos(muitos)

    def test_orquestracao_injetada_nao_acessa_cache_sem_identidade_ou_gatilho(self) -> None:
        regras = modulo.validar_configuracao({
            "schema_version": modulo.SCHEMA_CONFIG, "vinculos": [vinculo()],
        })
        base = {"chave_sessao": "agent:juan:telegram:main", "telegram": telegram()}
        for texto in ("Bom dia", "WhatsApp B3 de outro contato"):
            resultado = modulo.recuperar_mesa_juan(
                {**base, "texto": texto}, regras,
                ler_cache=lambda *a, **k: self.fail("cache não deveria ser acessado"),
            )
            self.assertEqual(resultado["status"], "nao_aplicavel")

    def test_orquestracao_passa_janela_e_manifesto_so_em_ram(self) -> None:
        regras = modulo.validar_configuracao({
            "schema_version": modulo.SCHEMA_CONFIG, "vinculos": [vinculo()],
        })
        chamadas: list[dict] = []
        doc = documento([mensagem(1, "resposta da mesa")])
        def ler(manifesto: dict, **opcoes: object) -> dict:
            chamadas.append({"manifesto": manifesto, **opcoes})
            return {"documento": doc, "conversa_ref": "conv-ficticia", "metadados": {}}
        entrada = {
            "chave_sessao": "agent:juan:telegram:main",
            "texto": "Consulte a conversa WhatsApp do hedge do Contato Fictício",
            "telegram": telegram(),
        }
        resultado = modulo.recuperar_mesa_juan(
            entrada, regras, agora=lambda: datetime(2026, 9, 8, tzinfo=timezone.utc),
            ler_cache=ler,
        )
        self.assertEqual(resultado["status"], "textos_para_revisao")
        self.assertEqual(resultado["trechos"][0]["texto"], "resposta da mesa")
        self.assertEqual(chamadas[0]["inicio"], "2026-09-01T00:00:00Z")
        self.assertEqual(chamadas[0]["fim"], "2026-09-08T00:00:00Z")
        self.assertEqual(chamadas[0]["manifesto"]["schema_version"], "manifesto-cache-wey-b3-v1")

    def test_config_privada_exige_0600_owner_regular_fora_git_e_sem_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as pasta:
            raiz = Path(pasta).resolve()
            caminho = raiz / "allowlist.json"
            caminho.write_text(json.dumps({
                "schema_version": modulo.SCHEMA_CONFIG, "vinculos": [vinculo()],
            }), encoding="utf-8")
            caminho.chmod(0o600)
            self.assertEqual(len(modulo.ler_configuracao_privada(caminho)), 1)
            caminho.chmod(0o640)
            with self.assertRaisesRegex(modulo.RecuperacaoMesaInvalida, "config_privada_invalida"):
                modulo.ler_configuracao_privada(caminho)
            caminho.chmod(0o600)
            atalho = raiz / "atalho.json"; atalho.symlink_to(caminho)
            with self.assertRaises(modulo.RecuperacaoMesaInvalida):
                modulo.ler_configuracao_privada(atalho)
            (raiz / ".git").mkdir()
            with self.assertRaises(modulo.RecuperacaoMesaInvalida):
                modulo.ler_configuracao_privada(caminho)

    def test_saida_estavel_e_imports_sem_rede_subprocesso_ou_escrita(self) -> None:
        rec = recuperacao([mensagem(1, "texto")])
        primeiro = modulo.selecionar_trechos(rec)
        segundo = modulo.selecionar_trechos(copy.deepcopy(rec))
        self.assertEqual(primeiro, segundo)
        fonte = Path(modulo.__file__).read_text(encoding="utf-8")
        arvore = ast.parse(fonte)
        importados = {
            nome.name.split(".")[0]
            for no in ast.walk(arvore) if isinstance(no, (ast.Import, ast.ImportFrom))
            for nome in (no.names if isinstance(no, ast.Import) else [ast.alias(name=no.module or "")])
        }
        self.assertTrue(importados.isdisjoint({"socket", "urllib", "http", "requests", "subprocess"}))
        self.assertNotIn("open(", fonte)
        self.assertNotIn("write_text", fonte)

    def test_cli_sanitiza_excecao_sem_caminho_identidade_ou_texto(self) -> None:
        entrada = b'{"texto":"segredo privado"}'
        falso_stdin = type("Entrada", (), {"buffer": io.BytesIO(entrada)})()
        saida = io.StringIO()
        with patch.object(sys, "stdin", falso_stdin), patch.object(sys, "stdout", saida), \
             patch.object(modulo, "ler_configuracao_privada", side_effect=RuntimeError("/privado senderId segredo")):
            self.assertEqual(modulo.main(["--entrada-stdin", "--config", "/privado/config.json"]), 0)
        serializado = saida.getvalue()
        self.assertNotIn("segredo", serializado)
        self.assertNotIn("/privado", serializado)
        self.assertEqual(json.loads(serializado)["status"], "recuperacao_indisponivel")


if __name__ == "__main__":
    unittest.main()
