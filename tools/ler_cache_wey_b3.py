#!/usr/bin/env python3
"""Lê um recorte exato do cache Wey sem sincronizar ou alterar o SQLite.

O caminho do banco e o JID existem somente em um manifesto privado. A abertura
usa ``mode=ro`` (sem ``immutable``, para respeitar WAL), ``query_only`` e SQL
fixo parametrizado. O resultado continua parcial: um recorte do cache local não
atesta a completude histórica do WhatsApp.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import stat
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote


SCHEMA_MANIFESTO = "manifesto-cache-wey-b3-v1"
SCHEMA_MENSAGENS = "mensagens-whatsapp-normalizadas-v1"
TAMANHO_MAXIMO_MANIFESTO = 32_768
LIMITE_MAXIMO_MENSAGENS = 1_000
LIMITE_MAXIMO_DIAS = 31
LIMITE_MAXIMO_BYTES_MENSAGEM = 64_000
LIMITE_MAXIMO_BYTES_TOTAL = 4_000_000
SQL_MENSAGENS = """
SELECT rowid,
       substr(CAST(msg_id AS BLOB), 1, ?), length(CAST(msg_id AS BLOB)), ts,
       substr(CAST(text AS BLOB), 1, ?), length(CAST(text AS BLOB)),
       substr(CAST(display_text AS BLOB), 1, ?), length(CAST(display_text AS BLOB)),
       substr(CAST(media_caption AS BLOB), 1, ?), length(CAST(media_caption AS BLOB)),
       substr(CAST(media_type AS BLOB), 1, ?), length(CAST(media_type AS BLOB)),
       revoked, deleted_for_me, deleted_at, payload_purged_at, edited, edited_ts
