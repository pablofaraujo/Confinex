#!/usr/bin/env python3
"""Inventário de metadados: não consulta linhas nem executa SQL/migração."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_BYTES = 10_000_000


def texto(valor):
    if not isinstance(valor, str) or not valor or len(valor) > 200 or any(ord(c) < 32 for c in valor):
        raise ValueError("metadado_invalido")
    return valor


def booleano(valor):
    if type(valor) is not bool:
        raise ValueError("booleano_invalido")
    return valor


def nomes(valores, nulo=False):
    if not isinstance(valores, list):
        raise ValueError("lista_invalida")
    return [None if v is None and nulo else texto(v) for v in valores]


def lista(valor):
    if not isinstance(valor, list):
        raise ValueError("lista_invalida")
    return valor


def assinatura(valor):
    return hashlib.sha256(json.dumps(valor, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def projetar_openapi(bruto):
    """Descarta descrições, exemplos, defaults, endpoints/RPCs e credenciais."""
    if bruto.get("swagger") != "2.0" or not isinstance(bruto.get("definitions"), dict):
        raise ValueError("openapi_nao_suportado")
    objetos = []
    for nome, definicao in sorted(bruto["definitions"].items()):
        if definicao.get("type") != "object" or not isinstance(definicao.get("properties"), dict):
            raise ValueError("definicao_invalida")
        colunas = []
        for campo, propriedade in sorted(definicao["properties"].items()):
            descricao = propriedade.get("description", "")
            if not isinstance(descricao, str):
                raise ValueError("descricao_invalida")
            colunas.append({
                "nome": texto(campo),
                "tipo": texto(propriedade.get("format") or propriedade.get("type")),
                "obrigatorio_api": campo in definicao.get("required", []),
                "pk_anotada": "<pk/>" in descricao,
                "fk_anotada": bool(re.search(r"<fk\s", descricao)),
            })
        objetos.append({"nome": texto(nome), "tipo": None, "rls": None,
                        "rls_forcada": None, "colunas": colunas})
    return {"versao": 1, "fonte": "openapi", "esquema": "exposto_pela_api",
            "somente_leitura": True, "objetos": objetos, "restricoes": None, "indices": None}


def projetar_catalogo_sql(bruto):
    if (bruto.get("versao") != 1 or bruto.get("fonte") != "pg_catalog"
            or bruto.get("esquema") != "public" or bruto.get("somente_leitura") is not True):
        raise ValueError("catalogo_sql_invalido")
    objetos, restricoes, indices = [], [], []
    for item in lista(bruto["objetos"]):
        if item["tipo"] not in ("r", "p", "v", "m"):
            raise ValueError("tipo_invalido")
        colunas = []
        for coluna in lista(item["colunas"]):
            posicao = coluna["posicao"]
            if type(posicao) is not int or posicao < 1:
                raise ValueError("posicao_invalida")
            colunas.append({"nome": texto(coluna["nome"]), "posicao": posicao,
                            "tipo": texto(coluna["tipo"]), "nao_nulo": booleano(coluna["nao_nulo"])})
        if (len({c["nome"] for c in colunas}) != len(colunas)
                or len({c["posicao"] for c in colunas}) != len(colunas)):
            raise ValueError("coluna_repetida")
        objetos.append({"nome": texto(item["nome"]), "tipo": item["tipo"],
                        "rls": booleano(item["rls"]), "rls_forcada": booleano(item["rls_forcada"]),
                        "colunas": sorted(colunas, key=lambda c: c["posicao"])})
    for item in lista(bruto["restricoes"]):
        if item["tipo"] not in ("p", "u", "f", "x"):
            raise ValueError("restricao_invalida")
        referencia = None
        if item["tipo"] == "f":
            ref = item["referencia"]
            referencia = {"esquema": texto(ref["esquema"]), "tabela": texto(ref["tabela"]),
                          "colunas": nomes(ref["colunas"])}
            if len(ref["colunas"]) != len(item["colunas"]) or not ref["colunas"]:
                raise ValueError("fk_invalida")
        restricoes.append({"tabela": texto(item["tabela"]), "nome": texto(item["nome"]),
                           "tipo": item["tipo"], "colunas": nomes(item["colunas"], nulo=item["tipo"] == "x"),
                           "referencia": referencia, "validada": booleano(item["validada"]),
                           "herdada": booleano(item["herdada"]),
                           "indice": texto(item["indice"]) if item["indice"] else None})
    for item in lista(bruto["indices"]):
        indice = {"tabela": texto(item["tabela"]), "nome": texto(item["nome"]),
                  "colunas": nomes(item["colunas"], nulo=True), "incluidas": nomes(item["incluidas"]),
                  "restricao_propria": texto(item["restricao_propria"]) if item["restricao_propria"] else None}
        for campo in ("unico", "primario", "valido", "pronto", "vivo", "parcial", "expressao", "nulos_nao_distintos"):
            indice[campo] = booleano(item[campo])
        indices.append(indice)
    if len({o["nome"] for o in objetos}) != len(objetos):
        raise ValueError("objeto_repetido")
    for itens in (restricoes, indices):
        if len({(i["tabela"], i["nome"]) for i in itens}) != len(itens):
            raise ValueError("metadado_repetido")
        if any(i["tabela"] not in {o["nome"] for o in objetos} for i in itens):
            raise ValueError("objeto_ausente")
    campos = {o["nome"]: {c["nome"] for c in o["colunas"]} for o in objetos}
    por_indice = {(i["tabela"], i["nome"]): i for i in indices}
    por_restricao = {(r["tabela"], r["nome"]): r for r in restricoes}
    for item in restricoes + indices:
        if not item["colunas"] or any(c is not None and c not in campos[item["tabela"]] for c in item["colunas"]):
            raise ValueError("coluna_ausente")
    for indice in indices:
        if any(c not in campos[indice["tabela"]] for c in indice["incluidas"]):
            raise ValueError("include_invalido")
        if (None in indice["colunas"]) != indice["expressao"]:
            raise ValueError("expressao_incoerente")
        if indice["primario"] and not indice["unico"]:
            raise ValueError("primario_nao_unico")
        propria = indice["restricao_propria"]
        if propria:
            restricao = por_restricao.get((indice["tabela"], propria))
            if not restricao or restricao["tipo"] not in ("p", "u", "x") or restricao["indice"] != indice["nome"]:
                raise ValueError("indice_sem_restricao")
        elif indice["primario"]:
            raise ValueError("primario_sem_restricao")
    for restricao in restricoes:
        if restricao["tipo"] in ("p", "u", "x"):
            indice = por_indice.get((restricao["tabela"], restricao["indice"]))
            if (not indice or indice["restricao_propria"] != restricao["nome"]
                    or indice["colunas"] != restricao["colunas"]):
                raise ValueError("restricao_sem_indice_coerente")
            if restricao["tipo"] in ("p", "u") and (not indice["unico"] or indice["parcial"] or indice["expressao"]):
                raise ValueError("unicidade_incoerente")
            if (restricao["tipo"] == "p") != indice["primario"]:
                raise ValueError("pk_incoerente")
        elif restricao["indice"] is not None:
            raise ValueError("fk_com_indice_proprio")
        ref = restricao["referencia"]
        if ref and ref["esquema"] == "public":
            if ref["tabela"] not in campos or any(c not in campos[ref["tabela"]] for c in ref["colunas"]):
                raise ValueError("referencia_ausente")
    return {"versao": 1, "fonte": "pg_catalog", "esquema": "public", "somente_leitura": True,
            "objetos": sorted(objetos, key=lambda o: o["nome"]),
            "restricoes": sorted(restricoes, key=lambda i: (i["tabela"], i["nome"])),
            "indices": sorted(indices, key=lambda i: (i["tabela"], i["nome"]))}


def resumir(catalogo):
    objetos = catalogo["objetos"]
    resumo = {"objetos": len(objetos), "colunas": sum(len(o["colunas"]) for o in objetos)}
    if catalogo["fonte"] == "openapi":
        resumo.update({"pk": None, "unique_restricoes": None, "fk": None,
                       "indices_unicos_ativos": None, "tabelas_com_rls": None,
                       "colunas_com_anotacao_pk": sum(c["pk_anotada"] for o in objetos for c in o["colunas"]),
                       "colunas_com_anotacao_fk": sum(c["fk_anotada"] for o in objetos for c in o["colunas"])})
        return resumo
    restricoes, indices = catalogo["restricoes"], catalogo["indices"]
    ativos = [i for i in indices if i["unico"] and i["valido"] and i["pronto"] and i["vivo"]]
    resumo.update({"pk": sum(c["tipo"] == "p" for c in restricoes),
                   "unique_restricoes": sum(c["tipo"] == "u" for c in restricoes),
                   "fk": sum(c["tipo"] == "f" for c in restricoes),
                   "fk_compostas": sum(c["tipo"] == "f" and len(c["colunas"]) > 1 for c in restricoes),
                   "fk_nao_validadas": sum(c["tipo"] == "f" and not c["validada"] for c in restricoes),
                   "exclusoes": sum(c["tipo"] == "x" for c in restricoes),
                   "indices_unicos_ativos": len(ativos),
                   "indices_unicos_autonomos": sum(i["restricao_propria"] is None for i in ativos),
                   "indices_unicos_parciais": sum(i["parcial"] for i in ativos),
                   "indices_unicos_expressoes": sum(i["expressao"] for i in ativos),
                   "indices_unicos_inativos": sum(i["unico"] for i in indices) - len(ativos),
                   "chaves_compostas_declaradas": sum(c["tipo"] in ("p", "u") and len(c["colunas"]) > 1 for c in restricoes),
                   "tabelas_com_rls": sum(o["rls"] for o in objetos if o["tipo"] in ("r", "p"))})
    return resumo


class SemRedirecionamento(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("redirecionamento_recusado")


def consultar_openapi(ambiente=None, abrir=None):
    ambiente = os.environ if ambiente is None else ambiente
    url = ambiente.get("SUPABASE_URL", "").rstrip("/")
    partes = urlsplit(url)
    if (partes.scheme != "https" or not re.fullmatch(r"[a-z0-9-]+\.supabase\.co", partes.netloc)
            or partes.path or partes.query or partes.fragment):
        raise ValueError("url_invalida")
    chave = ambiente.get("SUPABASE_SERVICE_KEY") or ambiente.get("SUPABASE_SERVICE_ROLE_KEY")
    if not chave or any(c.isspace() for c in chave):
        raise ValueError("credencial_ausente_ou_invalida")
    cabecalhos = {"Accept": "application/openapi+json", "apikey": chave}
    if chave.startswith("eyJ"):
        cabecalhos["Authorization"] = "Bearer " + chave
    pedido = Request(url + "/rest/v1/", headers=cabecalhos, method="GET")
    abrir = abrir or build_opener(SemRedirecionamento()).open
    with abrir(pedido, timeout=15) as resposta:
        dados = resposta.read(MAX_BYTES + 1)
    if len(dados) > MAX_BYTES:
        raise ValueError("resposta_acima_do_limite")
    return projetar_openapi(json.loads(dados))


def gerar_relatorio(antes, depois=None, acessos_rede=0):
    hash_antes = assinatura(antes)
    hash_depois = assinatura(depois) if depois is not None else None
    if depois is not None and hash_antes != hash_depois:
        raise ValueError("metadados_mudaram_durante_consulta")
    return {"plano_id": hash_antes[:12], "coletado_em_utc": datetime.now(timezone.utc).isoformat(),
            "catalogo": antes, "resumo": resumir(antes),
            "verificacao": {"assinatura_catalogo": hash_antes,
                            "assinatura_antes": hash_antes if depois is not None else None,
                            "assinatura_depois": hash_depois,
                            "metadados_inalterados": True if depois is not None else None,
                            "arquivo_inalterado": None, "linhas_operacionais_lidas": 0,
                            "dados_operacionais_verificados": False, "escritas_operacionais": 0,
                            "acessos_rede": acessos_rede},
            "limites": ["Inventário estrutural, não normalização de registros nem autorização de migração.",
                        "Assinaturas comparam metadados; não comprovam contagem/conteúdo das linhas.",
                        "RLS habilitada não significa políticas auditadas; não são exportadas políticas, defaults ou expressões.",
                        "Anotações OpenAPI não provam PK/FK completas, índices UNIQUE, chaves compostas ou RLS.",
                        "Índices parciais/de expressão não são identificadores globais; restrição e índice de suporte não se somam."]}


def markdown(relatorio):
    catalogo = relatorio["catalogo"]
    linhas = ["# Inventário de estrutura e chaves", "", f"Plano: `{relatorio['plano_id']}`.",
              f"Fonte: `{catalogo['fonte']}`. Nenhuma linha operacional consultada ou alterada.", "",
              "## Contagens", ""]
    for chave, valor in relatorio["resumo"].items():
        linhas.append(f"- {chave}: {'não verificado' if valor is None else valor}")
    linhas += ["", "## Objetos e campos", ""]
    for objeto in catalogo["objetos"]:
        nome = objeto["nome"].replace("`", "'")
        campos = ", ".join(c["nome"].replace("`", "'") for c in objeto["colunas"])
        linhas.append(f"- `{nome}`: {campos}")
    linhas += ["", "## Limites", "", *[f"- {l}" for l in relatorio["limites"]], ""]
    return "\n".join(linhas)


def salvar(relatorio, saida):
    destino = saida.resolve()
    privado = any(destino.parts[i:i + 2] == ("docs", "privado") for i in range(len(destino.parts) - 1))
    temporario = any(destino.is_relative_to(p.resolve()) for p in (Path("/tmp"), Path("/private/tmp"), Path(tempfile.gettempdir())))
    if not privado and not temporario:
        raise ValueError("saida_deve_ser_privada")
    destino.mkdir(mode=0o700, parents=True, exist_ok=False)
    for nome, conteudo in (("esquema.json", json.dumps(relatorio, ensure_ascii=False, indent=2) + "\n"),
                           ("esquema.md", markdown(relatorio))):
        with (destino / nome).open("x", encoding="utf-8") as arquivo:
            os.chmod(destino / nome, 0o600)
            arquivo.write(conteudo)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    origem = parser.add_mutually_exclusive_group(required=True)
    origem.add_argument("--arquivo", type=Path, help="JSON exportado da consulta pg_catalog somente leitura")
    origem.add_argument("--supabase", action="store_true", help="Dois GETs de OpenAPI; usa credenciais do ambiente")
    destino = parser.add_mutually_exclusive_group(required=True)
    destino.add_argument("--saida", type=Path)
    destino.add_argument("--stdout", action="store_true", help="Metadados sanitizados para transporte privado, nunca logs públicos")
    args = parser.parse_args()
    try:
        if args.supabase:
            relatorio = gerar_relatorio(consultar_openapi(), consultar_openapi(), acessos_rede=2)
        else:
            if args.arquivo.stat().st_size > MAX_BYTES:
                raise ValueError("arquivo_acima_do_limite")
            bruto = args.arquivo.read_bytes()
            catalogo = projetar_catalogo_sql(json.loads(bruto))
            if args.arquivo.read_bytes() != bruto:
                raise ValueError("arquivo_mudou")
            relatorio = gerar_relatorio(catalogo)
            relatorio["verificacao"]["assinatura_arquivo"] = hashlib.sha256(bruto).hexdigest()
            relatorio["verificacao"]["arquivo_inalterado"] = True
        if args.stdout:
            print(json.dumps(relatorio, ensure_ascii=False))
        else:
            salvar(relatorio, args.saida)
            print(json.dumps({"plano_id": relatorio["plano_id"], "resumo": relatorio["resumo"],
                              "verificacao": relatorio["verificacao"]}, ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        print("Inventário não gerado: confira acesso, formato e saída privada nova. Nenhuma escrita operacional foi executada.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
