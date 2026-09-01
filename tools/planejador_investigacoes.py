#!/usr/bin/env python3
"""Planejador de investigações proativas — dry-run por padrão, limite pequeno.

Papel (passo 9 de docs/investigacoes-proativas.md): olhar os rascunhos abertos
da fila de Revisões e propor, para cada um ainda não investigado, UMA
investigação com plano mínimo — uma tarefa de fonte no adaptador ``outro`` e a
tarefa de síntese obrigatória. Nada além do plano de controle é tocado:
o planejador escreve somente em ``investigacoes_revisao`` e
``investigacao_tarefas`` (as duas únicas tabelas em que o ``service_role``
tem INSERT na fundação), nunca em tabela operacional, staging ou Revisões.

Segurança e idempotência:
- dry-run é o padrão e não grava nada; a execução exige ``--executar``,
  ``--limite`` explícito e ``--confirmacao`` com o hash do plano impresso
  pelo dry-run (mesmo padrão das demais ferramentas do repositório);
- as chaves vêm de ``chaves_investigacao`` da biblioteca: reexecutar não
  duplica (conflito de chave idempotente é contado como "já existia");
- os specs de consulta passam pela normalização/sanitização da biblioteca —
  nenhum payload bruto, contato ou credencial entra no plano;
- escritas nunca são repetidas automaticamente (POST único por registro).

Efeito operacional consciente: criar uma investigação BLOQUEIA a promoção do
rascunho investigado até ela concluir e ser anexada (é o contrato da tela).
Por isso o limite pequeno e o gate humano por execução.
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
    from tools import atestar_cobertura_adaptador as atestar
    from tools import investigacoes_revisao as biblioteca
except ImportError:  # execução direta (VPS/container)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import atestar_cobertura_adaptador as atestar  # type: ignore[no-redef]
    import investigacoes_revisao as biblioteca  # type: ignore[no-redef]

VERSAO_PLANEJADOR = "planejador-v1.0.0"
ADAPTADOR_FONTE = "outro"
ADAPTADOR_FONTE_VERSION = "v1"
STATUS_DRAFT_PLANEJAVEIS = frozenset({
    "rascunho", "aguardando_confirmacao", "confirmado_telegram", "em_revisao",
})
POLITICA_POR_ENTIDADE = {
    "compras": "compra",
    "vendas": "venda",
    "pesagens_caderno": "pesagem",
    "abates": "abate",
}
TABELAS_ESCRITA = frozenset({"investigacoes_revisao", "investigacao_tarefas"})


def _sha256(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def _canonico(valor: Any) -> str:
    return atestar.json_canonico_postgres(valor)


# ---------------------------------------------------------------------------
# Derivações puras (testáveis sem rede)
# ---------------------------------------------------------------------------


def assunto_do_draft(draft: Mapping[str, Any]) -> dict[str, Any]:
    tipo = POLITICA_POR_ENTIDADE.get(
        str(draft.get("entidade_final_tipo") or ""), "revisao"
    )
    titulo = str(
        draft.get("codigo_sugerido")
        or draft.get("tipo_operacao")
        or "revisao"
    )
    return biblioteca.normalizar_assunto({
        "tipo": tipo,
        "titulo": f"Investigação do rascunho {titulo}",
        "referencia": str(draft.get("codigo_sugerido") or ""),
        "contexto_nome": str(draft.get("contexto_nome") or ""),
    })


def origem_do_draft(draft: Mapping[str, Any]) -> dict[str, Any]:
    return biblioteca.normalizar_origem({
        "canal": str(draft.get("origem_canal") or "revisoes"),
        "conversa_id": draft.get("origem_conversa_id"),
        "mensagem_id": draft.get("origem_mensagem_id"),
        "linhagem": "operation_draft",
    })


def fingerprint_do_draft(draft: Mapping[str, Any]) -> str:
    retrato = {
        "operation_draft_id": str(draft.get("id") or ""),
        "atualizado_em": str(draft.get("atualizado_em") or ""),
        "dados_extraidos": draft.get("dados_extraidos") or {},
        "campos_pendentes": draft.get("campos_pendentes") or {},
    }
    return _sha256(_canonico(retrato))


def consulta_fonte_do_draft(
    draft: Mapping[str, Any], campos_obrigatorios: list[str]
) -> dict[str, Any]:
    referencia = str(draft.get("codigo_sugerido") or "").strip()
    # A pergunta nunca carrega o UUID do rascunho: a identidade fica nas
    # chaves idempotentes (vinculo_assunto) e o texto permanece publicável.
    return {
        "tipo": "busca_operacional",
        "pergunta": (
            "evidencias documentais para a revisao "
            + (referencia or "sem codigo sugerido")
        ),
        "termos": [t for t in [referencia] if t],
        "campos": list(campos_obrigatorios),
        "limite": 10,
        "cobertura_esperada": "contexto_completo",
    }


CONSULTA_SINTESE = {
    "tipo": "sintese",
    "pergunta": "sintetizar evidencias aceitas",
    "termos": [],
    "campos": [],
    "limite": 100,
    "paginacao": "inicio",
    "cobertura_esperada": "fontes_planejadas",
}


def _tarefa(plano_item_ref: str, adaptador: str, versao: str,
            contrato: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "plano_item_ref": plano_item_ref,
        "adaptador": adaptador,
        "adaptador_version": versao,
        "consulta_ref": contrato["consulta_ref"],
        "consulta_schema_version": contrato["consulta_schema_version"],
        "consulta_spec": contrato["consulta_spec"],
        "consulta_canonico": contrato["consulta_canonico"],
        "consulta_hash": contrato["consulta_hash"],
    }


def plano_do_draft(draft: Mapping[str, Any]) -> dict[str, Any]:
    """Deriva o plano completo, determinístico, para um rascunho."""
    assunto = assunto_do_draft(draft)
    origem = origem_do_draft(draft)
    campos = biblioteca.campos_politica_assunto(assunto["tipo"])
    fingerprint = fingerprint_do_draft(draft)
    contrato_fonte = biblioteca.contrato_consulta(
        consulta_fonte_do_draft(draft, campos)
    )
    contrato_sintese = biblioteca.contrato_consulta(dict(CONSULTA_SINTESE))
    ref_fonte = "pitem_" + _sha256(
        "fonte:" + str(draft.get("id")) + ":" + contrato_fonte["consulta_hash"]
    )[:32]
    ref_sintese = "pitem_" + _sha256(
        "sintese:" + str(draft.get("id")) + ":" + contrato_sintese["consulta_hash"]
    )[:32]
    tarefas = sorted(
        [
            _tarefa(ref_fonte, ADAPTADOR_FONTE, ADAPTADOR_FONTE_VERSION,
                    contrato_fonte),
            _tarefa(ref_sintese, "sintese", biblioteca.VERSAO_POLITICA_PADRAO,
                    contrato_sintese),
        ],
        key=lambda item: item["plano_item_ref"],
    )
    plano_canonico = _canonico({
        "campos_obrigatorios": list(campos),
        "policy_schema_hash": biblioteca.HASH_SCHEMA_POLITICAS,
        "tarefas": tarefas,
    })
    plano_hash = _sha256(plano_canonico)
    chaves = biblioteca.chaves_investigacao(
        assunto, origem, contrato_fonte["consulta_spec"],
        fingerprint_base=fingerprint,
        adaptador=ADAPTADOR_FONTE,
        versao_adaptador=ADAPTADOR_FONTE_VERSION,
        plano_hash=plano_hash,
        vinculo_assunto={"tipo": "operation_draft",
                        "operation_draft_id": str(draft.get("id"))},
    )
    return {
        "operation_draft_id": str(draft.get("id")),
        "source_draft_atualizado_em": draft.get("atualizado_em"),
        "assunto": assunto,
        "campos_obrigatorios": list(campos),
        "fingerprint_base": fingerprint,
        "plano_canonico": plano_canonico,
        "plano_hash": plano_hash,
        "tarefas": tarefas,
        "tarefa_fonte_ref": ref_fonte,
        "chaves": chaves,
        "escopo": str(draft.get("escopo") or "revisoes"),
    }


def draft_planejavel(draft: Mapping[str, Any]) -> bool:
    if str(draft.get("status") or "") not in STATUS_DRAFT_PLANEJAVEIS:
        return False
    if not draft.get("id") or not draft.get("atualizado_em"):
        return False
    return True


def planejar(
    drafts: list[dict[str, Any]],
    chaves_existentes: set[str],
    limite: int,
    chaves_tarefas: set[str] | None = None,
) -> dict[str, Any]:
    """Deriva o lote de investigações a criar (ou reparar).

    Uma investigação cuja chave já existe normalmente é pulada; a exceção é
    quando a execução anterior parou entre os dois INSERTs e a tarefa de
    fonte nunca nasceu — nesse caso o item volta como modo ``reparar_tarefa``
    para a criação idempotente completar o que faltou, sem duplicar nada.
    Quando ``chaves_tarefas`` é ``None`` (estado desconhecido), toda chave
    existente é tratada como completa, preservando o comportamento antigo.
    """
    if limite < 1 or limite > 10:
        raise ValueError("limite deve ficar entre 1 e 10")
    itens: list[dict[str, Any]] = []
    ja_existiam = 0
    ignorados = 0
    for draft in drafts:
        if not draft_planejavel(draft):
            ignorados += 1
            continue
        item = plano_do_draft(draft)
        if item["chaves"]["investigacao"] in chaves_existentes:
            if (chaves_tarefas is None
                    or item["chaves"]["tarefa"] in chaves_tarefas):
                ja_existiam += 1
                continue
            item["modo"] = "reparar_tarefa"
        else:
            item["modo"] = "criar"
        itens.append(item)
        if len(itens) >= limite:
            break
    assinatura = _sha256(_canonico([
        {
            "operation_draft_id": item["operation_draft_id"],
            "chave_investigacao": item["chaves"]["investigacao"],
            "plano_hash": item["plano_hash"],
            "fingerprint_base": item["fingerprint_base"],
            "modo": item["modo"],
        }
        for item in itens
    ]))
    return {
        "planejador_version": VERSAO_PLANEJADOR,
        "limite": limite,
        "itens": itens,
        "ja_investigados": ja_existiam,
        "drafts_ignorados": ignorados,
        "confirmacao": assinatura,
    }


def resumo_sanitizado(plano: dict[str, Any]) -> dict[str, Any]:
    """Versão do plano para impressão: nada de payload, só identidades."""
    return {
        "planejador_version": plano["planejador_version"],
        "limite": plano["limite"],
        "confirmacao": plano["confirmacao"],
        "ja_investigados": plano["ja_investigados"],
        "drafts_ignorados": plano["drafts_ignorados"],
        "itens": [
            {
                "operation_draft_id": item["operation_draft_id"],
                "modo": item["modo"],
                "titulo": item["assunto"]["titulo"],
                "assunto_tipo": item["assunto"]["tipo"],
                "campos_obrigatorios": item["campos_obrigatorios"],
                "chave_investigacao": item["chaves"]["investigacao"],
                "chave_tarefa": item["chaves"]["tarefa"],
                "plano_hash": item["plano_hash"],
                "tarefas": [
                    {
                        "plano_item_ref": t["plano_item_ref"],
                        "adaptador": t["adaptador"],
                        "consulta_ref": t["consulta_ref"],
                    }
                    for t in item["tarefas"]
                ],
            }
            for item in plano["itens"]
        ],
    }


# ---------------------------------------------------------------------------
# Cliente PostgREST (allowlist fechada; escrita nunca repetida)
# ---------------------------------------------------------------------------


class ClientePlanejador:
    def __init__(self, url: str, chave: str, timeout: int = 20) -> None:
        if not url or not chave:
            raise ValueError("configuracao_incompleta")
        self.url = url.rstrip("/")
        self.chave = chave
        self.timeout = max(1, min(int(timeout), 20))

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        base = {
            "apikey": self.chave,
            "Authorization": f"Bearer {self.chave}",
        }
        base.update(extra or {})
        return base

    def listar(self, tabela: str, consulta: dict[str, str]) -> list[dict[str, Any]]:
        linhas: list[dict[str, Any]] = []
        limite, inicio = 1000, 0
        while True:
            parametros = dict(consulta)
            parametros["limit"] = str(limite)
            parametros["offset"] = str(inicio)
            requisicao = urllib.request.Request(
                f"{self.url}/rest/v1/{tabela}?"
                + urllib.parse.urlencode(parametros),
                method="GET",
                headers=self._headers(),
            )
            try:
                with urllib.request.urlopen(
                    requisicao, timeout=self.timeout
                ) as resposta:
                    pagina = json.loads(resposta.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                raise RuntimeError(
                    f"leitura de {tabela} falhou com HTTP {exc.code}"
                ) from exc
            if not isinstance(pagina, list):
                raise RuntimeError(f"resposta inválida de {tabela}")
            linhas.extend(pagina)
            if len(pagina) < limite:
                return linhas
            inicio += limite

    def inserir(self, tabela: str, payload: Mapping[str, Any]) -> str:
        """Um POST, sem retentativa. Conflito idempotente vira 'ja_existia'."""
        if tabela not in TABELAS_ESCRITA:
            raise ValueError(f"escrita não permitida: {tabela}")
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
            with urllib.request.urlopen(requisicao, timeout=self.timeout) as resposta:
                if resposta.status in {200, 201, 204}:
                    return "criado"
                raise RuntimeError(f"HTTP inesperado em {tabela}: {resposta.status}")
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                return "ja_existia"
            raise RuntimeError(
                f"escrita em {tabela} falhou com HTTP {exc.code}"
            ) from exc


def cliente_do_ambiente() -> ClientePlanejador:
    url = os.environ.get("SUPABASE_URL") or os.environ.get("CONFINEX_DB_URL") or ""
    chave = (
        os.environ.get("SUPABASE_SERVICE_KEY")
        or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or os.environ.get("CONFINEX_DB_KEY")
        or ""
    )
    return ClientePlanejador(url, chave)


def carregar_estado(
    cliente: ClientePlanejador,
) -> tuple[list[dict[str, Any]], set[str], set[str]]:
    drafts = cliente.listar("operation_drafts", {
        "select": (
            "id,status,atualizado_em,entidade_final_tipo,tipo_operacao,"
            "codigo_sugerido,contexto_nome,origem_canal,origem_conversa_id,"
            "origem_mensagem_id,dados_extraidos,campos_pendentes,escopo"
        ),
        "status": "in.(" + ",".join(sorted(STATUS_DRAFT_PLANEJAVEIS)) + ")",
        "order": "criado_em.asc",
    })
    existentes = cliente.listar("investigacoes_revisao", {
        "select": "chave_idempotencia",
    })
    chaves = {
        str(linha.get("chave_idempotencia") or "")
        for linha in existentes
    }
    tarefas = cliente.listar("investigacao_tarefas", {
        "select": "chave_idempotencia",
    })
    chaves_tarefas = {
        str(linha.get("chave_idempotencia") or "")
        for linha in tarefas
    }
    return drafts, chaves, chaves_tarefas


def registrar_investigacao(
    cliente: ClientePlanejador, item: Mapping[str, Any]
) -> dict[str, str]:
    resultado_inv = cliente.inserir("investigacoes_revisao", {
        "chave_idempotencia": item["chaves"]["investigacao"],
        "assunto_tipo": item["assunto"]["tipo"],
        "titulo": item["assunto"]["titulo"],
        "fingerprint_base": item["fingerprint_base"],
        "plano_hash": item["plano_hash"],
        "plano_canonico": item["plano_canonico"],
        "plano_tarefas": item["tarefas"],
        "policy_version": biblioteca.VERSAO_POLITICA_PADRAO,
        "policy_schema_hash": biblioteca.HASH_SCHEMA_POLITICAS,
        "campos_obrigatorios": item["campos_obrigatorios"],
        "source_draft_id": item["operation_draft_id"],
        "source_draft_atualizado_em": item["source_draft_atualizado_em"],
        "escopo": item["escopo"],
    })
    linhas = cliente.listar("investigacoes_revisao", {
        "select": "id",
        "chave_idempotencia": "eq." + item["chaves"]["investigacao"],
    })
    if len(linhas) != 1:
        raise RuntimeError("investigação não localizada após o INSERT")
    investigacao_id = str(linhas[0]["id"])
    fonte = next(
        t for t in item["tarefas"] if t["adaptador"] == ADAPTADOR_FONTE
    )
    resultado_tarefa = cliente.inserir("investigacao_tarefas", {
        "investigacao_id": investigacao_id,
        "chave_idempotencia": item["chaves"]["tarefa"],
        "plano_item_ref": fonte["plano_item_ref"],
        "adaptador": fonte["adaptador"],
        "adaptador_version": fonte["adaptador_version"],
        "consulta_ref": fonte["consulta_ref"],
        "consulta_schema_version": fonte["consulta_schema_version"],
        "consulta_spec": fonte["consulta_spec"],
        "consulta_canonico": fonte["consulta_canonico"],
        "consulta_hash": fonte["consulta_hash"],
    })
    return {
        "investigacao_id": investigacao_id,
        "investigacao": resultado_inv,
        "tarefa_fonte": resultado_tarefa,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Planejador de investigações (dry-run por padrão)"
    )
    parser.add_argument("--versao", action="store_true")
    parser.add_argument("--executar", action="store_true")
    parser.add_argument("--limite", type=int, default=1)
    parser.add_argument("--confirmacao")
    args = parser.parse_args(argv)
    if args.versao:
        print(VERSAO_PLANEJADOR)
        return 0
    cliente = cliente_do_ambiente()
    drafts, chaves, chaves_tarefas = carregar_estado(cliente)
    plano = planejar(drafts, chaves, args.limite, chaves_tarefas)
    print(json.dumps(resumo_sanitizado(plano), ensure_ascii=False, indent=2))
    if not args.executar:
        print(
            "\nDRY-RUN: nada foi gravado. Para executar: --executar "
            f"--limite {args.limite} --confirmacao {plano['confirmacao']}",
            file=sys.stderr,
        )
        return 0
    if args.confirmacao != plano["confirmacao"]:
        print(
            "ERRO: --confirmacao não corresponde ao plano atual "
            "(o estado mudou desde o dry-run); rode o dry-run de novo.",
            file=sys.stderr,
        )
        return 2
    resultados = []
    for item in plano["itens"]:
        resultados.append({
            "operation_draft_id": item["operation_draft_id"],
            **registrar_investigacao(cliente, item),
        })
    print(json.dumps({"executados": resultados}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
