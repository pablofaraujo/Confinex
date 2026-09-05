#!/usr/bin/env python3
"""Importa OFX somente para o staging privado, com confirmação e idempotência."""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

try:
    from exportar_snapshot_consolidacao import LeitorSupabase
    from perfilar_identidade_ofx import MAX_BYTES, extrair_ofx_privado
    from identidade_bancaria import (
        assinatura, chave_logica, comparar_conteudo, dados_basicos, decimal_assinado,
        metadados_ofx,
    )
except ModuleNotFoundError:
    from tools.exportar_snapshot_consolidacao import LeitorSupabase
    from tools.perfilar_identidade_ofx import MAX_BYTES, extrair_ofx_privado
    from tools.identidade_bancaria import (
        assinatura, chave_logica, comparar_conteudo, dados_basicos, decimal_assinado,
        metadados_ofx,
    )


NAMESPACE = uuid.UUID("93a6d927-d28d-4ed7-8c10-905554ce02eb")
TABELAS_ESCRITA = {"fontes_importacao", "transacoes_banco_staging"}


def ler_ofx(caminho: Path) -> dict[str, Any]:
    with caminho.open("rb") as entrada:
        perfil = extrair_ofx_privado(entrada.read(MAX_BYTES + 1))
    transacoes = []
    for demonstrativo in perfil["demonstrativos"]:
        identidade = demonstrativo["identidade"]
        # Preserva o endereço físico legado. A identidade completa vive nos
        # metadados; colisão de agência/moeda não é contornada com outro UUID.
        conta = ":".join(identidade[c] or "" for c in ("BANKID", "ACCTID"))
        for tx in demonstrativo["transacoes"]:
            transacoes.append({
                "fitid": tx["fitid"], "conta": conta, "banco": identidade["BANKID"],
                "data": tx["data"][:10] if tx["data"] else None,
                "tipo": tx["trntype"], "valor": tx["valor"],
                "descricao": tx["descricao"], "memo": tx["memo"],
                "dados_origem": {
                    "arquivo_sha256": perfil["sha256"], "promovido": False,
                    "ofx": {
                        "versao": 1, "identidade": identidade,
                        "identidade_sha256": assinatura(identidade),
                        "data_ofx_original": tx["data_ofx_original"],
                        "data_formato": tx["data_formato"],
                        "stmttrn_sha256": tx["stmttrn_sha256"],
                        "ocorrencias": [{"demonstrativo": demonstrativo["ordinal"],
                                         "transacao": tx["ordinal"]}],
                    },
                },
            })
    return {
        "arquivo": caminho.name, "sha256": perfil["sha256"], "transacoes": transacoes,
        "identidades_incompletas": perfil["identidades_incompletas"],
    }


