#!/usr/bin/env python3
"""Catálogo local de chaves: lê XLSX/CSV/JSON e nunca acessa a rede."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import posixpath
import re
import sys
import tempfile
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

try:
    from catalogo_chaves import perfilar_relacao, perfilar_tabela
except ModuleNotFoundError:
    from tools.catalogo_chaves import perfilar_relacao, perfilar_tabela

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
MAX_BYTES = 40 * 1024 * 1024
MAX_XML = 160 * 1024 * 1024
MAX_REGISTROS = 100_000
MAX_CELULAS = 1_000_000


def ler_bytes(caminho: Path) -> bytes:
    with caminho.open("rb") as arquivo:
        dados = arquivo.read(MAX_BYTES + 1)
    if len(dados) > MAX_BYTES:
        raise ValueError("fonte_acima_do_limite")
    return dados


def assinatura(dados: bytes) -> str:
    return hashlib.sha256(dados).hexdigest()


def inteiro_positivo(valor: object) -> int:
    if isinstance(valor, bool) or not isinstance(valor, int) or valor < 1:
        raise ValueError("numero_de_linha_invalido")
    return valor


def xml_seguro(dados: bytes) -> ET.Element:
    # OOXML de exportação é UTF-8; recusar outras codificações também impede
    # contornar a verificação de entidades com bytes intercalados de UTF-16.
    texto = dados.decode("utf-8-sig")
    if "\x00" in texto or "<!DOCTYPE" in texto.upper() or "<!ENTITY" in texto.upper():
        raise ValueError("xml_com_declaracao_nao_permitida")
    return ET.fromstring(texto)


def ler_xlsx(dados: bytes, fonte: dict) -> tuple[list[dict], list[str], dict]:
    """Mantém endereços de colunas e valores armazenados; não calcula fórmulas."""
    cabecalho = inteiro_positivo(fonte.get("linha_cabecalho", 0))
    fim = inteiro_positivo(fonte.get("linha_final", MAX_REGISTROS))
    if fim <= cabecalho:
        raise ValueError("intervalo_de_linhas_invalido")
    ignorar = {inteiro_positivo(v) for v in fonte.get("linhas_ignorar", [])}
    with ZipFile(io.BytesIO(dados)) as pacote:
        infos = pacote.infolist()
        if len(infos) > 6000 or sum(i.file_size for i in infos) > MAX_XML:
            raise ValueError("xlsx_acima_do_limite")
        if len({i.filename for i in infos}) != len(infos):
            raise ValueError("xlsx_com_entradas_repetidas")
        wb = xml_seguro(pacote.read("xl/workbook.xml"))
        rels = xml_seguro(pacote.read("xl/_rels/workbook.xml.rels"))
        aba = next((a for a in wb.findall("m:sheets/m:sheet", NS)
                    if a.get("name") == fonte.get("aba")), None)
        if aba is None:
            raise ValueError("aba_nao_encontrada")
        rel = next((r for r in rels if r.get("Id") == aba.get(REL)), None)
        if rel is None or rel.get("TargetMode") == "External":
            raise ValueError("referencia_de_aba_invalida")
        alvo = rel.get("Target", "")
        membro = posixpath.normpath(alvo.lstrip("/") if alvo.startswith("/")
                                    else posixpath.join("xl", alvo))
        if not membro.startswith("xl/") or ".." in membro.split("/"):
            raise ValueError("referencia_de_aba_invalida")
        strings = []
        if "xl/sharedStrings.xml" in pacote.namelist():
            raiz = xml_seguro(pacote.read("xl/sharedStrings.xml"))
            strings = ["".join(t.text or "" for t in si.findall(".//m:t", NS))
                       for si in raiz.findall("m:si", NS)]
        raiz = xml_seguro(pacote.read(membro))
        registros, rotulos, campos = [], {}, []
        formulas, formulas_sem_cache, erros, celulas = 0, 0, 0, 0
        cabecalho_lido = False
        linhas_lidas, numeros_vistos = [], set()
        for linha in raiz.findall("m:sheetData/m:row", NS):
            numero = int(linha.get("r", "0"))
            if numero < cabecalho or numero > fim or numero in ignorar:
                continue
            if numero in numeros_vistos:
                raise ValueError("linha_repetida")
            numeros_vistos.add(numero)
            registro = {}
            tem_formula_ou_erro = False
            for celula in linha.findall("m:c", NS):
                celulas += 1
                if celulas > MAX_CELULAS:
                    raise ValueError("xlsx_acima_do_limite")
                endereco = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]*)", celula.get("r", ""))
                if not endereco or int(endereco[2]) != numero:
                    raise ValueError("endereco_de_celula_invalido")
                coluna = endereco[1]
                if coluna in registro:
                    raise ValueError("celula_repetida")
                valor_xml = celula.find("m:v", NS)
                bruto = valor_xml.text if valor_xml is not None else None
                tipo = celula.get("t", "n")
                formula = celula.find("m:f", NS) is not None
                tem_formula_ou_erro = tem_formula_ou_erro or formula or tipo == "e"
                if formula and numero != cabecalho:
                    formulas += 1
                    formulas_sem_cache += bruto is None
                if tipo == "inlineStr":
                    valor = "".join(t.text or "" for t in celula.findall(".//m:t", NS))
                elif tipo == "s" and bruto is not None:
                    indice = int(bruto)
                    if indice < 0 or indice >= len(strings):
                        raise ValueError("indice_de_texto_invalido")
                    valor = strings[indice]
                elif tipo == "e":
                    erros += numero != cabecalho
                    valor = None
                elif bruto is None:
                    valor = None
                elif tipo == "b":
                    if bruto not in ("0", "1"):
                        raise ValueError("booleano_invalido")
                    valor = bruto == "1"
                elif tipo in ("str", "d"):
                    valor = bruto
                else:
                    valor = Decimal(bruto)
                    if not valor.is_finite():
                        raise ValueError("numero_nao_finito")
                registro[coluna] = valor
            if numero == cabecalho:
                cabecalho_lido = True
                campos = list(registro)
                rotulos = {c: str(v) if v is not None else "Sem título" for c, v in registro.items()}
                continue
            # Uma linha só com fórmula sem cache/erro também precisa aparecer
            # como incompleta; omiti-la produziria uma falsa chave única.
            if tem_formula_ou_erro or any(v is not None and str(v).strip() for v in registro.values()):
                for c in registro:
                    if c not in campos:
                        campos.append(c)
                        rotulos[c] = "Sem título"
                registros.append(registro)
                linhas_lidas.append(numero)
                if len(registros) > MAX_REGISTROS:
                    raise ValueError("registros_acima_do_limite")
        if not cabecalho_lido or not rotulos:
            raise ValueError("cabecalho_nao_encontrado")
        return registros, campos, {
            "rotulos": rotulos, "aba": fonte["aba"], "linha_cabecalho": cabecalho, "linha_final": fim,
            "linhas_ignoradas_explicitamente": sorted(ignorar),
            "linhas_lidas": linhas_lidas,
            "formulas": formulas, "formulas_sem_valor_armazenado": formulas_sem_cache,
            "celulas_com_erro": erros,
            "aviso": "Fórmulas não executadas. Cache pode estar desatualizado; datas numéricas não são reinterpretadas.",
        }


def ler_tabela(dados: bytes, extensao: str, fonte: dict) -> tuple[list[dict], list[str], dict]:
    if extensao == ".xlsx":
        return ler_xlsx(dados, fonte)
    if extensao == ".json":
        bruto = json.loads(dados.decode("utf-8-sig"), parse_float=Decimal,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("numero_nao_finito")))
        if isinstance(bruto, dict) and "tabelas" in bruto:
            bruto = bruto["tabelas"]
        if isinstance(bruto, dict):
            bruto = bruto.get(fonte.get("tabela"))
        if not isinstance(bruto, list) or not all(isinstance(r, dict) for r in bruto):
            raise ValueError("json_deve_conter_registros")
        registros = bruto
        campos = list(dict.fromkeys(c for r in registros for c in r))
        if not registros:
            campos = fonte.get("campos", [])
    elif extensao in (".csv", ".tsv"):
        separador = fonte.get("separador", "\t" if extensao == ".tsv" else ";")
        if separador not in (",", ";", "\t"):
            raise ValueError("separador_invalido")
        leitor = csv.reader(io.StringIO(dados.decode("utf-8-sig"), newline=""), delimiter=separador)
        cabecalho = inteiro_positivo(fonte.get("linha_cabecalho", 1))
        nomes = []
        for _ in range(cabecalho):
            nomes = next(leitor, [])
        if not nomes or any(not c.strip() for c in nomes) or len(set(nomes)) != len(nomes):
            raise ValueError("cabecalho_vazio_ou_repetido")
        campos, registros = nomes, []
        for linha in leitor:
            if not linha or not any(v.strip() for v in linha):
                continue
            if len(linha) != len(campos):
                raise ValueError("quantidade_de_colunas_invalida")
            registros.append(dict(zip(campos, linha)))
            if len(registros) > MAX_REGISTROS:
                raise ValueError("registros_acima_do_limite")
    else:
        raise ValueError("formato_nao_suportado")
    if len(registros) > MAX_REGISTROS or sum(len(r) for r in registros) > MAX_CELULAS:
        raise ValueError("registros_acima_do_limite")
    return registros, campos, {"rotulos": {c: c for c in campos}}


def gerar_catalogo(manifesto: dict, diretorio: Path) -> dict:
    fontes = manifesto.get("fontes", [])
    if manifesto.get("versao") != 1 or not isinstance(fontes, list) or not 1 <= len(fontes) <= 50:
        raise ValueError("manifesto_invalido")
    tabelas, catalogo, assinaturas = {}, [], {}
    for fonte in fontes:
        nome = fonte.get("id", "")
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", nome) or nome in tabelas:
            raise ValueError("identificador_de_fonte_invalido_ou_repetido")
        caminho = (diretorio / fonte["arquivo"]).resolve()
        if caminho not in assinaturas:
            dados = ler_bytes(caminho)
            assinaturas[caminho] = assinatura(dados)
        else:
            dados = ler_bytes(caminho)
            if assinatura(dados) != assinaturas[caminho]:
                raise ValueError("fonte_alterada_durante_leitura")
        registros, campos, metadados = ler_tabela(dados, caminho.suffix.lower(), fonte)
        tabelas[nome] = (registros, campos)
        perfil = perfilar_tabela(registros, campos, fonte.get("chaves", []))
        catalogo.append({"fonte": nome, "sha256_antes": assinaturas[caminho],
                         "metadados": metadados, **perfil})
    relacoes = []
    for rel in manifesto.get("relacoes", []):
        origem, destino = rel["origem"], rel["destino"]
        if origem not in tabelas or destino not in tabelas:
            raise ValueError("fonte_da_relacao_inexistente")
        for nome, cols in ((origem, rel["campos_origem"]), (destino, rel["campos_destino"])):
            if not cols or any(c not in tabelas[nome][1] for c in cols):
                raise ValueError("coluna_da_relacao_inexistente")
        relacoes.append({"origem": origem, "destino": destino,
                        "campos_origem": rel["campos_origem"], "campos_destino": rel["campos_destino"],
                        **perfilar_relacao(tabelas[origem][0], rel["campos_origem"],
                                           tabelas[destino][0], rel["campos_destino"])})
    for caminho, antes in assinaturas.items():
        if assinatura(ler_bytes(caminho)) != antes:
            raise ValueError("fonte_alterada_durante_leitura")
    for perfil in catalogo:
        perfil["sha256_depois"] = perfil["sha256_antes"]
    hash_manifesto = assinatura(json.dumps(manifesto, sort_keys=True, ensure_ascii=False).encode())
    base = {"versao": 1, "modo": "diagnostico_local_somente_leitura",
            "manifesto_sha256": hash_manifesto,
            "escritas_operacionais": 0, "acessos_rede": 0, "fontes_preservadas": True,
            "catalogo": catalogo, "relacoes": relacoes,
            "limites": ["Unicidade na amostra não confirma chave de negócio.",
                        "Campos vazios e fontes históricas não provam ausência na base atual.",
                        "Normalização auxiliar aponta colisões; não altera valores nem vínculos."]}
    base["plano_id"] = assinatura(json.dumps(base, sort_keys=True, ensure_ascii=False).encode())[:12]
    return base


def celula_md(valor: object) -> str:
    return str(valor).replace("|", "\\|").replace("\n", " ").replace("\r", " ").replace("<", "&lt;").replace(">", "&gt;")


def relatorio_markdown(plano: dict) -> str:
    linhas = ["# Catálogo de chaves e relacionamentos", "", f"Plano: {plano['plano_id']}", "",
              "Diagnóstico somente leitura. Nenhuma chave, índice, vínculo ou registro foi alterado.", "",
              "Os números se referem às fontes e aos intervalos configurados no manifesto.", ""]
    for fonte in plano["catalogo"]:
        linhas += [f"## {fonte['fonte']}", "", f"Registros: {fonte['registros']}", "",
                   "| Campo | Rótulo | Preenchidos | Nulos | Valores distintos | Grupos repetidos |",
                   "|---|---|---:|---:|---:|---:|"]
        for c in fonte["campos"]:
            linhas.append("| " + " | ".join(celula_md(v) for v in (
                c["campo"], fonte["metadados"]["rotulos"].get(c["campo"], c["campo"]),
                c["preenchidos"], c["nulos"], c["distintos"], c["grupos_duplicados"])) + " |")
        linhas += ["", "### Chaves configuradas", ""]
        for chave in fonte["chaves"]:
            linhas.append(f"- {celula_md(chave['nome'])}: {chave['distintos']} valores distintos; "
                          f"{chave['incompletos']} incompletos; {chave['grupos_duplicados']} grupos repetidos; "
                          f"{chave['colisoes_normalizacao']} colisões na comparação padronizada.")
        if not fonte["chaves"]:
            linhas.append("Nenhuma chave composta declarada; campos acima são apenas candidatos estatísticos.")
        if "aviso" in fonte["metadados"]:
            linhas += ["", fonte["metadados"]["aviso"],
                       f"Fórmulas sem cache: {fonte['metadados']['formulas_sem_valor_armazenado']}; "
                       f"células com erro: {fonte['metadados']['celulas_com_erro']}."]
        linhas += ["", "Assinaturas do arquivo antes/depois: idênticas.", ""]
    linhas += ["## Relações configuradas", ""]
    for r in plano["relacoes"]:
        linhas.append(f"- {r['origem']} → {r['destino']}: {r['cardinalidade_observada']}; "
                      f"{r['correspondentes']} registros correspondentes; {r['orfaos']} órfãos; "
                      f"{r['grupos_ambiguos_destino']} grupos ambíguos no destino.")
    if not plano["relacoes"]:
        linhas.append("Nenhuma relação declarada. Semelhança de nomes de colunas não confirma vínculo.")
    linhas += ["", "## Limites", "", *[f"- {x}" for x in plano["limites"]], ""]
    return "\n".join(linhas)


def salvar_relatorios(plano: dict, saida: Path) -> None:
    # Diretório novo: não sobrescreve fonte, manifesto ou relatório de outra execução.
    resolvido = saida.resolve()
    partes = resolvido.parts
    privado = any(partes[i:i + 2] == ("docs", "privado") for i in range(len(partes) - 1))
    temporario = any(resolvido.is_relative_to(p.resolve()) for p in
                    (Path("/tmp"), Path("/private/tmp"), Path(tempfile.gettempdir())))
    if not privado and not temporario:
        raise ValueError("saida_deve_ser_privada")
    arquivos = (("catalogo.json", json.dumps(plano, ensure_ascii=False, indent=2) + "\n"),
                ("catalogo.md", relatorio_markdown(plano)))
    saida.mkdir(mode=0o700, parents=True, exist_ok=False)
    for nome, conteudo in arquivos:
        caminho = saida / nome
        with caminho.open("x", encoding="utf-8") as arquivo:
            os.chmod(caminho, 0o600)
            arquivo.write(conteudo)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifesto", required=True, type=Path)
    parser.add_argument("--saida", required=True, type=Path)
    args = parser.parse_args()
    try:
        bruto = ler_bytes(args.manifesto)
        plano = gerar_catalogo(json.loads(bruto), args.manifesto.resolve().parent)
        if ler_bytes(args.manifesto) != bruto:
            raise ValueError("manifesto_alterado_durante_leitura")
        salvar_relatorios(plano, args.saida)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError, BadZipFile, ET.ParseError, ArithmeticError):
        # Não ecoar caminhos, conteúdo de células ou mensagens vindas de arquivos privados.
        print("Não foi possível gerar o catálogo. Confira formato, manifesto, intervalo e diretório de saída novo.", file=sys.stderr)
        return 1
    print(json.dumps({"plano_id": plano["plano_id"], "fontes": len(plano["catalogo"]),
                      "relacoes": len(plano["relacoes"]), "fontes_preservadas": True,
                      "escritas_operacionais": 0, "acessos_rede": 0}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
