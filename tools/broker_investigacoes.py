#!/usr/bin/env python3
"""Broker isolado das investigações proativas — o artefato atestável do gate.

Papel: é o ÚNICO processo autorizado a portar a credencial ``service_role``
fora do banco. Os adaptadores de fonte nunca tocam o Supabase: falam com o
broker por um socket local restrito, e o broker encaminha exclusivamente as
RPCs fechadas da fundação (``assumir_tarefa_investigacao``,
``adiar_tarefa_investigacao``, ``publicar_resultado_tarefa_investigacao`` e a
sonda ``saude_investigacoes_proativas``). Não existe caminho de escrita em
``compras``, ``vendas``, ``abates``, ``pesagens_caderno`` nem em qualquer
outra tabela operacional — nem aqui, nem nas RPCs que ele alcança.

Identidade atestável exigida pelo gate da migração 202608290002:

- ``--versao`` imprime a versão do broker (``broker_version`` da atestação);
- ``--teste-capacidades`` imprime o relatório canônico e determinístico do
  autoteste; o sha256 desse texto é o ``teste_capacidades_hash``;
- o sha256 do próprio arquivo, exatamente como implantado, é o
  ``broker_artefato_hash``.

Segredos: a credencial ``service_role`` entra apenas por variável de ambiente
(injetada pelo cofre local da VPS) e jamais é impressa. ``--emitir-credencial``
gera o segredo HMAC de um adaptador localmente, grava-o num arquivo 600 e
mostra somente o ``chave_id`` e a janela — nunca o valor. Nenhum segredo
existe neste arquivo nem no repositório.

Modo ``--dry-run``: o serviço aceita ``assumir`` e ``adiar`` (lease é estado
de execução transitório, já exercido na homologação de sombra), mas recusa
``publicar`` incondicionalmente — nenhuma evidência, alternativa, pendência
ou resultado é persistido em dry-run.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac as hmac_mod
import json
import os
import secrets
import socket
import sys
from datetime import datetime, timedelta, timezone
import urllib.error
import urllib.request
from typing import Any, Callable, Mapping

try:  # execução a partir da raiz do repositório (CI, testes)
    from tools import atestar_cobertura_adaptador as atestar
    from tools import investigacoes_revisao as biblioteca
except ImportError:  # execução direta na VPS (python3 tools/broker_investigacoes.py)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import atestar_cobertura_adaptador as atestar  # type: ignore[no-redef]
    import investigacoes_revisao as biblioteca  # type: ignore[no-redef]

VERSAO_BROKER = "broker-v1.0.0"
SCHEMA_CAPACIDADES = "broker-capacidades-v1"

RPCS_PERMITIDAS = frozenset({
    "assumir_tarefa_investigacao",
    "adiar_tarefa_investigacao",
    "publicar_resultado_tarefa_investigacao",
    "saude_investigacoes_proativas",
})
RPCS_DRY_RUN = frozenset({
    "assumir_tarefa_investigacao",
    "adiar_tarefa_investigacao",
    "saude_investigacoes_proativas",
})
ADAPTADORES_REGISTRADOS = frozenset({
    "agronotas", "ofx", "ima", "telegram", "wey", "outro",
})
OPS_SOCKET = frozenset({"assumir", "adiar", "publicar", "sonda"})
TABELA_CREDENCIAIS = "investigacao_adaptador_credenciais"


def _falha(mensagem: str) -> "SystemExit":
    print(f"ERRO: {mensagem}", file=sys.stderr)
    return SystemExit(2)


class ClienteBroker:
    """Cliente PostgREST de allowlist fechada; escrita nunca é repetida."""

    def __init__(self, url: str, chave: str, timeout: int = 20) -> None:
        if not url or not chave:
            raise ValueError("configuracao_incompleta")
        self.url = url.rstrip("/")
        self.chave = chave
        self.timeout = max(1, min(int(timeout), 20))

    def rpc(self, nome: str, payload: Mapping[str, Any]) -> Any:
        if nome not in RPCS_PERMITIDAS:
            raise ValueError(f"rpc_fora_da_allowlist:{nome}")
        requisicao = urllib.request.Request(
            f"{self.url}/rest/v1/rpc/{nome}",
            data=json.dumps(dict(payload), ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "apikey": self.chave,
                "Authorization": f"Bearer {self.chave}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(requisicao, timeout=self.timeout) as resposta:
                corpo = resposta.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            # Sem retentativa e sem eco do corpo: o payload pode conter
            # referências de evidência que não pertencem a log de erro.
            raise RuntimeError(f"rpc_{nome}_http_{exc.code}") from exc
        return json.loads(corpo) if corpo else None

    def inserir_credencial(self, payload: Mapping[str, Any]) -> None:
        requisicao = urllib.request.Request(
            f"{self.url}/rest/v1/{TABELA_CREDENCIAIS}",
            data=json.dumps(dict(payload), ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "apikey": self.chave,
                "Authorization": f"Bearer {self.chave}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
        )
        try:
            with urllib.request.urlopen(requisicao, timeout=self.timeout) as resposta:
                if resposta.status not in {200, 201, 204}:
                    raise RuntimeError(
                        f"credencial_http_{resposta.status}"
                    )
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"credencial_http_{exc.code}") from exc


def cliente_do_ambiente() -> ClienteBroker:
    url = os.environ.get("SUPABASE_URL") or os.environ.get("CONFINEX_DB_URL") or ""
    chave = (
        os.environ.get("SUPABASE_SERVICE_KEY")
        or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or os.environ.get("CONFINEX_DB_KEY")
        or ""
    )
    try:
        return ClienteBroker(url, chave)
    except ValueError as exc:
        raise _falha(
            "defina SUPABASE_URL e SUPABASE_SERVICE_KEY no ambiente (cofre local)"
        ) from exc


# ---------------------------------------------------------------------------
# Autoteste de capacidades — relatório canônico e determinístico
# ---------------------------------------------------------------------------

_SEGREDO_DE_TESTE = bytes(32)  # vetor público de teste; jamais uma credencial


def _sha256_texto(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def _verificacoes_capacidades() -> list[dict[str, Any]]:
    verificacoes: list[dict[str, Any]] = []

    def registrar(nome: str, executor: Callable[[], str]) -> None:
        try:
            detalhe = executor()
            verificacoes.append({"nome": nome, "ok": True, "detalhe": detalhe})
        except Exception as exc:  # noqa: BLE001 — o relatório é o diagnóstico
            verificacoes.append({
                "nome": nome, "ok": False,
                "detalhe": f"{type(exc).__name__}:{exc}",
            })

    def caso_canonico() -> str:
        vetores = [
            {"b": 1, "a": [1.5, None, True], "ç": {"z": "", "á": 0}},
            {"numero": 10.10, "texto": "linha\ncom\tcontrole"},
            [],
            {},
        ]
        texto = "|".join(
            atestar.json_canonico_postgres(vetor) for vetor in vetores
        )
        return _sha256_texto(texto)

    def caso_hash_pedido() -> str:
        return atestar.hash_pedido(
            estado_cobertura="completa",
            estado_resultado="confirmado",
            bundle={"evidencias": [{"fato": "vetor"}]},
            resumo_sanitizado="vetor de teste",
            erro_codigo=None,
            erro_sanitizado=None,
        )

    def caso_atestado_hmac() -> str:
        envelope = atestar.assinar_atestado_cobertura(
            segredo=_SEGREDO_DE_TESTE,
            chave_id="key_vetor-de-teste",
            adaptador="outro",
            adaptador_version="v1",
            artefato_hash="c" * 64,
            familia_fonte="auxiliar",
            consulta_hash="f" * 64,
            consulta_ref="qref_" + "0" * 32,
            tarefa_id="00000000-0000-4000-8000-000000000001",
            investigacao_id="00000000-0000-4000-8000-000000000002",
            lease_token="00000000-0000-4000-8000-000000000003",
            fencing_token=1,
            estado_cobertura="completa",
            estado_resultado="confirmado",
            bundle={"evidencias": [{"fato": "vetor"}]},
            inicio_confirmado=True,
            fim_confirmado=True,
            paginas_confirmadas=1,
            registros_confirmados=1,
            paginacao_modo="nao_paginado",
            artefato_cobertura_tipo="snapshot_fonte",
            cursor_final_hash=None,
            snapshot_fonte_hash="e" * 64,
            resumo_sanitizado="vetor de teste",
        )
        recomputado = hmac_mod.new(
            _SEGREDO_DE_TESTE,
            atestar.json_canonico_postgres(
                {k: v for k, v in envelope.items() if k != "hmac"}
            ).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac_mod.compare_digest(recomputado, envelope["hmac"]):
            raise ValueError("hmac_nao_reproduzivel")
        return envelope["hmac"]

    def caso_sanitizacao() -> str:
        sujo = {
            "senha": "não pode sair",
            "token": "não pode sair",
            "authorization": "não pode sair",
            "valor_total": 1234.56,
            "observacao": "texto comum",
        }
        limpo = biblioteca.sanitizar_payload(sujo)
        if any(chave in limpo for chave in ("senha", "token", "authorization")):
            raise ValueError("segredo_atravessou_sanitizacao")
        if "valor_total" not in limpo or "observacao" not in limpo:
            raise ValueError("sanitizacao_removeu_campo_legitimo")
        return _sha256_texto(atestar.json_canonico_postgres(limpo))

    def caso_assunto_protegido() -> str:
        assunto = biblioteca.normalizar_assunto(
            "Conferir NFe 12345678901234567890123456789012345678901234"
        )
        titulo = assunto["titulo"]
        if "12345678901234567890123456789012345678901234" in titulo:
            raise ValueError("identificador_atravessou_protecao")
        return _sha256_texto(atestar.json_canonico_postgres(assunto))

    def caso_consulta_deterministica() -> str:
        primeira = biblioteca.contrato_consulta("valor pix 1234; romaneio")
        segunda = biblioteca.contrato_consulta("valor pix 1234; romaneio")
        if primeira["consulta_hash"] != segunda["consulta_hash"]:
            raise ValueError("consulta_nao_deterministica")
        return primeira["consulta_hash"]

    def caso_fonte_selada() -> str:
        try:
            biblioteca.FonteAdaptadorSelada({"qualquer": 1}, object())
        except Exception:
            return "construcao_direta_recusada"
        raise ValueError("fonte_selada_sem_capacidade_aceita")

    def caso_rpc_allowlist() -> str:
        cliente = ClienteBroker("https://exemplo.invalid", "chave-sintetica")
        try:
            cliente.rpc("delete_tudo", {})
        except ValueError as exc:
            return str(exc)
        raise ValueError("rpc_fora_da_allowlist_aceita")

    def caso_dry_run() -> str:
        resposta = tratar_pedido(
            {"op": "publicar", "p_tarefa_id": "x"},
            adaptador="outro",
            executor="autoteste",
            dry_run=True,
            rpc=lambda nome, payload: (_ for _ in ()).throw(
                AssertionError("dry_run_encaminhou_publicacao")
            ),
        )
        if resposta.get("erro") != "dry_run_nao_publica":
            raise ValueError("dry_run_nao_recusou_publicacao")
        return "publicacao_recusada_em_dry_run"

    registrar("json_canonico_postgres", caso_canonico)
    registrar("hash_pedido", caso_hash_pedido)
    registrar("atestado_hmac_reproduzivel", caso_atestado_hmac)
    registrar("sanitizacao_remove_segredos", caso_sanitizacao)
    registrar("assunto_protege_identificadores", caso_assunto_protegido)
    registrar("consulta_deterministica", caso_consulta_deterministica)
    registrar("fonte_selada_exige_capacidade", caso_fonte_selada)
    registrar("rpc_allowlist_fechada", caso_rpc_allowlist)
    registrar("dry_run_recusa_publicacao", caso_dry_run)
    return verificacoes


def relatorio_capacidades() -> tuple[str, bool]:
    """Relatório canônico (sem relógio, sem ambiente) e veredito agregado."""
    verificacoes = _verificacoes_capacidades()
    relatorio = {
        "schema_version": SCHEMA_CAPACIDADES,
        "broker_version": VERSAO_BROKER,
        "verificacoes": verificacoes,
        "todas_ok": all(item["ok"] for item in verificacoes),
    }
    return atestar.json_canonico_postgres(relatorio), bool(relatorio["todas_ok"])


# ---------------------------------------------------------------------------
# Servidor local para adaptadores
# ---------------------------------------------------------------------------


def tratar_pedido(
    pedido: Mapping[str, Any],
    *,
    adaptador: str,
    executor: str,
    dry_run: bool,
    rpc: Callable[[str, Mapping[str, Any]], Any],
) -> dict[str, Any]:
    """Traduz um pedido do adaptador em, no máximo, uma RPC da allowlist.

    A identidade (adaptador/executor) é SEMPRE a do servidor: o cliente não
    escolhe agir por outro adaptador. Em dry-run, publicação é recusada antes
    de qualquer I/O.
    """
    op = str(pedido.get("op") or "")
    if op not in OPS_SOCKET:
        return {"erro": f"op_desconhecida:{op or 'vazia'}"}
    try:
        if op == "sonda":
            return {"ok": True, "saude": rpc("saude_investigacoes_proativas", {})}
        if op == "assumir":
            lease = int(pedido.get("lease_segundos") or 120)
            return {"ok": True, "tarefa": rpc("assumir_tarefa_investigacao", {
                "p_adaptador": adaptador,
                "p_executor": executor,
                "p_lease_segundos": max(30, min(lease, 900)),
            })}
        if op == "adiar":
            return {"ok": True, "adiada": rpc("adiar_tarefa_investigacao", {
                "p_tarefa_id": str(pedido.get("p_tarefa_id") or ""),
                "p_lease_token": str(pedido.get("p_lease_token") or ""),
                "p_fencing_token": int(pedido.get("p_fencing_token") or 0),
                "p_executor": executor,
                "p_atraso_segundos": int(pedido.get("p_atraso_segundos") or 60),
                "p_erro_codigo": str(pedido.get("p_erro_codigo") or "adiado"),
                "p_erro_sanitizado": str(pedido.get("p_erro_sanitizado") or ""),
            })}
        # op == "publicar"
        if dry_run:
            return {"erro": "dry_run_nao_publica"}
        atestado = pedido.get("p_atestado_cobertura")
        if not isinstance(atestado, Mapping) or not isinstance(
            atestado.get("hmac"), str
        ):
            return {"erro": "atestado_cobertura_obrigatorio"}
        payload = {
            "p_tarefa_id": str(pedido.get("p_tarefa_id") or ""),
            "p_lease_token": str(pedido.get("p_lease_token") or ""),
            "p_fencing_token": int(pedido.get("p_fencing_token") or 0),
            "p_estado_cobertura": str(pedido.get("p_estado_cobertura") or ""),
            "p_estado_resultado": str(pedido.get("p_estado_resultado") or ""),
            "p_bundle": pedido.get("p_bundle") or {},
            "p_atestado_cobertura": dict(atestado),
            "p_resumo_sanitizado": pedido.get("p_resumo_sanitizado"),
            "p_erro_codigo": pedido.get("p_erro_codigo"),
            "p_erro_sanitizado": pedido.get("p_erro_sanitizado"),
        }
        return {"ok": True, "resultado": rpc(
            "publicar_resultado_tarefa_investigacao", payload
        )}
    except (RuntimeError, ValueError, TypeError) as exc:
        return {"erro": str(exc)}


def servir(
    caminho_socket: str,
    *,
    adaptador: str,
    executor: str,
    dry_run: bool,
    rpc: Callable[[str, Mapping[str, Any]], Any],
    limite_pedidos: int | None = None,
) -> int:
    """Atende pedidos JSON (um por conexão) no socket local restrito."""
    if adaptador not in ADAPTADORES_REGISTRADOS:
        raise _falha(f"adaptador não registrado: {adaptador}")
    if os.path.exists(caminho_socket):
        raise _falha(f"socket já existe (outro broker?): {caminho_socket}")
    servidor = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        mascara_anterior = os.umask(0o177)  # o socket nasce 600
        try:
            servidor.bind(caminho_socket)
        finally:
            os.umask(mascara_anterior)
        servidor.listen(8)
        atendidos = 0
        while limite_pedidos is None or atendidos < limite_pedidos:
            conexao, _ = servidor.accept()
            with conexao:
                try:
                    bruto = conexao.makefile("rb").readline(1024 * 1024)
                    pedido = json.loads(bruto.decode("utf-8"))
                    if not isinstance(pedido, dict):
                        raise ValueError("pedido_nao_e_objeto")
                except (ValueError, UnicodeDecodeError) as exc:
                    resposta = {"erro": f"pedido_invalido:{type(exc).__name__}"}
                else:
                    resposta = tratar_pedido(
                        pedido, adaptador=adaptador, executor=executor,
                        dry_run=dry_run, rpc=rpc,
                    )
                conexao.sendall(
                    (json.dumps(resposta, ensure_ascii=False) + "\n").encode("utf-8")
                )
            atendidos += 1
        return atendidos
    finally:
        servidor.close()
        if os.path.exists(caminho_socket):
            os.unlink(caminho_socket)


# ---------------------------------------------------------------------------
# Emissão de credencial de adaptador
# ---------------------------------------------------------------------------


def emitir_credencial(
    cliente: ClienteBroker,
    *,
    adaptador: str,
    adaptador_version: str,
    chave_id: str,
    emite_minutos: int,
    aceita_minutos: int,
    diretorio_saida: str,
) -> str:
    """Gera o segredo localmente, registra a credencial e guarda o arquivo 600.

    O segredo NUNCA é impresso nem devolvido ao chamador por stdout; o retorno
    é apenas o caminho do arquivo local.
    """
    if adaptador not in ADAPTADORES_REGISTRADOS:
        raise ValueError(f"adaptador não registrado: {adaptador}")
    if not (1 <= emite_minutos <= 24 * 60) or aceita_minutos < emite_minutos:
        raise ValueError("janela de emissão/aceitação inválida")
    os.makedirs(diretorio_saida, mode=0o700, exist_ok=True)
    destino = os.path.join(diretorio_saida, f"{chave_id}.segredo")
    if os.path.exists(destino):
        raise ValueError(f"segredo já existe, não sobrescrevo: {destino}")
    segredo = secrets.token_bytes(32)
    agora = datetime.now(timezone.utc)
    cliente.inserir_credencial({
        "adaptador": adaptador,
        "adaptador_version": adaptador_version,
        "chave_id": chave_id,
        "chave_hmac": "\\x" + segredo.hex(),
        "valida_desde": agora.isoformat(),
        "emite_ate": (agora + timedelta(minutes=int(emite_minutos))).isoformat(),
        "aceita_ate": (agora + timedelta(minutes=int(aceita_minutos))).isoformat(),
    })
    descritor = os.open(destino, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descritor, "w", encoding="utf-8") as arquivo:
        arquivo.write(segredo.hex() + "\n")
    return destino


# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Broker isolado das investigações proativas"
    )
    parser.add_argument("--versao", action="store_true")
    parser.add_argument("--teste-capacidades", action="store_true")
    parser.add_argument("--hash-capacidades", action="store_true")
    parser.add_argument("--sonda", action="store_true",
                        help="chama a RPC de saúde (somente leitura)")
    parser.add_argument("--consumir", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--adaptador")
    parser.add_argument("--executor", default="broker-vps")
    parser.add_argument("--socket", dest="caminho_socket")
    parser.add_argument("--limite-pedidos", type=int)
    parser.add_argument("--emitir-credencial", metavar="ADAPTADOR")
    parser.add_argument("--adaptador-version", default="v1")
    parser.add_argument("--chave-id")
    parser.add_argument("--emite-minutos", type=int, default=30)
    parser.add_argument("--aceita-minutos", type=int, default=60)
    parser.add_argument("--saida", default=os.path.expanduser("~/.confinex_broker"))
    args = parser.parse_args(argv)

    if args.versao:
        print(VERSAO_BROKER)
        return 0
    if args.teste_capacidades or args.hash_capacidades:
        texto, todas_ok = relatorio_capacidades()
        if args.hash_capacidades:
            print(_sha256_texto(texto))
        else:
            print(texto)
        return 0 if todas_ok else 1
    if args.sonda:
        cliente = cliente_do_ambiente()
        saude = cliente.rpc("saude_investigacoes_proativas", {})
        print(json.dumps(saude, ensure_ascii=False))
        return 0
    if args.emitir_credencial:
        cliente = cliente_do_ambiente()
        chave_id = args.chave_id or f"key_{secrets.token_hex(8)}"
        try:
            destino = emitir_credencial(
                cliente,
                adaptador=args.emitir_credencial,
                adaptador_version=args.adaptador_version,
                chave_id=chave_id,
                emite_minutos=args.emite_minutos,
                aceita_minutos=args.aceita_minutos,
                diretorio_saida=args.saida,
            )
        except ValueError as exc:
            raise _falha(str(exc)) from exc
        print(json.dumps({
            "chave_id": chave_id,
            "adaptador": args.emitir_credencial,
            "adaptador_version": args.adaptador_version,
            "emite_minutos": args.emite_minutos,
            "aceita_minutos": args.aceita_minutos,
            "segredo_em": destino,
        }, ensure_ascii=False))
        return 0
    if args.consumir:
        if not args.adaptador or not args.caminho_socket:
            raise _falha("--consumir exige --adaptador e --socket")
        cliente = cliente_do_ambiente()
        atendidos = servir(
            args.caminho_socket,
            adaptador=args.adaptador,
            executor=args.executor,
            dry_run=bool(args.dry_run),
            rpc=cliente.rpc,
            limite_pedidos=args.limite_pedidos,
        )
        print(json.dumps({"pedidos_atendidos": atendidos}, ensure_ascii=False))
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