def montar_plano(ofx: dict[str, Any], fontes: list[dict[str, Any]], staging: list[dict[str, Any]]) -> dict[str, Any]:
    fonte_id = str(uuid.uuid5(NAMESPACE, f"fonte:ofx:{ofx['sha256']}"))
    bloqueios, iguais, repetidas = [], 0, 0
    if ofx.get("identidades_incompletas"):
        bloqueios.append({"motivo": "demonstrativo_com_identidade_incompleta"})
    fontes_iguais = [f for f in fontes if f.get("tipo") == "ofx" and f.get("hash_sha256") == ofx["sha256"]]
    fonte_existe = bool(fontes_iguais)
    if fonte_existe:
        if len(fontes_iguais) != 1 or not fontes_iguais[0].get("id"):
            bloqueios.append({"motivo": "referencia_de_fonte_ambigua"})
        else:
            fonte_id = fontes_iguais[0]["id"]
    if any(f.get("id") == fonte_id and f not in fontes_iguais for f in fontes):
        bloqueios.append({"motivo": "colisao_uuid_fonte"})
    grupos = defaultdict(list)
    for ordinal, item in enumerate(ofx["transacoes"], 1):
        chave = chave_logica(item)
        if chave is None or dados_basicos(item) is None or not item.get("tipo"):
            bloqueios.append({"motivo": "transacao_incompleta", "ordinal": ordinal})
            continue
        grupos[chave].append(item)
    candidatas = []
    for grupo in grupos.values():
        item = grupo[0]
        if any(comparar_conteudo(item, outro) != "igual" for outro in grupo):
            bloqueios.append({"motivo": "conteudo_divergente_no_arquivo", "fitid": item["fitid"]})
            continue
        repetidas += len(grupo) - 1
        item = json.loads(json.dumps(item))  # Não modificar o objeto lido.
        metadados_ofx(item)["ocorrencias"] = [
            ref for tx in grupo for ref in metadados_ofx(tx)["ocorrencias"]
        ]
        candidatas.append(item)
    # UNIQUE e UUID continuam legados: jamais unir duas identidades por esse rótulo.
    fisicas = Counter((i["conta"], i["fitid"]) for i in candidatas)
    uuids = Counter(str(uuid.uuid5(NAMESPACE, f"transacao:{i['conta']}:{i['fitid']}")) for i in candidatas)
    novos = []
    for item in candidatas:
        fisica = (item["conta"], item["fitid"])
        tx_id = str(uuid.uuid5(NAMESPACE, f"transacao:{item['conta']}:{item['fitid']}"))
        motivo = None
        if fisicas[fisica] > 1:
            motivo = "colisao_identidades_na_chave_legada"
        if uuids[tx_id] > 1:
            motivo = "colisao_uuid_entre_candidatas"
        relevantes = [r for r in staging if r.get("fitid") == item["fitid"] or r.get("id") == tx_id]
        mesmas = []
        for outro in relevantes:
            chave_outro = chave_logica(outro)
            if chave_outro is None:
                motivo = "legado_sem_prova_de_identidade"
            elif chave_outro == chave_logica(item):
                mesmas.append(outro)
            elif (outro.get("conta"), outro.get("fitid")) == fisica or outro.get("id") == tx_id:
                motivo = "colisao_identidades_na_chave_legada"
        if len(mesmas) > 1:
            motivo = "referencia_ambigua_no_staging"
        elif mesmas:
            comparacao = comparar_conteudo(item, mesmas[0])
            if comparacao != "igual":
                motivo = "conteudo_divergente_no_staging" if comparacao == "divergente" else "conteudo_legado_incompleto"
            elif not mesmas[0].get("id"):
                motivo = "referencia_ambigua_no_staging"
        if motivo:
            bloqueios.append({"motivo": motivo, "fitid": item["fitid"]})
        elif mesmas:
            iguais += 1
        else:
            novos.append({"id": tx_id, "fonte_importacao_id": fonte_id, **item, "estado": "nao_revisada"})
    datas = sorted(item["data"] for item in ofx["transacoes"] if item.get("data"))
    fonte = {
        "id": fonte_id,
        "tipo": "ofx",
        "nome_arquivo": ofx["arquivo"],
        "hash_sha256": ofx["sha256"],
        "periodo_inicio": min(datas) if datas else None,
        "periodo_fim": max(datas) if datas else None,
        "quantidade_registros": len(ofx["transacoes"]),
        "origem_canal": "arquivo_enviado",
        "origem_referencia": ofx["arquivo"],
        "estado": "importada_staging",
        "metadados": {
            "formato": "ofx", "promovido_para_operacional": False,
            # Preserva a evidência de todas as ocorrências, também em novo
            # arquivo cujo lançamento já esteja no staging. Sem copiar MEMO.
            "ofx": {"versao": 1, "ocorrencias": [
                {"fitid": t["fitid"], **metadados_ofx(t)} for t in ofx["transacoes"]
            ]},
        },
        "criado_por": "codex",
    }
    plano = {
        "modo": "dry_run",
        "executavel": not bloqueios,
        "bloqueios": bloqueios,
        "snapshot_sha256": assinatura({"fontes": fontes, "staging": staging}),
        "fonte_existe": fonte_existe,
        "fonte": fonte,
        "transacoes": novos,
        "resumo": {
            "transacoes_no_arquivo": len(ofx["transacoes"]),
            "transacoes_ja_no_staging": iguais,
            "transacoes_novas": len(novos),
            "repeticoes_identicas_no_arquivo": repetidas,
            "bloqueios_por_motivo": dict(sorted(Counter(b["motivo"] for b in bloqueios).items())),
            "escritas_executadas": 0,
            "periodo_inicio": min(datas) if datas else None,
            "periodo_fim": max(datas) if datas else None,
            "tabelas_operacionais_alteradas": 0,
        },
    }
    plano["plano_id"] = assinatura(plano)[:12]
    return plano


