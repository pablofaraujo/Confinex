#!/usr/bin/env python3
"""Atualiza a curva BGI do painel usando a consulta pública oficial da B3."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import ssl
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Callable


ARTEFATO = Path(__file__).resolve().parents[1] / "dados" / "painel-boi-gordo.json"
URL_B3 = "https://cotacao.b3.com.br/mds/api/v1/DailyFluctuationHistory/{contrato}"
CODIGOS_MESES = ("F", "G", "H", "J", "K", "M", "N", "Q", "U", "V", "X", "Z")
NOMES_MESES = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")


def contexto_ssl() -> ssl.SSLContext:
    """Mantém verificação TLS também no Python.org do macOS sem CA configurada."""
    caminhos = ssl.get_default_verify_paths()
    if caminhos.cafile and Path(caminhos.cafile).is_file():
        return ssl.create_default_context()
    certificado_sistema = Path("/etc/ssl/cert.pem")
    if certificado_sistema.is_file():
        return ssl.create_default_context(cafile=str(certificado_sistema))
    return ssl.create_default_context()


def validar(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("fonte não retornou objeto JSON")
    obrigatorios = ("atualizadoEm", "fonte", "indicadores", "curvaBGI")
    faltantes = [campo for campo in obrigatorios if not payload.get(campo)]
    if faltantes:
        raise ValueError("fonte sem campos: " + ", ".join(faltantes))
    if not isinstance(payload["indicadores"], list) or not isinstance(payload["curvaBGI"], list):
        raise ValueError("indicadores e curvaBGI devem ser listas")
    return payload


def baixar(url: str, timeout: int = 30) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Confinex-Painel/2"})
    with urllib.request.urlopen(request, timeout=timeout, context=contexto_ssl()) as response:
        return validar(json.load(response))


def contratos_a_partir(data: dt.date, quantidade: int = 12) -> list[tuple[str, str]]:
    contratos = []
    ano, mes = data.year, data.month
    for deslocamento in range(quantidade):
        indice = mes - 1 + deslocamento
        ano_contrato = ano + indice // 12
        mes_contrato = indice % 12
        contrato = f"BGI{CODIGOS_MESES[mes_contrato]}{ano_contrato % 100:02d}"
        contratos.append((contrato, f"{NOMES_MESES[mes_contrato]}/{ano_contrato % 100:02d}"))
    return contratos


def _abrir_json(url: str, timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Confinex-Painel/2"})
    with urllib.request.urlopen(request, timeout=timeout, context=contexto_ssl()) as response:
        return json.load(response)


def cotacao_b3(
    contrato: str,
    mes: str,
    timeout: int = 20,
    abrir_json: Callable[[str, int], dict[str, Any]] = _abrir_json,
) -> dict[str, Any] | None:
    payload = abrir_json(URL_B3.format(contrato=contrato), timeout)
    if payload.get("BizSts", {}).get("cd") != "OK":
        return None
    piso = payload.get("TradgFlr", {})
    cotacoes = piso.get("scty", {}).get("lstQtn") or []
    if not cotacoes:
        return None
    ultima = cotacoes[-1]
    valor = ultima.get("closPric")
    data = piso.get("date")
    horario = ultima.get("dtTm") or "00:00:00"
    if not isinstance(valor, (int, float)) or valor <= 0 or not data:
        return None
    dt.date.fromisoformat(data)
    return {
        "venc": contrato,
        "mes": mes,
        "valor": round(float(valor), 2),
        "agio": None,
        "tipo": "futuro",
        "dataFonte": f"{data} {horario}",
    }


def montar_atualizacao_b3(
    anterior: dict[str, Any],
    hoje: dt.date | None = None,
    abrir_json: Callable[[str, int], dict[str, Any]] = _abrir_json,
) -> dict[str, Any]:
    hoje = hoje or dt.date.today()
    futuros = []
    for contrato, mes in contratos_a_partir(hoje):
        try:
            item = cotacao_b3(contrato, mes, abrir_json=abrir_json)
        except Exception:
            item = None
        if item:
            futuros.append(item)
    if not futuros:
        raise ValueError("B3 não retornou nenhum vencimento válido")

    data_mais_recente = max(item["dataFonte"] for item in futuros)
    data_pregao = dt.datetime.fromisoformat(data_mais_recente).date()
    if (hoje - data_pregao).days > 7:
        raise ValueError("B3 retornou somente pregão defasado")

    fisicos = [dict(item) for item in anterior.get("curvaBGI", []) if item.get("tipo") == "fisico" or str(item.get("venc", "")).startswith("Físico")]
    for item in fisicos:
        item.setdefault("tipo", "fisico")
        item.setdefault("dataFonte", anterior.get("referenciasAtualizadasEm", anterior.get("atualizadoEm")))
        item["agio"] = None

    atualizado = dict(anterior)
    atualizado.update({
        "atualizadoEm": data_mais_recente,
        "atualizadoEmB3": data_mais_recente,
        "referenciasAtualizadasEm": anterior.get("referenciasAtualizadasEm", anterior.get("atualizadoEm")),
        "fonte": "B3 oficial (curva automática); demais referências preservadas com data própria",
        "curvaBGI": fisicos + futuros,
    })
    for indicador in atualizado.get("indicadores", []):
        indicador.setdefault("dataFonte", atualizado["referenciasAtualizadasEm"])
    return validar(atualizado)


def _gravar_atomico(payload: dict[str, Any], destino: Path) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=destino.parent, delete=False) as temporario:
        json.dump(payload, temporario, ensure_ascii=False, indent=2)
        temporario.write("\n")
        nome = temporario.name
    Path(nome).replace(destino)


def atualizar(url: str | None = None, destino: Path = ARTEFATO) -> dict[str, Any]:
    if url:
        payload = baixar(url)
    else:
        anterior = validar(json.loads(destino.read_text(encoding="utf-8")))
        payload = montar_atualizacao_b3(anterior)
    _gravar_atomico(payload, destino)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", help="fonte consolidada opcional; sem ela, usa a B3 oficial")
    parser.add_argument("--destino", type=Path, default=ARTEFATO)
    args = parser.parse_args()
    try:
        payload = atualizar(args.url, args.destino)
    except Exception as erro:  # fonte externa não pode apagar o último dado válido
        print(f"Fonte indisponível; artefato preservado: {erro}")
        return 1
    print(json.dumps({"ok": True, "atualizadoEm": payload["atualizadoEm"], "fonte": payload["fonte"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
