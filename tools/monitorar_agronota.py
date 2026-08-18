#!/usr/bin/env python3
"""Concilia NF-e novas do AgroNota com a fila de Revisões do Confinex.

O modo padrão é dry-run. A execução escreve somente no staging fiscal e nas
tabelas de revisão/auditoria; tabelas operacionais não fazem parte do cliente.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agronota_nf import analisar_xml_nfe, campos_pendentes_documento, documento_deve_ser_indexado


CONFIRMACAO = "PROCESSAR NFS AGRONOTA PARA REVISAO"
NAMESPACE = uuid.UUID("a1a23770-061f-4ff9-9e69-40d1d16b7e7c")
TABELAS_ESCRITA = {"notas_fiscais_xml_raw", "operation_drafts", "pending_actions", "eventos"}
TABELAS_LEITURA = TABELAS_ESCRITA | {"gtas", "entradas_confinamento"}


def id_deterministico(tipo: str, chave: str) -> str:
    return str(uuid.uuid5(NAMESPACE, f"agronota:{tipo}:{chave}"))


def referencia_fonte(chave: str) -> str:
    return "agronota-nfe-" + hashlib.sha256(chave.encode()).hexdigest()[:16]


class ClienteSupabase:
    def __init__(self, url: str, chave: str):
        self.url = url.rstrip("/") + "/rest/v1/"
        self.headers = {"apikey": chave, "Authorization": "Bearer " + chave, "Content-Type": "application/json"}

    def _chamar(self, metodo: str, caminho: str, payload: Any = None, prefer: str | None = None):
        tabela = caminho.split("?", 1)[0]
        permitidas = TABELAS_LEITURA if metodo == "GET" else TABELAS_ESCRITA
        if tabela not in permitidas:
            raise ValueError(f"tabela não permitida: {tabela}")
        headers = dict(self.headers)
        if prefer:
            headers["Prefer"] = prefer
        corpo = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(self.url + caminho, data=corpo, headers=headers, method=metodo)
        with urllib.request.urlopen(req, timeout=30) as resposta:
            bruto = resposta.read()
            return json.loads(bruto) if bruto else None

    def listar_notas(self, desde: str, numero_nota: str | None = None) -> list[dict[str, Any]]:
        filtros = {
            "select": "id,chave_acesso,numero,data,valor,qtd_total_itens,descricao_itens,operacao_id,gta,alerta_gta_ausente,eh_complemento,eh_venda_gado_pablo,nf_referenciada,nota_fiscal_id,fonte,criado_em",
            "order": "criado_em.asc",
            "limit": "500",
        }
        if numero_nota:
            filtros["numero"] = "eq." + numero_nota
        else:
            filtros["criado_em"] = "gte." + desde
        query = urllib.parse.urlencode(filtros)
        return self._chamar("GET", "notas_fiscais_xml_raw?" + query) or []

    def operacoes_por_gta(self, gta: str) -> set[str]:
        operacoes: set[str] = set()
        for tabela, coluna in (("gtas", "numero"), ("entradas_confinamento", "gta")):
            query = urllib.parse.urlencode({"select": "operacao_id", coluna: "eq." + gta, "limit": "50"})
            for item in self._chamar("GET", tabela + "?" + query) or []:
                if item.get("operacao_id"):
                    operacoes.add(item["operacao_id"])
        return operacoes

    def operacoes_por_referencias(self, referencias: list[str], nota_fiscal_id: str | None = None) -> set[str]:
        operacoes: set[str] = set()
        filtros: list[tuple[str, str]] = [("chave_acesso", item) for item in referencias]
        if nota_fiscal_id:
            filtros.append(("id", nota_fiscal_id))
        for coluna, valor in filtros:
            query = urllib.parse.urlencode({"select": "operacao_id", coluna: "eq." + valor, "limit": "10"})
            for item in self._chamar("GET", "notas_fiscais_xml_raw?" + query) or []:
                if item.get("operacao_id"):
                    operacoes.add(item["operacao_id"])
        return operacoes

    def existe(self, tabela: str, identificador: str) -> bool:
        return self.obter(tabela, identificador) is not None

    def obter(self, tabela: str, identificador: str) -> dict[str, Any] | None:
        query = urllib.parse.urlencode({"select": "*", "id": "eq." + identificador, "limit": "1"})
        linhas = self._chamar("GET", tabela + "?" + query) or []
        return linhas[0] if linhas else None

    def inserir(self, tabela: str, payload: dict[str, Any]):
        self._chamar("POST", tabela, [payload], "return=minimal")

    def atualizar_nota(self, identificador: str, payload: dict[str, Any]):
        query = urllib.parse.urlencode({"id": "eq." + identificador})
        self._chamar("PATCH", "notas_fiscais_xml_raw?" + query, payload, "return=minimal")

    def atualizar(self, tabela: str, identificador: str, payload: dict[str, Any]):
        query = urllib.parse.urlencode({"id": "eq." + identificador})
        self._chamar("PATCH", tabela + "?" + query, payload, "return=minimal")


def montar_registros(nota: dict[str, Any], analise: dict[str, Any]) -> dict[str, dict[str, Any]]:
    chave = nota["chave_acesso"]
    draft_id = id_deterministico("draft", chave)
    action_id = id_deterministico("action", chave)
    event_id = id_deterministico("event", chave)
    fonte = referencia_fonte(chave)
    pendentes = campos_pendentes_documento(
        tem_gta=bool(analise.get("gta")), operacao_vinculada=bool(nota.get("operacao_id")),
    )
    complemento = bool(analise.get("eh_complemento") or nota.get("eh_complemento"))
    if nota.get("operacao_id"):
        relacao = "complemento_de_negocio_existente" if complemento else "documento_de_negocio_existente"
    elif complemento:
        relacao = "complemento_pendente_de_vinculo"
    else:
        relacao = "relacao_com_negocio_a_conferir"
    contexto = "Documentos fiscais"
    fonte_nota = str(nota.get("fonte") or "")
    contraparte = (
        analise.get("emitente_nome")
        if fonte_nota.endswith("_recebida")
        else analise.get("destinatario_nome")
    )
    origem_documento = (
        "Recebida pelo AgroNota"
        if fonte_nota.endswith("_recebida")
        else "Emitida pelo AgroNota" if fonte_nota.endswith("_emitida") else "AgroNota"
    )
    descricao_itens = nota.get("descricao_itens") or " | ".join(
        analise.get("descricoes_produtos") or []
    )
    situacoes = {
        "documento_de_negocio_existente": "Documento relacionado a um negócio existente.",
        "complemento_de_negocio_existente": "Complemento relacionado a um negócio existente.",
        "complemento_pendente_de_vinculo": "Complemento fiscal ainda sem negócio confirmado.",
        "relacao_com_negocio_a_conferir": "NF-e encontrada sem negócio confirmado.",
    }
    dados = {
        "tipo_documento": "NF-e pecuária",
        "numero_nf": nota.get("numero"),
        "data_emissao": nota.get("data"),
        "data": nota.get("data"),
        "valor_total": nota.get("valor"),
        "quantidade": nota.get("qtd_total_itens"),
        "gta": analise.get("gta"),
        "contraparte": contraparte,
        "emitente_nome": analise.get("emitente_nome"),
        "destinatario_nome": analise.get("destinatario_nome"),
        "natureza_operacao": analise.get("natureza_operacao"),
        "descricao_itens": descricao_itens,
        "fonte_documento": origem_documento,
        "documento": f"NF-e {nota.get('numero')}" if nota.get("numero") else "NF-e",
        "operacao_id": nota.get("operacao_id"),
        "fonte_referencia": fonte,
        "natureza_documento": "venda" if analise.get("eh_nota_venda") else "a_conferir",
        "relacao_negocio": relacao,
        "pode_ser_novo_negocio": relacao == "relacao_com_negocio_a_conferir",
        "eh_complemento": complemento,
        "vinculo_ambiguo": bool(analise.get("vinculo_ambiguo")),
        "situacao": situacoes.get(relacao, "Relação documental ainda precisa ser conferida."),
        "divergencia": (
            "Mais de um negócio possível foi encontrado; mantenha sem vínculo até conferir."
            if analise.get("vinculo_ambiguo") else ""
        ),
        "acao_recomendada": (
            "Confirme se a NF-e pertence a um negócio existente, complementa documentos "
            "ou animais, ou representa um negócio novo."
        ),
        "evidencia": " · ".join(filter(None, (
            f"NF-e {nota.get('numero')}" if nota.get("numero") else None,
            f"GTA {analise.get('gta')}" if analise.get("gta") else None,
            f"{nota.get('qtd_total_itens')} cabeças" if nota.get("qtd_total_itens") is not None else None,
            str(descricao_itens) if descricao_itens else None,
        ))),
        "opcoes_relacao": [
            "documento de negócio existente",
            "complemento documental",
            "complemento de animais de negócio existente",
            "possível negócio novo",
        ],
        "promovido_para_operacional": False,
        "contexto_nome": contexto,
        "contexto_operacional": contexto,
        "grupo_telegram": contexto,
        "origem_canal": "agronotas",
        "origem_conversa_id": fonte,
        "origem_mensagem_id": fonte,
        "agente": "juan",
        "escopo": "documentos_fiscais",
        "status_confirmacao": "em_revisao",
    }
    draft = {
        "id": draft_id, "agente": "juan", "status": "em_revisao",
        "tipo_operacao": "indexacao_nota_fiscal_negocio",
        "entidade_final_tipo": "revisao_documental",
        "confianca": 0.95 if analise.get("gta") and nota.get("operacao_id") else 0.8,
        "dados_extraidos": dados, "campos_pendentes": pendentes,
        "inferencias": {"gta_lida_do_xml": bool(analise.get("gta")),
                        "nota_fiscal_deve_ser_relacionada_a_negocio": True,
                        "classificacao_relacao": relacao,
                        "exige_confirmacao": True},
        "pending_action_id": action_id, "origem_canal": "agronotas",
        "origem_conversa_id": fonte, "origem_mensagem_id": fonte,
        "contexto_canonico": "agronotas:monitor-fiscal", "contexto_nome": contexto,
        "escopo": "documentos_fiscais",
    }
    action = {
        "id": action_id, "agente": "juan", "usuario_solicitante": "sistema",
        "canal": "agronotas", "acao_tipo": "revisar_indexacao_nota_fiscal",
        "entidade_tipo": "operation_draft", "entidade_id": draft_id,
        "resumo": "Classificar a relação da NF-e com o negócio correto",
        "payload": {"operation_draft_id": draft_id, "dados_extraidos": dados,
                    "campos_pendentes": pendentes, "promovido_para_operacional": False},
        "resultado": {"operation_draft_id": draft_id}, "status": "aguardando_confirmacao",
        "origem_canal": "agronotas", "origem_conversa_id": fonte,
        "origem_mensagem_id": fonte, "contexto_canonico": "agronotas:monitor-fiscal",
        "contexto_nome": contexto, "escopo": "documentos_fiscais",
    }
    event = {
        "id": event_id, "tipo": "nota_fiscal_indexada_para_revisao", "agente": "juan",
        "usuario": "sistema", "entidade_tipo": "operation_draft", "entidade_id": draft_id,
        "origem": "agronotas_monitor_fiscal", "origem_canal": "agronotas",
        "origem_conversa_id": fonte, "origem_mensagem_id": fonte,
        "contexto_canonico": "agronotas:monitor-fiscal", "contexto_nome": contexto,
        "escopo": "documentos_fiscais", "status": "pendente",
        "fonte_ref": fonte, "confianca": draft["confianca"],
        "dados": {"operation_draft_id": draft_id, "pending_action_id": action_id,
                  "gta_identificada": bool(analise.get("gta")), "relacao_negocio": relacao,
                  "promovido_para_operacional": False},
        "observacao": "Nota fiscal indexada para relacionar ao negócio correto, sem promoção operacional.",
    }
    return {"operation_drafts": draft, "pending_actions": action, "eventos": event}


def planejar(
    cliente: ClienteSupabase,
    xml_store: Path,
    desde: str,
    numero_nota: str | None = None,
) -> dict[str, Any]:
    alteracoes_nota: list[tuple[str, dict[str, Any]]] = []
    atualizacoes: list[tuple[str, str, dict[str, Any]]] = []
    criacoes: list[tuple[str, dict[str, Any]]] = []
    faltam_xml = ambiguas = ignoradas = 0
    for nota in cliente.listar_notas(desde, numero_nota=numero_nota):
        caminho = xml_store / f"{nota.get('chave_acesso')}-procNfe.xml"
        if not caminho.exists():
            faltam_xml += 1
            continue
        try:
            analise = analisar_xml_nfe(caminho.read_bytes())
        except Exception:
            faltam_xml += 1
            continue
        if not documento_deve_ser_indexado(
            analise, fonte=nota.get("fonte"),
            eh_venda_gado_pablo=bool(nota.get("eh_venda_gado_pablo")),
        ):
            ignoradas += 1
            continue
        patch: dict[str, Any] = {}
        if analise.get("gta") and nota.get("gta") != analise["gta"]:
            patch["gta"] = analise["gta"]
        alerta = not bool(analise.get("gta"))
        if nota.get("alerta_gta_ausente") != alerta:
            patch["alerta_gta_ausente"] = alerta
        if patch:
            alteracoes_nota.append((nota["id"], patch))
        if analise.get("gta_ambigua"):
            ambiguas += 1
        operacoes_candidatas: set[str] = set()
        if nota.get("operacao_id"):
            operacoes_candidatas.add(nota["operacao_id"])
        if analise.get("gta"):
            operacoes_candidatas.update(cliente.operacoes_por_gta(analise["gta"]))
        referencias = list(analise.get("referencias_nfe") or [])
        if nota.get("nf_referenciada") and re.fullmatch(r"\d{44}", str(nota["nf_referenciada"])):
            referencias.append(str(nota["nf_referenciada"]))
        operacoes_candidatas.update(cliente.operacoes_por_referencias(
            referencias, nota.get("nota_fiscal_id")
        ))
        nota_processada = dict(nota)
        if not nota.get("operacao_id") and len(operacoes_candidatas) == 1:
            nota_processada["operacao_id"] = next(iter(operacoes_candidatas))
            patch["operacao_id"] = nota_processada["operacao_id"]
            if not any(item_id == nota["id"] for item_id, _ in alteracoes_nota):
                alteracoes_nota.append((nota["id"], patch))
            else:
                for indice, (item_id, existente) in enumerate(alteracoes_nota):
                    if item_id == nota["id"]:
                        alteracoes_nota[indice] = (item_id, {**existente, **patch})
                        break
        analise["vinculo_ambiguo"] = len(operacoes_candidatas) > 1
        registros = montar_registros(nota_processada, analise)
        for tabela, payload in registros.items():
            atual = cliente.obter(tabela, payload["id"])
            if atual is None:
                criacoes.append((tabela, payload))
            elif tabela == "operation_drafts" and atual.get("status") in {"rascunho", "em_revisao", "aguardando_confirmacao"}:
                campos = ("tipo_operacao", "entidade_final_tipo", "dados_extraidos",
                          "campos_pendentes", "inferencias", "confianca")
                patch_existente = {campo: payload[campo] for campo in campos if atual.get(campo) != payload.get(campo)}
                if patch_existente:
                    atualizacoes.append((tabela, payload["id"], patch_existente))
            elif tabela == "pending_actions" and atual.get("status") in {"em_revisao", "aguardando_confirmacao"}:
                campos = ("acao_tipo", "payload", "resultado", "resumo")
                patch_existente = {campo: payload[campo] for campo in campos if atual.get(campo) != payload.get(campo)}
                if patch_existente:
                    atualizacoes.append((tabela, payload["id"], patch_existente))
            elif tabela == "eventos" and (
                (atual.get("dados") or {}).get("novo_negocio") is True
                or cliente.existe("eventos", id_deterministico("event-negocio", nota["chave_acesso"]))
            ):
                evento_correcao = dict(payload)
                evento_correcao["id"] = id_deterministico("event-correcao-relacao", nota["chave_acesso"])
                evento_correcao["tipo"] = "classificacao_de_nota_fiscal_corrigida"
                evento_correcao["observacao"] = "Classificação automática como novo negócio foi removida; relação voltou para conferência."
                if not cliente.existe("eventos", evento_correcao["id"]):
                    criacoes.append(("eventos", evento_correcao))
            elif tabela == "eventos" and analise.get("gta") and not (atual.get("dados") or {}).get("gta_identificada"):
                evento_gta = dict(payload)
                evento_gta["id"] = id_deterministico("event-gta", nota["chave_acesso"])
                evento_gta["tipo"] = "gta_identificada_em_documento_fiscal"
                evento_gta["observacao"] = "GTA identificada no XML; revisão documental atualizada sem promoção operacional."
                if not cliente.existe("eventos", evento_gta["id"]):
                    criacoes.append(("eventos", evento_gta))
    assinatura = ([f"nf:{i}" for i, _ in alteracoes_nota]
                  + [f"up:{t}:{i}" for t, i, _ in atualizacoes]
                  + [f"{t}:{p['id']}" for t, p in criacoes])
    return {
        "plano_id": hashlib.sha256("\n".join(sorted(assinatura)).encode()).hexdigest()[:12],
        "alteracoes_notas": alteracoes_nota, "atualizacoes": atualizacoes, "criacoes": criacoes,
        "resumo": {
            "notas_atualizadas": len(alteracoes_nota),
            "operation_drafts": sum(t == "operation_drafts" for t, _ in criacoes),
            "pending_actions": sum(t == "pending_actions" for t, _ in criacoes),
            "eventos": sum(t == "eventos" for t, _ in criacoes),
            "operation_drafts_atualizados": sum(t == "operation_drafts" for t, _, _ in atualizacoes),
            "pending_actions_atualizados": sum(t == "pending_actions" for t, _, _ in atualizacoes),
            "gtas_ambiguas": ambiguas, "xml_ausente_ou_invalido": faltam_xml,
            "documentos_nao_pecuarios_ignorados": ignoradas,
            "tabelas_operacionais_alteradas": 0,
            "filtro_numero_nota": numero_nota,
        },
    }


def executar(cliente: ClienteSupabase, plano: dict[str, Any]):
    for identificador, payload in plano["alteracoes_notas"]:
        cliente.atualizar_nota(identificador, payload)
    for tabela, identificador, payload in plano["atualizacoes"]:
        cliente.atualizar(tabela, identificador, payload)
    # operation_drafts.pending_action_id possui FK; a ação deve existir primeiro.
    ordem = {"pending_actions": 0, "operation_drafts": 1, "eventos": 2}
    for tabela, payload in sorted(plano["criacoes"], key=lambda item: ordem[item[0]]):
        cliente.inserir(tabela, payload)


def credenciais() -> tuple[str, str]:
    url = os.environ.get("CONFINEX_DB_URL") or os.environ.get("SUPABASE_URL")
    chave = os.environ.get("CONFINEX_DB_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not chave:
        raise SystemExit("Credenciais do Supabase ausentes")
    return url, chave


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--xml-store", type=Path, required=True)
    parser.add_argument("--since-days", type=int, default=2)
    parser.add_argument("--numero-nota")
    parser.add_argument("--executar", action="store_true")
    parser.add_argument("--confirmacao")
    args = parser.parse_args()
    if args.since_days < 1 or args.since_days > 365:
        raise SystemExit("--since-days deve ficar entre 1 e 365")
    if args.numero_nota and not re.fullmatch(r"\d{1,20}", args.numero_nota):
        raise SystemExit("--numero-nota deve conter somente dígitos")
    if args.executar and args.confirmacao != CONFIRMACAO:
        raise SystemExit("Confirmação inválida")
    url, chave = credenciais()
    cliente = ClienteSupabase(url, chave)
    desde = (datetime.now(timezone.utc) - timedelta(days=args.since_days)).isoformat().replace("+00:00", "Z")
    plano = planejar(cliente, args.xml_store, desde, numero_nota=args.numero_nota)
    if args.executar:
        executar(cliente, plano)
    saida = {"plano_id": plano["plano_id"], **plano["resumo"],
             "modo": "executado" if args.executar else "dry-run", "nenhuma_promocao_operacional": True}
    print(json.dumps(saida, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