class EscritorStaging:
    def __init__(self, url: str, chave: str, timeout: int = 20) -> None:
        self.url, self.chave = url.rstrip("/"), chave
        self.timeout = max(1, min(int(timeout), 20))

    def inserir(self, tabela: str, payload: dict[str, Any]) -> None:
        if tabela not in TABELAS_ESCRITA:
            raise ValueError(f"escrita não permitida: {tabela}")
        requisicao = urllib.request.Request(
            f"{self.url}/rest/v1/{tabela}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "apikey": self.chave,
                "Authorization": f"Bearer {self.chave}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
        )
        try:
            with urllib.request.urlopen(requisicao, timeout=self.timeout) as resposta:
                if resposta.status not in {200, 201}:
                    raise RuntimeError(f"HTTP inesperado em {tabela}: {resposta.status}")
                recebidos = json.loads(resposta.read().decode("utf-8"))
                if not isinstance(recebidos, list) or len(recebidos) != 1 or not isinstance(recebidos[0], dict):
                    raise RuntimeError("resultado de escrita não comprovado; conferir antes de retomar")
                for campo, valor in payload.items():
                    recebido = recebidos[0].get(campo)
                    igual = decimal_assinado(recebido) == decimal_assinado(valor) if campo == "valor" else recebido == valor
                    if campo not in recebidos[0] or not igual:
                        raise RuntimeError("conteúdo gravado não comprovado; conferir antes de retomar")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"escrita em {tabela} falhou com HTTP {exc.code}") from exc


def executar(plano: dict[str, Any], escritor: EscritorStaging) -> dict[str, int]:
    conteudo = {c: v for c, v in plano.items() if c != "plano_id"}
    if assinatura(conteudo)[:12] != plano.get("plano_id"):
        raise ValueError("plano alterado; refazer a conferência")
    if not plano.get("executavel") or plano.get("bloqueios"):
        raise ValueError("importação bloqueada; resolver identidade/conteúdo antes de gravar")
    if not plano["fonte_existe"]:
        escritor.inserir("fontes_importacao", plano["fonte"])
    for item in plano["transacoes"]:
        escritor.inserir("transacoes_banco_staging", item)
    return {
        "fontes_criadas": 0 if plano["fonte_existe"] else 1,
        "transacoes_criadas": len(plano["transacoes"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ofx", required=True, type=Path)
    parser.add_argument("--snapshot", type=Path, help="snapshot privado local; proíbe --executar e não acessa a rede")
    parser.add_argument("--executar", action="store_true")
    parser.add_argument("--confirmacao")
    args = parser.parse_args()
    if args.snapshot and args.executar:
        raise SystemExit("snapshot local permite somente simulação, nunca execução")
    ofx = ler_ofx(args.ofx)
    url = os.environ.get("SUPABASE_URL") or os.environ.get("CONFINEX_DB_URL") or ""
    chave = (
        os.environ.get("SUPABASE_SERVICE_KEY")
        or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or os.environ.get("CONFINEX_DB_KEY")
        or ""
    )
    if args.snapshot:
        with args.snapshot.open("rb") as entrada:
            bruto = entrada.read(50_000_001)
        if len(bruto) > 50_000_000:
            raise ValueError("snapshot acima do limite")
        snapshot = json.loads(bruto)
        tabelas = snapshot.get("tabelas") if isinstance(snapshot, dict) else None
        if not isinstance(tabelas, dict) or any(
            not isinstance(tabelas.get(t), list) or not all(isinstance(i, dict) for i in tabelas[t])
            for t in ("fontes_importacao", "transacoes_banco_staging")
        ):
            raise ValueError("snapshot incompleto; exige fontes e staging explicitamente")
        fontes, staging = tabelas["fontes_importacao"], tabelas["transacoes_banco_staging"]
    else:
        leitor = LeitorSupabase(url, chave)
        fontes, staging = leitor.listar("fontes_importacao"), leitor.listar("transacoes_banco_staging")
    plano = montar_plano(ofx, fontes, staging)
    resultado = {"fontes_criadas": 0, "transacoes_criadas": 0}
    if args.executar:
        esperada = f"IMPORTAR OFX STAGING {plano['plano_id']}"
        if args.confirmacao != esperada:
            raise SystemExit(f"confirmação inválida; use: {esperada}")
        resultado = executar(plano, EscritorStaging(url, chave))
        plano["modo"] = "executado"
    print(json.dumps({
        "plano_id": plano["plano_id"],
        "modo": plano["modo"],
        "resumo": plano["resumo"],
        "executavel": plano["executavel"],
        **resultado,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