FROM messages
WHERE chat_jid = ? AND ts >= ? AND ts <= ?
ORDER BY ts ASC, rowid ASC
LIMIT ?
""".strip()
COLUNAS_MENSAGENS = {
    "rowid": "INTEGER",
    "chat_jid": "TEXT",
    "msg_id": "TEXT",
    "ts": "INTEGER",
    "text": "TEXT",
    "display_text": "TEXT",
    "media_caption": "TEXT",
    "media_type": "TEXT",
    "revoked": "INTEGER",
    "deleted_for_me": "INTEGER",
    "deleted_at": "INTEGER",
    "payload_purged_at": "INTEGER",
    "edited": "INTEGER",
    "edited_ts": "INTEGER",
}
COLUNAS_CHATS = {"jid": "TEXT"}
DETALHE_COBERTURA = "cache_local_recorte_limitado_nao_atesta_historico_completo"


class CacheWeyIndisponivel(RuntimeError):
    """Erro fechado cujo texto é somente um código estável e sanitizado."""

    def __init__(self, codigo: str) -> None:
        self.codigo = codigo
        super().__init__(codigo)


def _falhar(codigo: str) -> None:
    raise CacheWeyIndisponivel(codigo)


def _normalizar_alias_macos(
    caminho: Path,
    *,
    plataforma: str | None = None,
    resolver: Callable[[str], str] = os.path.realpath,
) -> Path:
    absoluto = caminho.absolute()
    plataforma = sys.platform if plataforma is None else plataforma
    if plataforma != "darwin" or len(absoluto.parts) < 2:
        return absoluto
    esperado = {"tmp": "/private/tmp", "var": "/private/var"}.get(absoluto.parts[1])
    if esperado and resolver("/" + absoluto.parts[1]) == esperado:
        return Path(esperado, *absoluto.parts[2:])
    return absoluto


def _tem_ancestral_symlink(caminho: Path) -> bool:
    atual = _normalizar_alias_macos(caminho)
    while True:
        try:
            if stat.S_ISLNK(atual.lstat().st_mode):
                return True
        except OSError:
            return True
        if atual.parent == atual:
            return False
        atual = atual.parent


def _esta_em_repositorio(caminho: Path) -> bool:
    atual = caminho.parent
    while True:
        try:
            if (atual / ".git").exists():
                return True
        except OSError:
            return True
        if atual.parent == atual:
            return False
        atual = atual.parent


def _validar_arquivo(caminho: Path, *, manifesto: bool) -> os.stat_result:
    if not isinstance(caminho, Path) or not caminho.is_absolute():
        _falhar("caminho_privado_invalido")
    if _tem_ancestral_symlink(caminho) or _esta_em_repositorio(caminho):
        _falhar("caminho_privado_invalido")
    try:
        dados = caminho.lstat()
    except OSError:
        _falhar("arquivo_privado_indisponivel")
    if not stat.S_ISREG(dados.st_mode):
        _falhar("arquivo_privado_invalido")
    if manifesto and dados.st_mode & 0o077:
        _falhar("manifesto_sem_permissao_privada")
    return dados


def _validar_banco_entrada(caminho: Path) -> os.stat_result:
    """Valida a fonte privada; estar sob Git não transforma leitura em saída."""
    if not isinstance(caminho, Path) or not caminho.is_absolute():
        _falhar("banco_privado_invalido")
    if _tem_ancestral_symlink(caminho):
        _falhar("banco_privado_invalido")
    try:
        dados = caminho.lstat()
    except OSError:
        _falhar("banco_privado_indisponivel")
    if not stat.S_ISREG(dados.st_mode):
        _falhar("banco_privado_invalido")
    if dados.st_uid != os.geteuid():
        _falhar("banco_privado_proprietario_invalido")
    if stat.S_IMODE(dados.st_mode) not in {0o400, 0o600}:
        _falhar("banco_privado_permissao_invalida")
    return dados


def _validar_manifesto(manifesto: Any) -> dict[str, str]:
    if not isinstance(manifesto, dict) or set(manifesto) != {
        "schema_version", "db_path", "chat_jid"
    }:
        _falhar("manifesto_invalido")
    if manifesto.get("schema_version") != SCHEMA_MANIFESTO:
        _falhar("manifesto_invalido")
    db_path = manifesto.get("db_path")
    chat_jid = manifesto.get("chat_jid")
    if not isinstance(db_path, str) or not db_path or not Path(db_path).is_absolute():
        _falhar("manifesto_invalido")
    if not isinstance(chat_jid, str) or not chat_jid or len(chat_jid) > 512:
        _falhar("manifesto_invalido")
    jid_menor = chat_jid.casefold()
    if not jid_menor.endswith("@s.whatsapp.net"):
        _falhar("conversa_fora_do_escopo_individual")
    _validar_banco_entrada(Path(db_path))
    return {"schema_version": SCHEMA_MANIFESTO, "db_path": db_path, "chat_jid": chat_jid}


def ler_manifesto_privado(caminho: Path) -> dict[str, str]:
    """Lê e valida o manifesto mínimo, sem retornar caminhos em erros."""
    caminho = Path(caminho)
    dados_arquivo = _validar_arquivo(caminho, manifesto=True)
    if dados_arquivo.st_size > TAMANHO_MAXIMO_MANIFESTO:
        _falhar("manifesto_acima_do_limite")
    try:
        with caminho.open("r", encoding="utf-8") as arquivo:
            manifesto = json.load(arquivo)
    except (OSError, UnicodeError, json.JSONDecodeError):
        _falhar("manifesto_invalido")
    return _validar_manifesto(manifesto)


def _data_utc(valor: str) -> datetime:
    if not isinstance(valor, str) or not valor:
        _falhar("intervalo_invalido")
    try:
        data = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        _falhar("intervalo_invalido")
    if data.tzinfo is None:
        _falhar("intervalo_sem_timezone")
    return data.astimezone(timezone.utc)


def _janela(inicio: str, fim: str) -> tuple[datetime, datetime, int, int]:
    inicio_utc = _data_utc(inicio)
    fim_utc = _data_utc(fim)
    if inicio_utc > fim_utc or fim_utc - inicio_utc > timedelta(days=LIMITE_MAXIMO_DIAS):
        _falhar("intervalo_invalido")
    try:
        segundo_inicio = math.ceil(inicio_utc.timestamp())
        segundo_fim = math.floor(fim_utc.timestamp())
    except (OverflowError, OSError, ValueError):
        _falhar("intervalo_invalido")
    if segundo_inicio > segundo_fim:
        _falhar("intervalo_sem_segundo_inteiro")
    return inicio_utc, fim_utc, segundo_inicio, segundo_fim


def _referencia_opaca(valor: str, tamanho: int = 24) -> str:
    return hashlib.sha256(valor.encode("utf-8")).hexdigest()[:tamanho]


def _colunas_tabela(conexao: sqlite3.Connection, tabela: str) -> list[tuple[Any, ...]]:
    try:
        registro = conexao.execute(
            "SELECT type FROM sqlite_schema WHERE name = ?", (tabela,)
        ).fetchall()
        colunas = conexao.execute("PRAGMA table_info(" + tabela + ")").fetchall()
    except sqlite3.OperationalError as exc:
        if "interrupt" in str(exc).casefold():
            _falhar("deadline_excedido")
        _falhar("schema_cache_incompativel")
    except sqlite3.Error:
        _falhar("schema_cache_incompativel")
    if registro != [("table",)]:
        _falhar("schema_cache_incompativel")
    return colunas


def _validar_schema(conexao: sqlite3.Connection) -> None:
    colunas = _colunas_tabela(conexao, "messages")
    tipos = {
        linha[1]: str(linha[2] or "").upper()
        for linha in colunas
        if isinstance(linha, tuple) and len(linha) >= 3
    }
    if any(tipos.get(nome) != tipo for nome, tipo in COLUNAS_MENSAGENS.items()):
        _falhar("schema_cache_incompativel")
    primaria = {linha[1]: linha[5] for linha in colunas if len(linha) >= 6}
    if primaria.get("rowid") != 1:
        _falhar("schema_cache_incompativel")
    colunas_chats = _colunas_tabela(conexao, "chats")
    tipos_chats = {linha[1]: str(linha[2] or "").upper() for linha in colunas_chats if len(linha) >= 3}
    primaria_chats = {linha[1]: linha[5] for linha in colunas_chats if len(linha) >= 6}
    if any(tipos_chats.get(nome) != tipo for nome, tipo in COLUNAS_CHATS.items()):
        _falhar("schema_cache_incompativel")
    if primaria_chats.get("jid") != 1:
        _falhar("schema_cache_incompativel")


def ler_cache_wey_b3(
    caminho_manifesto: Path,
    *,
    inicio: str,
    fim: str,
    limite: int = 200,
    segundos: float = 5.0,
    max_bytes_mensagem: int = 16_000,
    max_bytes_total: int = 1_000_000,
    conectar: Callable[..., sqlite3.Connection] = sqlite3.connect,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Retorna ``{documento, conversa_ref, metadados}`` para o normalizador.

    ``documento`` segue ``mensagens-whatsapp-normalizadas-v1``. Nenhum valor do
    manifesto aparece em erros ou metadados.
    """
    manifesto = ler_manifesto_privado(Path(caminho_manifesto))
    return ler_cache_wey_b3_manifesto(
        manifesto,
        inicio=inicio,
        fim=fim,
        limite=limite,
        segundos=segundos,
        max_bytes_mensagem=max_bytes_mensagem,
        max_bytes_total=max_bytes_total,
        conectar=conectar,
        monotonic=monotonic,
    )


