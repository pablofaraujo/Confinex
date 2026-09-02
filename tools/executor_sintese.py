#!/usr/bin/env python3
"""Executor da tarefa de síntese — conclui investigações do plano de controle.

Papel (incremento 5 do pós-janela): quando todas as tarefas-fonte de uma
investigação terminam, a síntese consolida o que foi publicado e encerra a
rodada. Este executor roda no host (como o planejador, com as credenciais do
ambiente): a síntese não usa credencial de adaptador — por desenho, o banco
deriva e confere a cobertura sozinho e recusa atestado para ela.

O que a v1 faz — e o que ela se RECUSA a fazer:
- materializa a linha da tarefa de síntese em ``investigacao_tarefas`` de
  forma idempotente, campo a campo igual ao item já selado em
  ``plano_tarefas`` (o gate do banco confere byte a byte);
- assume a síntese pela RPC oficial (só é assumível quando o plano está
  materializado e todas as fontes estão terminais com cobertura);
- SEM evidências utilizáveis (fontes vazias ou falhas), publica o resultado
  honesto: nenhuma alternativa, uma pendência aberta por campo obrigatório
  (mais a pendência de fonte quando a cobertura falhou) — o banco então
  conclui a investigação como ``evidencia_insuficiente``/``cobertura_incompleta``;
- COM evidências publicadas, a v1 ABORTA sem escrever: concluir descartando
  evidência seria mentir. A montagem de alternativas explicáveis é a v2,
  quando os adaptadores reais (wey, agronotas, ofx) existirem.

Dry-run é o padrão, com hash do plano; a execução exige ``--executar
--confirmacao <hash> --executor <nome>``. Escrita direta própria SÓ em
``investigacao_tarefas`` (a materialização); todo o resto passa pelas RPCs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping

try:  # execução a partir da raiz do repositório (CI, testes)
    from tools import investigacoes_revisao as biblioteca
except ImportError:  # execução direta (VPS/host)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import investigacoes_revisao as biblioteca  # type: ignore[no-redef]

VERSAO_EXECUTOR = "executor-sintese-v1.0.0"
ESCRITA_PROPRIA = frozenset({"investigacao_tarefas"})
RPCS_PERMITIDAS = frozenset({
    "assumir_tarefa_investigacao",
    "publicar_resultado_tarefa_investigacao",
})
COBERTURAS_SUCESSO = frozenset({"completa", "vazio_com_cobertura"})
ESTADOS_TERMINAIS_FONTE = frozenset({"concluida", "cancelada", "obsoleta"})


def _sha(objeto: Any) -> str:
    canonico = json.dumps(objeto, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"))
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Derivações puras (testáveis sem rede)
# ---------------------------------------------------------------------------


def linha_tarefa_sintese(investigacao: Mapping[str, Any]) -> dict[str, Any]:
    """Deriva a linha idempotente da síntese a partir do plano imutável."""
    itens = [item for item in (investigacao.get("plano_tarefas") or [])
             if item.get("adaptador") == "sintese"]
    if len(itens) != 1:
        raise ValueError("plano sem exatamente uma tarefa de síntese")
    item = itens[0]
    chave = biblioteca.chave_estavel(
        "tar", str(investigacao["chave_idempotencia"]), "sintese",
        str(investigacao["policy_version"]),
    )
    return {
        "id": biblioteca.id_deterministico("tarefa", chave),
        "investigacao_id": str(investigacao["id"]),
        "chave_idempotencia": chave,
        "plano_item_ref": item["plano_item_ref"],
        "adaptador": "sintese",
        "adaptador_version": item["adaptador_version"],
        "consulta_ref": item["consulta_ref"],
        "consulta_schema_version": item["consulta_schema_version"],
        "consulta_spec": item["consulta_spec"],
        "consulta_canonico": item["consulta_canonico"],
        "consulta_hash": item["consulta_hash"],
    }


def cobertura_das_fontes(tarefas_fonte: list[Mapping[str, Any]]) -> str:
    """Replica a derivação do banco (``investigacao_cobertura_sintese``)."""
    if not tarefas_fonte:
        raise ValueError("investigação sem tarefas-fonte")
    for tarefa in tarefas_fonte:
        if str(tarefa.get("estado_execucao") or "") not in ESTADOS_TERMINAIS_FONTE:
            raise ValueError("fonte ainda não terminal — síntese prematura")
        if not tarefa.get("estado_cobertura"):
            raise ValueError("fonte terminal sem cobertura registrada")
    coberturas = [str(t["estado_cobertura"]) for t in tarefas_fonte]
    if any(c not in COBERTURAS_SUCESSO for c in coberturas):
        return "cobertura_incompleta"
    if all(c == "vazio_com_cobertura" for c in coberturas):
        return "vazio_com_cobertura"
    return "completa"


def montar_resultado_sintese(
    investigacao: Mapping[str, Any],
    tarefas_fonte: list[Mapping[str, Any]],
    evidencias: list[Mapping[str, Any]],
    tarefa_sintese_id: str,
) -> dict[str, Any]:
    """Monta o publicável da síntese v1 (somente o caso sem evidências)."""
    if evidencias:
        raise ValueError(
            "EVIDENCIAS_EXIGEM_SINTESE_V2: há evidências publicadas e a v1 "
            "não monta alternativas — não concluo descartando evidência"
        )
    cobertura = cobertura_das_fontes(tarefas_fonte)
    campos = [str(c) for c in (investigacao.get("campos_obrigatorios") or [])]
    resultado = biblioteca.resultado_investigacao(
        {}, cobertura=cobertura, campos_obrigatorios=campos,
    )
    pendencias_internas = biblioteca._pendencias_planejadas(
        investigacao_id=str(investigacao["id"]),
        tarefa_sintese_id=tarefa_sintese_id,
        chave_investigacao=str(investigacao["chave_idempotencia"]),
        resultado=resultado,
        cobertura=cobertura,
        confianca={},
        campos_obrigatorios=campos,
    )
    pendencias = [
        {
            "id_logico": p["id_logico"],
            "chave_idempotencia": p["chave_idempotencia"],
            "tipo": p["tipo"],
            "campo": p["campo"],
            "fonte_tipo": None,
            "descricao_sanitizada": p["descricao_sanitizada"],
            "estado": p["estado"],
        }
        for p in pendencias_internas
    ]
    resumo = (
        f"sintese sem evidencias: {len(pendencias)} pendencia(s) aberta(s) "
        "para revisao humana"
    )
    return {
        "estado_cobertura": cobertura,
        "estado_resultado": resultado,
        "bundle": {"evidencias": [], "alternativas": [],
                   "pendencias": pendencias, "ligacoes": []},
        "resumo_sanitizado": resumo,
        "erro_codigo": None,
        "erro_sanitizado": None,
    }


def plano_execucao(
    investigacao: Mapping[str, Any],
    linha_sintese: Mapping[str, Any],
    linha_ja_existe: bool,
    resultado: Mapping[str, Any],
) -> dict[str, Any]:
    plano = {
        "executor_version": VERSAO_EXECUTOR,
        "investigacao_id": str(investigacao["id"]),
        "tarefa_sintese_id": str(linha_sintese["id"]),
        "materializar": not linha_ja_existe,
        "estado_cobertura": resultado["estado_cobertura"],
        "estado_resultado": resultado["estado_resultado"],
        "pendencias": [
            {"tipo": p["tipo"], "campo": p["campo"]}
            for p in resultado["bundle"]["pendencias"]
        ],
    }
    plano["confirmacao"] = _sha(plano)
    return plano


# ---------------------------------------------------------------------------
# Cliente PostgREST (allowlists fechadas; escrita nunca repetida)
# ---------------------------------------------------------------------------


class ClienteSintese:
    def __init__(self, url: str, chave: str, timeout: int = 20) -> None:
        if not url or not chave:
            raise ValueError("configuracao_incompleta")
        self.url = url.rstrip("/")
        self.chave = chave
        self.timeout = max(1, min(int(timeout), 20))

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        base = {"apikey": self.chave, "Authorization": f"Bearer {self.chave}"}
        base.update(extra or {})
        return base

    def _get(self, tabela: str, consulta: dict[str, str]) -> list[dict[str, Any]]:
        requisicao = urllib.request.Request(
            f"{self.url}/rest/v1/{tabela}?" + urllib.parse.urlencode(consulta),
            method="GET", headers=self._headers(),
        )
        try:
            with urllib.request.urlopen(requisicao, timeout=self.timeout) as r:
                linhas = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"leitura de {tabela} falhou com HTTP {exc.code}"
            ) from exc
        if not isinstance(linhas, list):
            raise RuntimeError(f"resposta inválida de {tabela}")
        return linhas

    def inserir(self, tabela: str, payload: Mapping[str, Any]) -> str:
        """Um POST, sem retentativa; conflito idempotente vira 'ja_existia'."""
        if tabela not in ESCRITA_PROPRIA:
            raise ValueError(f"escrita não permitida ao executor: {tabela}")
        requisicao = urllib.request.Request(
            f"{self.url}/rest/v1/{tabela}",
            data=json.dumps(dict(payload), ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers=self._headers({
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            }),
        )
        try:
            with urllib.request.urlopen(requisicao, timeout=self.timeout) as r:
                if r.status in {200, 201, 204}:
                    return "criado"
                raise RuntimeError(f"HTTP inesperado em {tabela}: {r.status}")
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                return "ja_existia"
            raise RuntimeError(
                f"escrita em {tabela} falhou com HTTP {exc.code}"
            ) from exc

    def rpc(self, nome: str, payload: Mapping[str, Any]) -> Any:
        if nome not in RPCS_PERMITIDAS:
            raise ValueError(f"rpc_fora_da_allowlist:{nome}")
        requisicao = urllib.request.Request(
            f"{self.url}/rest/v1/rpc/{nome}",
            data=json.dumps(dict(payload), ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers=self._headers({"Content-Type": "application/json"}),
        )
        try:
            with urllib.request.urlopen(requisicao, timeout=self.timeout) as r:
                corpo = r.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"rpc_{nome}_http_{exc.code}") from exc
        return json.loads(corpo) if corpo else None

    def carregar(self, investigacao_id: str) -> dict[str, Any]:
        investigacoes = self._get("investigacoes_revisao", {
            "select": ("id,chave_idempotencia,policy_version,"
                       "campos_obrigatorios,plano_tarefas,estado_execucao"),
            "id": "eq." + investigacao_id,
        })
        if len(investigacoes) != 1:
            raise RuntimeError("investigação não encontrada")
        tarefas = self._get("investigacao_tarefas", {
            "select": ("id,adaptador,estado_execucao,estado_cobertura,"
                       "chave_idempotencia"),
            "investigacao_id": "eq." + investigacao_id,
        })
        evidencias = self._get("investigacao_evidencias", {
            "select": "id",
            "investigacao_id": "eq." + investigacao_id,
        })
        return {
            "investigacao": investigacoes[0],
            "tarefas_fonte": [t for t in tarefas
                              if t.get("adaptador") != "sintese"],
            "tarefas_sintese": [t for t in tarefas
                                if t.get("adaptador") == "sintese"],
            "evidencias": evidencias,
        }


def cliente_do_ambiente() -> ClienteSintese:
    return ClienteSintese(
        os.environ.get("SUPABASE_URL")
        or os.environ.get("CONFINEX_DB_URL") or "",
        os.environ.get("SUPABASE_SERVICE_KEY")
        or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or os.environ.get("CONFINEX_DB_KEY") or "",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Executor de síntese (dry-run por padrão; v1 só sem evidências)"
    )
    parser.add_argument("--versao", action="store_true")
    parser.add_argument("--investigacao", help="id da investigação alvo")
    parser.add_argument("--lease-segundos", type=int, default=120)
    parser.add_argument("--executar", action="store_true")
    parser.add_argument("--confirmacao")
    parser.add_argument("--executor", dest="executor",
                        help="nome de quem executa (obrigatório no --executar)")
    args = parser.parse_args(argv)
    if args.versao:
        print(VERSAO_EXECUTOR)
        return 0
    if not args.investigacao:
        print("ERRO: --investigacao é obrigatório", file=sys.stderr)
        return 2

    cliente = cliente_do_ambiente()
    estado = cliente.carregar(args.investigacao)
    investigacao = estado["investigacao"]
    linha = linha_tarefa_sintese(investigacao)
    ja_existe = any(
        str(t.get("chave_idempotencia")) == linha["chave_idempotencia"]
        for t in estado["tarefas_sintese"]
    )
    try:
        resultado = montar_resultado_sintese(
            investigacao, estado["tarefas_fonte"], estado["evidencias"],
            linha["id"],
        )
    except ValueError as erro:
        print(f"ERRO: {erro}", file=sys.stderr)
        return 2
    plano = plano_execucao(investigacao, linha, ja_existe, resultado)
    print(json.dumps(plano, ensure_ascii=False, indent=1))

    if not args.executar:
        print("\nDRY-RUN: nada foi gravado. Para executar: --executar "
              f"--confirmacao {plano['confirmacao']} --executor <nome>",
              file=sys.stderr)
        return 0
    if args.confirmacao != plano["confirmacao"]:
        print("ERRO: confirmação não confere com o plano atual",
              file=sys.stderr)
        return 2
    if not (args.executor or "").strip():
        print("ERRO: --executor é obrigatório na execução", file=sys.stderr)
        return 2

    if plano["materializar"]:
        modo = cliente.inserir("investigacao_tarefas", linha)
        print(f"tarefa de síntese: {modo}", file=sys.stderr)
    assumida = cliente.rpc("assumir_tarefa_investigacao", {
        "p_adaptador": "sintese",
        "p_executor": args.executor.strip(),
        "p_lease_segundos": max(30, min(args.lease_segundos, 900)),
    })
    if not assumida:
        print("ERRO: nenhuma síntese assumível (fontes não terminais, plano "
              "não materializado ou lease de outro executor)", file=sys.stderr)
        return 2
    if str(assumida.get("investigacao_id")) != str(investigacao["id"]):
        print("ERRO: a síntese assumida pertence a outra investigação "
              f"({assumida.get('investigacao_id')}); nada publicado — o lease "
              "expira sozinho e a tarefa volta à fila", file=sys.stderr)
        return 2
    resposta = cliente.rpc("publicar_resultado_tarefa_investigacao", {
        "p_tarefa_id": str(assumida["id"]),
        "p_lease_token": str(assumida["lease_token"]),
        "p_fencing_token": int(assumida["fencing_token"]),
        "p_estado_cobertura": resultado["estado_cobertura"],
        "p_estado_resultado": resultado["estado_resultado"],
        "p_bundle": resultado["bundle"],
        "p_atestado_cobertura": None,
        "p_resumo_sanitizado": resultado["resumo_sanitizado"],
    })
    print(json.dumps({"publicado": True, "resposta": resposta},
                     ensure_ascii=False, indent=1), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
