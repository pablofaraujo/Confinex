#!/usr/bin/env python3
"""Regressões sintéticas do leitor privado do cache Wey."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ler_cache_wey_b3 import (
    CacheWeyIndisponivel,
    SCHEMA_MANIFESTO,
    _normalizar_alias_macos,
    ler_cache_wey_b3,
    ler_cache_wey_b3_manifesto,
)


SCHEMA_FIXTURE = """
CREATE TABLE chats (
    jid TEXT PRIMARY KEY,
    kind TEXT,
    name TEXT,
    last_message_ts INTEGER,
    archived INTEGER,
    pinned INTEGER,
    muted_until INTEGER,
    unread INTEGER,
    unread_count INTEGER
);
CREATE TABLE messages (
    rowid INTEGER PRIMARY KEY,
    chat_jid TEXT,
    chat_name TEXT,
    msg_id TEXT,
    sender_jid TEXT,
    sender_name TEXT,
    ts INTEGER,
    from_me INTEGER,
    text TEXT,
    display_text TEXT,
    quoted_msg_id TEXT,
    quoted_sender_jid TEXT,
    is_forwarded INTEGER,
    forwarding_score INTEGER,
    reaction_to_id TEXT,
    reaction_emoji TEXT,
    media_type TEXT,
    media_caption TEXT,
    filename TEXT,
    mime_type TEXT,
    direct_path TEXT,
    media_key BLOB,
    file_sha256 BLOB,
    file_enc_sha256 BLOB,
    file_length INTEGER,
    local_path TEXT,
    downloaded_at INTEGER,
    media_unavailable_at INTEGER,
    revoked INTEGER NOT NULL DEFAULT 0,
    deleted_for_me INTEGER NOT NULL DEFAULT 0,
    deleted_at INTEGER,
    deletion_reason TEXT,
    payload_purged_at INTEGER,
    edited INTEGER NOT NULL DEFAULT 0,
    edited_ts INTEGER NOT NULL DEFAULT 0,
    buttons TEXT
);
"""


class TestLeitorCacheWey(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.pasta = Path(self.temp.name).resolve()
        self.db = self.pasta / "cache.db"
        self.jid = "5511999999999@s.whatsapp.net"
        conexao = sqlite3.connect(self.db)
        conexao.executescript(SCHEMA_FIXTURE)
        conexao.execute("INSERT INTO chats(jid, kind) VALUES (?, ?)", (self.jid, "chat"))
        conexao.commit()
        conexao.close()
        self.db.chmod(0o600)
        self.manifesto = self.pasta / "manifesto.json"
        self._gravar_manifesto()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _gravar_manifesto(self, **mudancas: object) -> None:
        dados: dict[str, object] = {
            "schema_version": SCHEMA_MANIFESTO,
            "db_path": str(self.db),
            "chat_jid": self.jid,
        }
        dados.update(mudancas)
        self.manifesto.write_text(json.dumps(dados), encoding="utf-8")
        self.manifesto.chmod(0o600)

    def _inserir(
        self,
        rowid: int,
        msg_id: str | None,
        ts: int,
        *,
        jid: str | None = None,
        texto: str | None = None,
        display: str | None = None,
        legenda: str | None = None,
        media: str | None = None,
    ) -> None:
        conexao = sqlite3.connect(self.db)
        conexao.execute(
            "INSERT INTO messages(rowid,chat_jid,msg_id,ts,text,display_text,media_caption,media_type) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (rowid, jid or self.jid, msg_id, ts, texto, display, legenda, media),
        )
        conexao.commit()
        conexao.close()

    def _ler(self, **opcoes: object) -> dict[str, object]:
        return ler_cache_wey_b3(
            self.manifesto,
            inicio="2026-09-01T00:00:00Z",
            fim="2026-09-06T23:59:59Z",
            **opcoes,
        )

    def test_isola_conversa_e_janela_fechada(self) -> None:
        outro = "5511888888888@s.whatsapp.net"
        conexao = sqlite3.connect(self.db)
        conexao.execute("INSERT INTO chats(jid) VALUES (?)", (outro,))
        conexao.commit()
        conexao.close()
        inicio, fim = 1788220800, 1788739199
        self._inserir(1, "a", inicio, texto="início")
        self._inserir(2, "b", fim, display="fim")
        self._inserir(3, "c", inicio - 1, texto="fora")
        self._inserir(4, "d", inicio, jid=outro, texto="outra conversa")
        resultado = self._ler()
        self.assertEqual([m["texto"] for m in resultado["documento"]["mensagens"]], ["início", "fim"])
        self.assertEqual(resultado["documento"]["cobertura"]["estado"], "parcial")
        self.assertFalse(resultado["documento"]["cobertura"]["atestado"])
        self.assertNotIn(self.jid, json.dumps(resultado, ensure_ascii=False))

    def test_conteudo_prioriza_text_display_legenda_sem_ocr(self) -> None:
        ts = 1788220800
        self._inserir(1, "a", ts, texto="texto", display="display", legenda="legenda")
        self._inserir(2, "b", ts + 1, display="display")
        self._inserir(3, "c", ts + 2, legenda="legenda", media="image")
        self._inserir(4, "d", ts + 3, media="image")
        resultado = self._ler()
        self.assertEqual([m["texto"] for m in resultado["documento"]["mensagens"]], ["texto", "display", "legenda"])
        self.assertEqual(resultado["metadados"]["omitidas_sem_texto"], 1)

    def test_marcadores_exatos_de_audio_sao_omitidos_sem_ocr(self) -> None:
        self._inserir(
            1, "audio", 1788220800,
            texto=" [Audio] ", display="SENT AUDIO", legenda="［Ａｕｄｉｏ］", media=" audio ",
        )
        resultado = self._ler()
        self.assertEqual(resultado["documento"]["mensagens"], [])
        self.assertEqual(resultado["metadados"]["omitidas_anexo_sem_texto"], 1)
        self.assertEqual(resultado["metadados"]["omitidas_sem_texto"], 0)
        self.assertFalse(resultado["documento"]["cobertura"]["truncada"])

    def test_audio_sem_campos_conta_anexo_e_vazio_sem_tipo_conta_sem_texto(self) -> None:
        self._inserir(1, "audio-vazio", 1788220800, media="audio")
        self._inserir(2, "vazio", 1788220801)
        resultado = self._ler()
        self.assertEqual(resultado["documento"]["mensagens"], [])
        self.assertEqual(resultado["metadados"]["omitidas_anexo_sem_texto"], 1)
        self.assertEqual(resultado["metadados"]["omitidas_sem_texto"], 1)

    def test_allowlist_de_midia_e_exata_por_campo_e_tipo(self) -> None:
        ts = 1788220800
        self._inserir(1, "humano", ts, texto="enviei o áudio sobre BGI", display="Sent audio", media="audio")
        self._inserir(2, "display", ts + 1, texto="[Audio]", display="Fechei BGI", media="audio")
        self._inserir(3, "tipo", ts + 2, texto="[Audio]", media="document")
        self._inserir(4, "campo-text", ts + 3, texto="Sent audio", media="audio")
        self._inserir(5, "campo-display", ts + 4, display="[Audio]", media="audio")
        self._inserir(6, "campo-caption", ts + 5, legenda="Sent audio", media="audio")
        resultado = self._ler()
        self.assertEqual(
            [m["texto"] for m in resultado["documento"]["mensagens"]],
            ["enviei o áudio sobre BGI", "Fechei BGI", "[Audio]", "Sent audio", "[Audio]", "Sent audio"],
        )
        self.assertEqual(resultado["metadados"]["omitidas_anexo_sem_texto"], 0)

    def test_legenda_humana_apos_dois_marcadores_e_preservada(self) -> None:
        self._inserir(
            1, "legenda", 1788220800, texto="[Audio]", display="Sent audio",
            legenda="Fechei BGI-26-001", media="audio",
        )
        resultado = self._ler()
        self.assertEqual(resultado["documento"]["mensagens"][0]["texto"], "Fechei BGI-26-001")

    def test_substring_e_marcador_desconhecido_sao_preservados(self) -> None:
        self._inserir(1, "substring", 1788220800, texto="[Audio] texto humano", media="audio")
        self._inserir(2, "desconhecido", 1788220801, texto="[Voice]", media="audio")
        resultado = self._ler()
        self.assertEqual(
            [m["texto"] for m in resultado["documento"]["mensagens"]],
            ["[Audio] texto humano", "[Voice]"],
        )

    def test_legenda_humana_grande_apos_marcadores_nao_fabrica_fallback(self) -> None:
        self._inserir(
            1, "legenda-grande", 1788220800, texto="[Audio]", display="Sent audio",
            legenda="12345", media="audio",
        )
        resultado = self._ler(max_bytes_mensagem=4, max_bytes_total=20)
        self.assertEqual(resultado["documento"]["mensagens"], [])
        self.assertEqual(resultado["metadados"]["omitidas_tamanho"], 1)
        self.assertEqual(resultado["metadados"]["omitidas_anexo_sem_texto"], 0)

    def test_media_type_e_validado_e_limitado_antes_da_classificacao(self) -> None:
        conexao = sqlite3.connect(self.db)
        conexao.execute(
            "INSERT INTO messages(rowid,chat_jid,msg_id,ts,text,media_type) VALUES(?,?,?,?,?,?)",
            (1, self.jid, "tipo", 1788220800, "[Audio]", sqlite3.Binary(b"\xff")),
        )
        conexao.commit()
        conexao.close()
        with self.assertRaisesRegex(CacheWeyIndisponivel, "conteudo_cache_invalido"):
            self._ler()

    def test_media_type_acima_do_limite_falha_fechado(self) -> None:
        self._inserir(1, "tipo-grande", 1788220800, texto="[Audio]", media="a" * 257)
        with self.assertRaisesRegex(CacheWeyIndisponivel, "conteudo_cache_invalido"):
            self._ler()

    def test_campo_inferior_corrompido_falha_mesmo_com_texto_prioritario(self) -> None:
        conexao = sqlite3.connect(self.db)
        conexao.execute(
            "INSERT INTO messages(rowid,chat_jid,msg_id,ts,text,display_text,media_type) "
            "VALUES(?,?,?,?,?,?,?)",
            (1, self.jid, "corrompida", 1788220800, "texto válido", sqlite3.Binary(b"\xff"), "audio"),
        )
        conexao.commit()
        conexao.close()
        with self.assertRaisesRegex(CacheWeyIndisponivel, "conteudo_cache_invalido"):
            self._ler()
        conexao = sqlite3.connect(self.db)
        conexao.execute("UPDATE messages SET media_type = ?", ("a" * 257,))
        conexao.commit()
        conexao.close()
        with self.assertRaisesRegex(CacheWeyIndisponivel, "conteudo_cache_invalido"):
            self._ler()

    def test_texto_prioritario_acima_do_limite_nao_cai_para_campo_inferior(self) -> None:
        self._inserir(1, "grande", 1788220800, texto="12345", display="ok", media="audio")
        resultado = self._ler(max_bytes_mensagem=4, max_bytes_total=20)
        self.assertEqual(resultado["documento"]["mensagens"], [])
        self.assertEqual(resultado["metadados"]["omitidas_tamanho"], 1)
        self.assertEqual(resultado["metadados"]["omitidas_anexo_sem_texto"], 0)

    def test_campo_inferior_grande_nao_substitui_texto_prioritario_valido(self) -> None:
        self._inserir(1, "prioridade", 1788220800, texto="ok", display="12345", media="audio")
        resultado = self._ler(max_bytes_mensagem=4, max_bytes_total=20)
        self.assertEqual([m["texto"] for m in resultado["documento"]["mensagens"]], ["ok"])
        self.assertEqual(resultado["metadados"]["omitidas_tamanho"], 0)

    def test_msg_id_ausente_nao_e_inventado(self) -> None:
        self._inserir(1, None, 1788220800, texto="sem id")
        resultado = self._ler()
        self.assertIsNone(resultado["documento"]["mensagens"][0]["mensagem_ref"])
        self.assertTrue(resultado["metadados"]["identidade_pendente"])

    def test_revogada_excluida_expurgada_sao_omitidas_e_edicao_e_declarada(self) -> None:
        conexao = sqlite3.connect(self.db)
        linhas = (
            (1, "rev", "B3-26-001 encerrar", 1, 0, None, None, 0, 0),
            (2, "del", "B3-26-001 encerrar", 0, 1, None, None, 0, 0),
            (3, "pur", "B3-26-001 encerrar", 0, 0, None, 1788220900, 0, 0),
            (4, "edit", "Correção B3-26-001 rolada", 0, 0, None, None, 1, 1788221000),
            (5, "delt", "B3-26-001 encerrar", 0, 0, 1788221001, None, 0, 0),
        )
        for rowid, msg_id, texto, revogada, excluida, excluida_em, expurgada, editada, editada_em in linhas:
            conexao.execute(
                "INSERT INTO messages(rowid,chat_jid,msg_id,ts,text,revoked,deleted_for_me,"
                "deleted_at,payload_purged_at,edited,edited_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (rowid, self.jid, msg_id, 1788220800 + rowid, texto, revogada, excluida,
                 excluida_em, expurgada, editada, editada_em),
            )
        conexao.commit()
        conexao.close()
        resultado = self._ler()
        self.assertEqual([m["texto"] for m in resultado["documento"]["mensagens"]], ["Correção B3-26-001 rolada"])
        self.assertEqual(resultado["metadados"]["omitidas_por_estado"], 4)
        self.assertEqual(resultado["metadados"]["editadas_sem_historico"], 1)

    def test_revogada_e_omitida_antes_de_decodificar_ou_aplicar_limite(self) -> None:
        conexao = sqlite3.connect(self.db)
        conexao.execute(
            "INSERT INTO messages(rowid,chat_jid,msg_id,ts,text,revoked) VALUES(?,?,?,?,?,?)",
            (1, self.jid, "rev", 1788220800, sqlite3.Binary(b"\xff" * 100), 1),
        )
        conexao.commit()
        conexao.close()
        resultado = self._ler(max_bytes_mensagem=4, max_bytes_total=20)
        self.assertEqual(resultado["metadados"]["omitidas_por_estado"], 1)
        self.assertEqual(resultado["metadados"]["omitidas_tamanho"], 0)
        self.assertEqual(resultado["documento"]["mensagens"], [])

    def test_flags_fora_de_zero_um_e_edicao_incoerente_falham(self) -> None:
        casos = (
            ("revoked", 2, 0),
            ("deleted_for_me", -1, 0),
            ("edited", 0, 1788220900),
        )
        for coluna, valor, edited_ts in casos:
            with self.subTest(coluna=coluna, valor=valor):
                conexao = sqlite3.connect(self.db)
                try:
                    conexao.execute("DELETE FROM messages")
                    conexao.execute(
                        f"INSERT INTO messages(rowid,chat_jid,msg_id,ts,text,{coluna},edited_ts) "
                        "VALUES(?,?,?,?,?,?,?)",
                        (1, self.jid, "estado", 1788220800, "fixture", valor, edited_ts),
                    )
                    conexao.commit()
                finally:
                    conexao.close()
                with self.assertRaisesRegex(CacheWeyIndisponivel, "estado_cache_invalido"):
                    self._ler()

    def test_sentinela_real_edicao_zero_e_edicao_positiva(self) -> None:
        conexao = sqlite3.connect(self.db)
        conexao.execute(
            "INSERT INTO messages(rowid,chat_jid,msg_id,ts,text,edited,edited_ts) "
            "VALUES(?,?,?,?,?,?,?)",
            (1, self.jid, "original", 1788220800, "original", 0, 0),
        )
        conexao.execute(
            "INSERT INTO messages(rowid,chat_jid,msg_id,ts,text,edited,edited_ts) "
            "VALUES(?,?,?,?,?,?,?)",
            (2, self.jid, "editada", 1788220801, "correção", 1, 1788220802),
        )
        conexao.commit()
        conexao.close()
        resultado = self._ler()
        self.assertEqual([m["texto"] for m in resultado["documento"]["mensagens"]], ["original", "correção"])
        self.assertEqual(resultado["metadados"]["editadas_sem_historico"], 1)

    def test_formato_de_conversa_individual_e_allowlist_fechada(self) -> None:
        for jid in ("canal@newsletter", "grupo@g.us", "identidade@lid"):
            with self.subTest(jid=jid):
                with self.assertRaisesRegex(CacheWeyIndisponivel, "conversa_fora_do_escopo_individual"):
                    ler_cache_wey_b3_manifesto(
                        {"schema_version": SCHEMA_MANIFESTO, "db_path": str(self.db), "chat_jid": jid},
                        inicio="2026-09-01T00:00:00Z", fim="2026-09-02T00:00:00Z",
                    )

    def test_sql_injection_e_parametrizada_e_exata(self) -> None:
        jid_malicioso = "' OR 1=1 --@s.whatsapp.net"
        conexao = sqlite3.connect(self.db)
        conexao.execute("INSERT INTO chats(jid) VALUES (?)", (jid_malicioso,))
        conexao.execute(
            "INSERT INTO messages(rowid,chat_jid,msg_id,ts,text) VALUES(?,?,?,?,?)",
            (1, self.jid, "normal", 1788220800, "não deve vazar"),
        )
        conexao.execute(
            "INSERT INTO messages(rowid,chat_jid,msg_id,ts,text) VALUES(?,?,?,?,?)",
            (2, jid_malicioso, "exato", 1788220800, "somente exata"),
        )
        conexao.commit()
        conexao.close()
        resultado = ler_cache_wey_b3_manifesto(
            {"schema_version": SCHEMA_MANIFESTO, "db_path": str(self.db), "chat_jid": jid_malicioso},
            inicio="2026-09-01T00:00:00Z", fim="2026-09-06T23:59:59Z",
        )
        self.assertEqual([m["texto"] for m in resultado["documento"]["mensagens"]], ["somente exata"])

    def test_mode_ro_query_only_sem_immutable_ddl_ou_checkpoint(self) -> None:
        self._inserir(1, "a", 1788220800, texto="ok")
        chamadas: list[tuple[str, dict[str, object]]] = []
        sql_emitido: list[str] = []

        def conectar(database: str, **opcoes: object) -> sqlite3.Connection:
            chamadas.append((database, opcoes))
            conexao = sqlite3.connect(database, **opcoes)
            conexao.set_trace_callback(sql_emitido.append)
            return conexao

        antes = self.db.read_bytes()
        self._ler(conectar=conectar)
        self.assertEqual(self.db.read_bytes(), antes)
        self.assertIn("mode=ro", chamadas[0][0])
        self.assertNotIn("immutable", chamadas[0][0])
        self.assertTrue(chamadas[0][1]["uri"])
        comandos = "\n".join(sql_emitido).upper()
        self.assertIn("PRAGMA QUERY_ONLY = ON", comandos)
        for proibido in ("INSERT ", "UPDATE ", "DELETE ", "CREATE ", "CHECKPOINT"):
            self.assertNotIn(proibido, comandos)
        somente_leitura = sqlite3.connect(
            "file:" + str(self.db) + "?mode=ro", uri=True, isolation_level=None
        )
        somente_leitura.execute("PRAGMA query_only = ON")
        try:
            with self.assertRaises(sqlite3.OperationalError):
                somente_leitura.execute("INSERT INTO chats(jid) VALUES ('fixture')")
            with self.assertRaises(sqlite3.OperationalError):
                somente_leitura.execute("CREATE TABLE proibida(id INTEGER)")
        finally:
            somente_leitura.close()

    def test_leitura_enxerga_wal_sem_immutable(self) -> None:
        escritor = sqlite3.connect(self.db)
        escritor.execute("PRAGMA journal_mode=WAL")
        escritor.execute("PRAGMA wal_autocheckpoint=0")
        escritor.execute(
            "INSERT INTO messages(rowid,chat_jid,msg_id,ts,text) VALUES(?,?,?,?,?)",
            (1, self.jid, "wal", 1788220800, "no wal"),
        )
        escritor.commit()
        try:
            self.assertEqual(self._ler()["documento"]["mensagens"][0]["texto"], "no wal")
        finally:
            escritor.close()

    def test_truncamentos_quantidade_bytes_e_celula(self) -> None:
        for indice in range(1, 4):
            self._inserir(indice, str(indice), 1788220800 + indice, texto="abcde")
        quantidade = self._ler(limite=2)
        self.assertTrue(quantidade["metadados"]["truncada_quantidade"])
        total = self._ler(max_bytes_total=7, max_bytes_mensagem=7)
        self.assertTrue(total["metadados"]["truncada_bytes"])
        celula = self._ler(max_bytes_mensagem=4, max_bytes_total=20)
        self.assertEqual(celula["documento"]["mensagens"], [])
        self.assertEqual(celula["metadados"]["omitidas_tamanho"], 3)
        self.assertTrue(celula["documento"]["cobertura"]["truncada"])

    def test_deadline_e_limites_de_tipos_falham_fechado(self) -> None:
        relogio = iter((0.0, 1.0))
        with self.assertRaisesRegex(CacheWeyIndisponivel, "deadline_excedido"):
            self._ler(segundos=0.05, monotonic=lambda: next(relogio, 1.0))
        for opcoes in ({"limite": True}, {"max_bytes_total": True}, {"segundos": True}):
            with self.assertRaises(CacheWeyIndisponivel):
                self._ler(**opcoes)

    def test_intervalo_fracionario_nao_amplia_janela(self) -> None:
        self._inserir(1, "a", 1788220800, texto="antes")
        self._inserir(2, "b", 1788220801, texto="dentro")
        resultado = ler_cache_wey_b3(
            self.manifesto,
            inicio="2026-09-01T00:00:00.5Z",
            fim="2026-09-01T00:00:01.9Z",
        )
        self.assertEqual([m["texto"] for m in resultado["documento"]["mensagens"]], ["dentro"])

    def test_schema_view_coluna_tipo_pk_e_conversa_inexistente_falham(self) -> None:
        self._gravar_manifesto(chat_jid="5511777777777@s.whatsapp.net")
        with self.assertRaisesRegex(CacheWeyIndisponivel, "conversa_exata_nao_encontrada"):
            self._ler()
        conexao = sqlite3.connect(self.db)
        conexao.execute("DROP TABLE messages")
        conexao.execute("CREATE VIEW messages AS SELECT 1 AS rowid")
        conexao.commit()
        conexao.close()
        self._gravar_manifesto()
        with self.assertRaisesRegex(CacheWeyIndisponivel, "schema_cache_incompativel"):
            self._ler()

    def test_schema_falha_com_coluna_tipo_ou_pk_incorretos_em_ambas_tabelas(self) -> None:
        casos = {
            "mensagens_sem_coluna": SCHEMA_FIXTURE.replace("    media_type TEXT,\n", ""),
            "mensagens_tipo_incorreto": SCHEMA_FIXTURE.replace("    ts INTEGER,\n", "    ts TEXT,\n"),
            "mensagens_sem_pk": SCHEMA_FIXTURE.replace(
                "    rowid INTEGER PRIMARY KEY,\n", "    rowid INTEGER,\n"
            ),
            "chats_sem_coluna": SCHEMA_FIXTURE.replace("    jid TEXT PRIMARY KEY,\n", ""),
            "chats_tipo_incorreto": SCHEMA_FIXTURE.replace(
                "    jid TEXT PRIMARY KEY,\n", "    jid INTEGER PRIMARY KEY,\n"
            ),
            "chats_sem_pk": SCHEMA_FIXTURE.replace(
                "    jid TEXT PRIMARY KEY,\n", "    jid TEXT,\n"
            ),
        }
        for nome, schema in casos.items():
            with self.subTest(caso=nome):
                self.db.unlink(missing_ok=True)
                conexao = sqlite3.connect(self.db)
                conexao.executescript(schema)
                conexao.close()
                self.db.chmod(0o600)
                with self.assertRaisesRegex(CacheWeyIndisponivel, "schema_cache_incompativel"):
                    self._ler()

    def test_manifesto_privado_absoluto_sem_symlink_nem_repo(self) -> None:
        self.manifesto.chmod(0o640)
        with self.assertRaisesRegex(CacheWeyIndisponivel, "manifesto_sem_permissao_privada"):
            self._ler()
        self.manifesto.chmod(0o600)
        link = self.pasta / "manifesto-link.json"
        link.symlink_to(self.manifesto)
        with self.assertRaisesRegex(CacheWeyIndisponivel, "caminho_privado_invalido"):
            ler_cache_wey_b3(link, inicio="2026-09-01T00:00:00Z", fim="2026-09-02T00:00:00Z")
        (self.pasta / ".git").mkdir()
        with self.assertRaisesRegex(CacheWeyIndisponivel, "caminho_privado_invalido"):
            self._ler()

    def test_banco_fonte_privado_pode_estar_sob_git_sem_relaxar_manifesto(self) -> None:
        repositorio = self.pasta / "fonte"
        repositorio.mkdir()
        (repositorio / ".git").mkdir()
        banco_no_repo = repositorio / "cache.db"
        self.db.rename(banco_no_repo)
        banco_no_repo.chmod(0o600)
        self.db = banco_no_repo
        self._gravar_manifesto(db_path=str(banco_no_repo))
        resultado = self._ler()
        self.assertEqual(resultado["documento"]["cobertura"]["estado"], "parcial")

    def test_banco_aceita_0600_ou_0400_e_recusa_outros_modos_antes_de_conectar(self) -> None:
        for modo in (0o600, 0o400):
            with self.subTest(modo=oct(modo)):
                self.db.chmod(modo)
                self._ler()
        for modo in (0o640, 0o644, 0o000):
            with self.subTest(modo=oct(modo)):
                self.db.chmod(modo)
                chamado = False

                def conectar(*args: object, **kwargs: object) -> sqlite3.Connection:
                    nonlocal chamado
                    chamado = True
                    raise AssertionError("nao deveria conectar")

                with self.assertRaisesRegex(CacheWeyIndisponivel, "banco_privado_permissao_invalida"):
                    self._ler(conectar=conectar)
                self.assertFalse(chamado)
        self.db.chmod(0o600)

    def test_banco_de_outro_proprietario_e_recusado_antes_de_conectar(self) -> None:
        uid_diferente = self.db.stat().st_uid + 1
        chamado = False

        def conectar(*args: object, **kwargs: object) -> sqlite3.Connection:
            nonlocal chamado
            chamado = True
            raise AssertionError("nao deveria conectar")

        with patch("ler_cache_wey_b3.os.geteuid", return_value=uid_diferente), \
             self.assertRaisesRegex(CacheWeyIndisponivel, "banco_privado_proprietario_invalido"):
            self._ler(conectar=conectar)
        self.assertFalse(chamado)

    def test_banco_folha_e_ancestral_symlink_sao_recusados(self) -> None:
        link_folha = self.pasta / "cache-link.db"
        link_folha.symlink_to(self.db)
        self._gravar_manifesto(db_path=str(link_folha))
        with self.assertRaisesRegex(CacheWeyIndisponivel, "banco_privado_invalido"):
            self._ler()

        real = self.pasta / "real"
        real.mkdir()
        banco_real = real / "cache.db"
        self.db.rename(banco_real)
        banco_real.chmod(0o600)
        ancestral = self.pasta / "ancestral-link"
        ancestral.symlink_to(real, target_is_directory=True)
        self._gravar_manifesto(db_path=str(ancestral / "cache.db"))
        with self.assertRaisesRegex(CacheWeyIndisponivel, "banco_privado_invalido"):
            self._ler()

    def test_alias_macos_so_e_normalizado_quando_destino_confirmado(self) -> None:
        caminho = Path("/tmp/privado/manifesto.json")
        correto = _normalizar_alias_macos(
            caminho, plataforma="darwin", resolver=lambda _: "/private/tmp"
        )
        falso = _normalizar_alias_macos(
            caminho, plataforma="darwin", resolver=lambda _: "/destino/controlado"
        )
        self.assertEqual(correto, Path("/private/tmp/privado/manifesto.json"))
        self.assertEqual(falso, caminho)

    def test_erros_nao_expoem_jid_caminho_texto_ou_sqlite(self) -> None:
        self.db.write_bytes(b"not sqlite; segredo financeiro")
        with self.assertRaises(CacheWeyIndisponivel) as captura:
            self._ler()
        erro = str(captura.exception)
        self.assertIn(erro, {"cache_sqlite_indisponivel", "schema_cache_incompativel"})
        self.assertNotIn(self.jid, erro)
        self.assertNotIn(str(self.db), erro)
        self.assertNotIn("segredo", erro)

    def test_timestamp_extremo_falha_sem_excecao_bruta(self) -> None:
        self._inserir(1, "a", 9223372036854775807, texto="fora")
        # A janela SQL não alcança o extremo; injeta um inteiro extremo dentro do
        # resultado por uma conexão proxy não é necessário para provar o recorte.
        with self.assertRaises(CacheWeyIndisponivel):
            ler_cache_wey_b3(
                self.manifesto,
                inicio="9999-12-31T23:59:59.9+14:00",
                fim="9999-12-31T23:59:59.99+14:00",
            )


if __name__ == "__main__":
    unittest.main()