def ler_cache_wey_b3_manifesto(
    manifesto: dict[str, Any],
    *,
    inicio: str,
    fim: str,
    limite: int = 200,
    segundos: float = 5.0,
    max_bytes_mensagem: int = 16_000,
    max_bytes_total: int = 1_000_000,
    conectar: Callable[..., sqlite3.Connection] = sqlite3.connect,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Executa a mesma leitura a partir de manifesto já mantido somente em RAM."""
    if isinstance(limite, bool) or not isinstance(limite, int) or not 1 <= limite <= LIMITE_MAXIMO_MENSAGENS:
        _falhar("limite_invalido")
    if isinstance(segundos, bool) or not isinstance(segundos, (int, float)) or not 0.05 <= segundos <= 30:
        _falhar("deadline_invalido")
    for valor, teto in (
        (max_bytes_mensagem, LIMITE_MAXIMO_BYTES_MENSAGEM),
        (max_bytes_total, LIMITE_MAXIMO_BYTES_TOTAL),
    ):
        if isinstance(valor, bool) or not isinstance(valor, int) or not 1 <= valor <= teto:
            _falhar("limite_bytes_invalido")
    if max_bytes_mensagem > max_bytes_total:
        _falhar("limite_bytes_invalido")

    manifesto = _validar_manifesto(manifesto)
    inicio_utc, fim_utc, segundo_inicio, segundo_fim = _janela(inicio, fim)
    conversa_ref = _referencia_opaca(manifesto["chat_jid"], 12)
    prazo = monotonic() + float(segundos)
    db_path = manifesto["db_path"]
    uri = "file:" + quote(db_path, safe="/") + "?mode=ro"
    conexao: sqlite3.Connection | None = None
    try:
        conexao = conectar(
            uri,
            uri=True,
            timeout=min(float(segundos), 2.0),
            isolation_level=None,
        )
        conexao.execute("PRAGMA query_only = ON")
        confirmado = conexao.execute("PRAGMA query_only").fetchone()
        if confirmado != (1,):
            _falhar("somente_leitura_nao_confirmada")

        def interromper() -> int:
            return 1 if monotonic() >= prazo else 0

        conexao.set_progress_handler(interromper, 100)
        conexao.execute("BEGIN")
        _validar_schema(conexao)
        conversa = conexao.execute(
            "SELECT 1 FROM chats WHERE jid = ? LIMIT 2", (manifesto["chat_jid"],)
        ).fetchall()
        if conversa != [(1,)]:
            _falhar("conversa_exata_nao_encontrada")
        teto_celula = max_bytes_mensagem + 1
        teto_identificador = 1_025
        teto_tipo = 257
        linhas = conexao.execute(SQL_MENSAGENS, (
            teto_identificador,
            teto_celula, teto_celula, teto_celula,
            teto_tipo,
            manifesto["chat_jid"], segundo_inicio, segundo_fim, limite + 1,
        )).fetchall()
        if monotonic() >= prazo:
            _falhar("deadline_excedido")
    except CacheWeyIndisponivel:
        raise
    except sqlite3.OperationalError as exc:
        codigo = "deadline_excedido" if "interrupt" in str(exc).casefold() else "cache_sqlite_indisponivel"
        _falhar(codigo)
    except (sqlite3.Error, OSError, UnicodeError, ValueError, TypeError):
        _falhar("cache_sqlite_indisponivel")
    finally:
        if conexao is not None:
            try:
                conexao.rollback()
                conexao.set_progress_handler(None, 0)
                conexao.close()
            except sqlite3.Error:
                pass

    truncada_quantidade = len(linhas) > limite
    omitidas_sem_texto = 0
    omitidas_tamanho = 0
    omitidas_por_estado = 0
    editadas_sem_historico = 0
    truncada_bytes = False
    identidade_pendente = False
    bytes_total = 0
    mensagens: list[dict[str, Any]] = []
    for linha in linhas[:limite]:
        if monotonic() >= prazo:
            _falhar("deadline_excedido")
        if not isinstance(linha, tuple) or len(linha) != 18:
            _falhar("linha_cache_invalida")
        (
            _rowid, msg_id_bytes, msg_id_tamanho, ts,
            texto_bytes, texto_tamanho,
            display_bytes, display_tamanho,
            legenda_bytes, legenda_tamanho,
            _media_tipo_bytes, media_tipo_tamanho,
            revoked, deleted_for_me, deleted_at, payload_purged_at, edited, edited_ts,
        ) = linha
        if isinstance(ts, bool) or not isinstance(ts, int):
            _falhar("timestamp_cache_invalido")
        estados = (revoked, deleted_for_me, edited)
        if any(valor not in {None, 0, 1} or isinstance(valor, bool) for valor in estados):
            _falhar("estado_cache_invalido")
        for valor in (deleted_at, payload_purged_at, edited_ts):
            if valor is not None and (
                isinstance(valor, bool) or not isinstance(valor, int) or valor < 0
            ):
                _falhar("estado_cache_invalido")
        if edited_ts not in {None, 0} and edited != 1:
            _falhar("estado_cache_invalido")
        if (
            revoked == 1 or deleted_for_me == 1
            or deleted_at is not None or payload_purged_at is not None
        ):
            omitidas_por_estado += 1
            continue
        if edited == 1:
            editadas_sem_historico += 1
        candidatos: list[tuple[str | None, int | None]] = []
        for bruto, tamanho_original in (
            (texto_bytes, texto_tamanho),
            (display_bytes, display_tamanho),
            (legenda_bytes, legenda_tamanho),
        ):
            if bruto is None:
                if tamanho_original is not None:
                    _falhar("conteudo_cache_invalido")
                candidatos.append((None, None))
                continue
            if not isinstance(bruto, bytes) or isinstance(tamanho_original, bool) or not isinstance(tamanho_original, int):
                _falhar("conteudo_cache_invalido")
            try:
                candidatos.append((bruto.decode("utf-8"), tamanho_original))
            except UnicodeDecodeError:
                _falhar("conteudo_cache_invalido")
        escolhido = next(
            ((valor, tamanho) for valor, tamanho in candidatos if isinstance(valor, str) and valor.strip()),
            None,
        )
        conteudo = escolhido[0] if escolhido else None
        if conteudo is None:
            omitidas_sem_texto += 1
            continue
        tamanho = escolhido[1]
        if not isinstance(tamanho, int) or tamanho > max_bytes_mensagem:
            omitidas_tamanho += 1
            continue
        if bytes_total + tamanho > max_bytes_total:
            truncada_bytes = True
            break
        if msg_id_bytes is None:
            if msg_id_tamanho is not None:
                _falhar("identidade_cache_invalida")
            msg_id = None
        elif (
            not isinstance(msg_id_bytes, bytes)
            or isinstance(msg_id_tamanho, bool)
            or not isinstance(msg_id_tamanho, int)
            or msg_id_tamanho > 1_024
        ):
            _falhar("identidade_cache_invalida")
        else:
            try:
                msg_id = msg_id_bytes.decode("utf-8")
            except UnicodeDecodeError:
                _falhar("identidade_cache_invalida")
            if not msg_id:
                _falhar("identidade_cache_invalida")
        if media_tipo_tamanho is not None and (
            isinstance(media_tipo_tamanho, bool)
            or not isinstance(media_tipo_tamanho, int)
            or media_tipo_tamanho > 256
        ):
            _falhar("conteudo_cache_invalido")
        mensagem_ref = None if msg_id is None else "msg_" + _referencia_opaca(msg_id)
        identidade_pendente = identidade_pendente or mensagem_ref is None
        bytes_total += tamanho
        try:
            timestamp = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        except (OverflowError, OSError, ValueError):
            _falhar("timestamp_cache_invalido")
        mensagens.append({
            "conversa_ref": conversa_ref,
            "mensagem_ref": mensagem_ref,
            "timestamp": timestamp,
            "texto": conteudo,
        })

    metadados = {
        "consultas_dados": 2,
        "validacoes_schema": 4,
        "linhas_consultadas": min(len(linhas), limite),
        "mensagens_processadas": len(mensagens),
        "omitidas_sem_texto": omitidas_sem_texto,
        "omitidas_tamanho": omitidas_tamanho,
        "omitidas_por_estado": omitidas_por_estado,
        "editadas_sem_historico": editadas_sem_historico,
        "truncada_quantidade": truncada_quantidade,
        "truncada_bytes": truncada_bytes,
        "identidade_pendente": identidade_pendente,
        "bytes_processados": bytes_total,
    }
    return {
        "documento": {
            "schema_version": SCHEMA_MENSAGENS,
            "cobertura": {
                "estado": "parcial",
                "fonte": "cache_wey_sqlite_somente_leitura",
                "captura_ativa_confirmada": False,
                "intervalo_inicio": inicio_utc.isoformat().replace("+00:00", "Z"),
                "intervalo_fim": fim_utc.isoformat().replace("+00:00", "Z"),
                "atestado": False,
                "detalhe_sanitizado": DETALHE_COBERTURA,
                "diagnostico_sanitizado": {
                    "omitidas_sem_texto": omitidas_sem_texto,
                    "omitidas_tamanho": omitidas_tamanho,
                    "omitidas_por_estado": omitidas_por_estado,
                    "editadas_sem_historico": editadas_sem_historico,
                    "truncada_quantidade": truncada_quantidade,
                    "truncada_bytes": truncada_bytes,
                },
                "identidade_pendente": identidade_pendente,
                "truncada": truncada_quantidade or truncada_bytes or omitidas_tamanho > 0,
            },
            "mensagens": mensagens,
        },
        "conversa_ref": conversa_ref,
        "metadados": metadados,
    }
