#!/usr/bin/env python3
"""Compara um extrato OFX com um snapshot bancário, sem executar escrita."""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

try:
    from perfilar_identidade_ofx import MAX_BYTES, extrair_ofx_privado
    from identidade_bancaria import avaliar_presenca, comparar_conteudo, chave_logica
except ModuleNotFoundError:
    from tools.perfilar_identidade_ofx import MAX_BYTES, extrair_ofx_privado
    from tools.identidade_bancaria import avaliar_presenca, comparar_conteudo, chave_logica


def campo_ofx(bloco: str, nome: str) -> str:
    encontrado = re.search(rf"<{nome}>([^<\r\n]+)", bloco, re.I)
    return encontrado.group(1).strip() if encontrado else ""


def data_ofx(valor: str) -> str | None:
    digitos = re.sub(r"\D", "", valor or "")
    if len(digitos) < 8:
        return None
    try:
        return datetime.strptime(digitos[:8], "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def ler_ofx(caminho: Path) -> dict[str, Any]:
    with caminho.open("rb") as arquivo:
        conteudo_bytes = arquivo.read(MAX_BYTES + 1)
    perfil = extrair_ofx_privado(conteudo_bytes)
    transacoes = []
    for demonstrativo in perfil["demonstrativos"]:
        identidade = demonstrativo["identidade"]
        for tx in demonstrativo["transacoes"]:
            data = tx["data"][:10] if tx["data"] else None
            metadados = {
                "versao": 1,
                "identidade": dict(identidade),
                "identidade_sha256": _assinatura(identidade),
                "data_ofx_original": tx["data_ofx_original"],
                "data_formato": tx["data_formato"],
                "stmttrn_sha256": tx["stmttrn_sha256"],
                "ocorrencias": [{"demonstrativo": demonstrativo["ordinal"],
                                 "transacao": tx["ordinal"]}],
            }
            transacoes.append({
                "fitid": tx["fitid"], "id_externo": tx["fitid"], "data": data,
                "tipo": tx["trntype"], "valor": tx["valor"],
                "descricao": tx["descricao"], "memo": tx["memo"],
                "dados_origem": {"ofx": metadados},
            })
    return {
        "sha256": hashlib.sha256(conteudo_bytes).hexdigest(),
        "transacoes": transacoes,
        "identidades_incompletas": perfil["identidades_incompletas"],
        "resumo": perfil["resumo"],
    }


def _canonico_hash(valor: Any) -> Any:
    if isinstance(valor, Decimal):
        return ["decimal", str(valor)]
    if isinstance(valor, (str, int, float, bool)) or valor is None:
        return valor
    if isinstance(valor, date):
        return ["data", valor.isoformat()]
    if isinstance(valor, dict):
        return {str(chave): _canonico_hash(valor[chave]) for chave in sorted(valor, key=str)}
    if isinstance(valor, (list, tuple)):
        return [_canonico_hash(item) for item in valor]
    raise TypeError("tipo não serializável na assinatura do plano")


def _assinatura(valor: Any) -> str:
    conteudo = json.dumps(_canonico_hash(valor), ensure_ascii=False,
                          sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(conteudo).hexdigest()


def _dados_comparados_sha256(ofx: dict[str, Any], snapshot: list[dict[str, Any]]) -> str:
    """Assina a entrada inteira comparada sem copiá-la para o plano."""

    comparados = {
        "ofx": {"sha256": ofx.get("sha256"), "transacoes": ofx.get("transacoes", [])},
        "snapshot": snapshot,
    }
    return _assinatura(comparados)


def _fitid(item: dict[str, Any]) -> Any:
    return item.get("fitid") if item.get("fitid") is not None else item.get("id_externo")


def _classificar_item(item: dict[str, Any], snapshot: list[dict[str, Any]]) -> str:
    """Retorna somente uma classe interna; nenhuma dessas razões é pública por ID."""

    if (item.get("data") is None or item.get("valor") is None
            or item.get("tipo") in (None, "")):
        return "conteudo_pendente"
    try:
        estado = avaliar_presenca(item, snapshot)
    except (KeyError, TypeError, ValueError):
        return "conteudo_pendente"
    if estado in {"presente_por_identidade", "presente_por_vinculo"}:
        return estado
    if estado == "ausente_na_amostra":
        # A ausência da chave lógica completa é um caso novo na amostra;
        # coincidência isolada de FITID não reintroduz uma chave global.
        return "novo"
    return estado


def gerar_plano(ofx: dict[str, Any], snapshot: list[dict[str, Any]], referencia: date) -> dict[str, Any]:
    if not isinstance(ofx, dict) or not isinstance(ofx.get("transacoes"), list):
        raise ValueError("ofx inválido")
    if not isinstance(snapshot, list) or any(not isinstance(item, dict) for item in snapshot):
        raise ValueError("snapshot inválido")
    transacoes = list(ofx.get("transacoes") or [])
    chaves_ofx: list[Any] = []
    identificadores_sem_prova_ofx = 0
    chaves_snapshot: list[Any] = []
    identificadores_sem_prova_snapshot = 0
    for item in transacoes:
        try:
            chave = chave_logica(item)
        except (KeyError, TypeError, ValueError):
            chave = None
        if chave is None:
            identificadores_sem_prova_ofx += 1
        else:
            chaves_ofx.append(chave)
    for item in snapshot:
        try:
            chave = chave_logica(item, "id_externo")
        except (KeyError, TypeError, ValueError):
            chave = None
        if chave is None:
            identificadores_sem_prova_snapshot += 1
        else:
            chaves_snapshot.append(chave)
    datas_ofx = sorted(item["data"] for item in transacoes if item.get("data"))
    datas_supabase = sorted(
        str(item.get("data") or "")[:10]
        for item in snapshot
        if item.get("data")
    )
    grupos: dict[Any, list[dict[str, Any]]] = collections.defaultdict(list)
    estados: list[str] = []
    motivos: collections.Counter[str] = collections.Counter()
    indeterminadas_ocorrencias = 0
    repeticoes_identicas = 0
    casos_repeticao_identica = 0
    repeticoes_divergentes = 0
    repeticoes_divergentes_ocorrencias = 0
    for item in transacoes:
        try:
            chave = chave_logica(item)
        except (KeyError, TypeError, ValueError):
            chave = None
        if chave is None:
            estados.append("identidade_pendente")
            motivos["identidade_pendente"] += 1
            indeterminadas_ocorrencias += 1
        else:
            grupos[chave].append(item)
    itens_classificaveis: list[tuple[dict[str, Any], int]] = []
    for grupo in grupos.values():
        if len(grupo) > 1:
            comparacoes = [comparar_conteudo(grupo[0], item) for item in grupo[1:]]
            if any(resultado != "igual" for resultado in comparacoes):
                repeticoes_divergentes += 1
                estados.append("conteudo_divergente_no_ofx")
                motivos["conteudo_divergente_no_ofx"] += 1
                indeterminadas_ocorrencias += len(grupo)
                repeticoes_divergentes_ocorrencias += len(grupo) - 1
                continue
            repeticoes_identicas += len(grupo) - 1
            casos_repeticao_identica += 1
            itens_classificaveis.append((grupo[0], len(grupo)))
            continue
        itens_classificaveis.append((grupo[0], 1))
    estados_itens: list[str] = []
    ocorrencias_itens: list[int] = []
    for item, ocorrencias in itens_classificaveis:
        estado = _classificar_item(item, snapshot)
        estados_itens.append(estado)
        ocorrencias_itens.append(ocorrencias)
        estados.append(estado)
        if estado not in {"novo", "presente_por_identidade", "presente_por_vinculo"}:
            motivos[estado] += 1
    # Mantém um caso por chave lógica, inclusive repetições idênticas, e
    # conserva a quantidade de ocorrências em campo separado.
    novos = [item for (item, _), estado in zip(itens_classificaveis, estados_itens) if estado == "novo"]
    novas_ocorrencias = sum(ocorrencias for estado, ocorrencias in zip(estados_itens, ocorrencias_itens)
                            if estado == "novo")
    presentes = sum(estado in {"presente_por_identidade", "presente_por_vinculo"} for estado in estados_itens)
    presentes_ocorrencias = sum(ocorrencias for estado, ocorrencias in zip(estados_itens, ocorrencias_itens)
                                if estado in {"presente_por_identidade", "presente_por_vinculo"})
    indeterminados = sum(estado not in {"novo", "presente_por_identidade", "presente_por_vinculo"} for estado in estados)
    indeterminadas_ocorrencias += sum(ocorrencias for estado, ocorrencias in zip(estados_itens, ocorrencias_itens)
                                     if estado not in {"novo", "presente_por_identidade", "presente_por_vinculo"})
    novos_por_data = dict(sorted(collections.Counter(item["data"] for item in novos if item.get("data")).items()))
    plano = {
        "gerado_em": datetime.now().astimezone().isoformat(),
        "data_referencia": referencia.isoformat(),
        "modo": "dry_run_somente_leitura",
        "escritas_executadas": 0,
        "arquivo": {
            "sha256": ofx["sha256"],
            "transacoes": len(transacoes),
            "identificadores_unicos": len(set(chaves_ofx)),
            "identificadores_sem_prova": identificadores_sem_prova_ofx,
            "duplicidades_internas": repeticoes_identicas + repeticoes_divergentes_ocorrencias,
            "data_inicial": min(datas_ofx) if datas_ofx else None,
            "data_final": max(datas_ofx) if datas_ofx else None,
        },
        "supabase": {
            "transacoes": len(snapshot),
            "identificadores_unicos": len(set(chaves_snapshot)),
            "identificadores_sem_prova": identificadores_sem_prova_snapshot,
            "data_final": max(datas_supabase) if datas_supabase else None,
        },
        "cruzamento": {
            "ja_presentes": presentes,
            "novos": len(novos),
            "novas_ocorrencias": novas_ocorrencias,
            "indeterminados": indeterminados,
            "indeterminadas_ocorrencias": indeterminadas_ocorrencias,
            "presentes_ocorrencias": presentes_ocorrencias,
            "novos_por_data": novos_por_data,
            "data_inicial_novos": min((item["data"] for item in novos), default=None),
            "data_final_novos": max((item["data"] for item in novos), default=None),
            "indeterminados_por_motivo": dict(sorted(motivos.items())),
            "repeticoes_identicas": repeticoes_identicas,
            "casos_repeticao_identica": casos_repeticao_identica,
            "repeticoes_conteudo_divergente": repeticoes_divergentes,
        },
        "comparacao": {"dados_comparados_sha256": _dados_comparados_sha256(ofx, snapshot)},
        "pendencias": [],
    }
    if plano["arquivo"]["data_final"] != referencia.isoformat():
        plano["pendencias"].append("extrato_nao_chega_a_data_de_referencia")
    if novos:
        plano["pendencias"].append("lancamentos_novos_aguardam_importacao_controlada")
    if indeterminados:
        plano["pendencias"].append("lancamentos_indeterminados_aguardam_conferencia")
    if ofx.get("identidades_incompletas"):
        plano["pendencias"].append("demonstrativos_com_identidade_incompleta")
    if casos_repeticao_identica:
        plano["pendencias"].append("repeticoes_identicas_aguardam_conferencia")
    if plano["arquivo"]["duplicidades_internas"]:
        plano["pendencias"].append("extrato_possui_identificadores_duplicados")
    assinavel = {chave: valor for chave, valor in plano.items() if chave != "gerado_em"}
    plano["plano_id"] = _assinatura(assinavel)[:12]
    return plano


def relatorio_markdown(plano: dict[str, Any]) -> str:
    arquivo = plano["arquivo"]
    supabase = plano["supabase"]
    cruzamento = plano["cruzamento"]
    return f"""# Complemento privado — extrato bancário OFX

Plano `{plano['plano_id']}`, gerado em {plano['gerado_em']}, exclusivamente em
modo de leitura. Nenhuma escrita foi executada.

- período disponível no OFX: **{arquivo['data_inicial']} a {arquivo['data_final']}**;
- data de referência solicitada: **{plano['data_referencia']}**;
- {arquivo['transacoes']} lançamentos e {arquivo['identificadores_unicos']} identificadores únicos;
- {cruzamento['ja_presentes']} já existem entre as {supabase['transacoes']} transações do Supabase;
- **{cruzamento['novos']} lançamentos novos**, de {cruzamento['data_inicial_novos']} a {cruzamento['data_final_novos']};
- novos por data: {json.dumps(cruzamento['novos_por_data'], ensure_ascii=False)};
- {cruzamento['indeterminados']} lançamentos indeterminados, por motivo: {json.dumps(cruzamento['indeterminados_por_motivo'], ensure_ascii=False)};
- duplicidades internas no arquivo: {arquivo['duplicidades_internas']}.

A data de referência solicitada é {plano['data_referencia']}, enquanto o último
lançamento observado é de {arquivo['data_final']}. Portanto, esta fonte não
comprova ausência de movimentação nos dias posteriores. Os lançamentos novos
não foram importados, conciliados ou vinculados. Qualquer importação futura deve
usar a identidade bancária completa mais o `FITID` como chave idempotente,
comparar contagens antes/depois e exigir autorização própria. Indeterminados,
colisões e conteúdo divergente permanecem pendentes de conferência.
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ofx", required=True, type=Path)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--data-referencia", required=True, type=date.fromisoformat)
    parser.add_argument("--saida-json", required=True, type=Path)
    parser.add_argument("--saida-md", required=True, type=Path)
    args = parser.parse_args()
    plano = gerar_plano(
        ler_ofx(args.ofx),
        json.loads(args.snapshot.read_text(encoding="utf-8")),
        args.data_referencia,
    )
    args.saida_json.parent.mkdir(parents=True, exist_ok=True)
    args.saida_md.parent.mkdir(parents=True, exist_ok=True)
    args.saida_json.write_text(json.dumps(plano, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.saida_md.write_text(relatorio_markdown(plano), encoding="utf-8")
    print(json.dumps({
        "plano_id": plano["plano_id"],
        "modo": plano["modo"],
        "escritas_executadas": plano["escritas_executadas"],
        "novos": plano["cruzamento"]["novos"],
        "data_final": plano["arquivo"]["data_final"],
        "pendencias": plano["pendencias"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
