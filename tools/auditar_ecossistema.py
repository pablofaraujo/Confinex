#!/usr/bin/env python3
"""Auditoria permanente de páginas, dependências, menu e navegação do Confinex.

O modo estático não usa rede. Com ``--navegador``, um servidor local efêmero é
iniciado e o Chromium percorre as páginas em desktop e celular. O relatório
sempre distingue ``aprovado``, ``falhou`` e ``não testado``.
"""

from __future__ import annotations

import argparse
import contextlib
import http.server
import json
import re
import socket
import subprocess
import sys
import tempfile
import threading
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / "js" / "cfagro-shell.js"
FALHAS_CONHECIDAS = ROOT / "tools" / "auditoria_falhas_conhecidas.json"
ARTEFATOS = ROOT / "artifacts" / "auditoria-ecossistema"
STATUS_VALIDOS = {"aprovado", "falhou", "não testado"}
EXTERNOS_NOVA_JANELA = {"Datamars Livestock", "AgroNota", "IMA / SIDAGRO"}
EXTERNOS_MESMA_JANELA = {"Portfolio B3"}


@dataclass(frozen=True)
class ItemMenu:
    rotulo: str
    href: str
    externo: bool


@dataclass(frozen=True)
class Resultado:
    id: str
    requisito: str
    cenario: str
    esperado: str
    status: str
    evidencia: str
    camada: str = "estática"

    def __post_init__(self) -> None:
        if self.status not in STATUS_VALIDOS:
            raise ValueError(f"status inválido: {self.status}")


class InventarioHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.recursos: list[tuple[str, str]] = []
        self.links: list[dict[str, str]] = []
        self.scripts_inline: list[str] = []
        self._em_script = False
        self._script_src = ""
        self._script: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        dados = {chave: valor or "" for chave, valor in attrs}
        if dados.get("id"):
            self.ids.add(dados["id"])
        if tag == "a" and dados.get("href"):
            self.links.append(dados)
        if tag == "script":
            self._em_script = True
            self._script_src = dados.get("src", "")
            self._script = []
            if self._script_src:
                self.recursos.append(("script", self._script_src))
        elif tag == "link" and dados.get("href"):
            self.recursos.append(("link", dados["href"]))
        elif tag in {"img", "source"} and dados.get("src"):
            self.recursos.append((tag, dados["src"]))

    def handle_data(self, data: str) -> None:
        if self._em_script and not self._script_src:
            self._script.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._em_script:
            codigo = "".join(self._script).strip()
            if codigo:
                self.scripts_inline.append(codigo)
            self._em_script = False
            self._script_src = ""
            self._script = []


def ler_html(path: Path) -> InventarioHTML:
    inventario = InventarioHTML()
    inventario.feed(path.read_text(encoding="utf-8"))
    return inventario


def extrair_menu(shell: Path = SHELL) -> list[ItemMenu]:
    if not shell.exists():
        return []
    fonte = shell.read_text(encoding="utf-8")
    bloco = re.search(r"var\s+NAV\s*=\s*\[(.*?)\];", fonte, re.S)
    if not bloco:
        return []
    itens: list[ItemMenu] = []
    for objeto in re.findall(r"\{([^{}]+)\}", bloco.group(1)):
        href = re.search(r"href\s*:\s*['\"]([^'\"]+)['\"]", objeto)
        rotulo = re.search(r"rotulo\s*:\s*['\"]([^'\"]+)['\"]", objeto)
        if href and rotulo:
            itens.append(
                ItemMenu(
                    rotulo=rotulo.group(1),
                    href=href.group(1),
                    externo=bool(re.search(r"\bext\s*:\s*true\b", objeto)),
                )
            )
    return itens


def paginas_html(root: Path = ROOT) -> list[Path]:
    return sorted(root.glob("*.html"))


def eh_url_externa(href: str) -> bool:
    return bool(re.match(r"^https?://", href, re.I))


def destino_local(href: str, root: Path = ROOT) -> tuple[Path, str]:
    parsed = urlparse(href)
    caminho = unquote(parsed.path)
    if caminho in {"", ".", "./", "/"}:
        arquivo = "index.html"
    else:
        arquivo = caminho.removeprefix("./").removeprefix("/")
        if arquivo.endswith("/"):
            arquivo += "index.html"
    return root / arquivo, parsed.fragment


def redirect_da_pagina(path: Path) -> str | None:
    texto = path.read_text(encoding="utf-8")
    match = re.search(
        r"location\.(?:replace|assign)\(\s*['\"]([^'\"]+)['\"]\s*\)",
        texto,
        re.I,
    )
    return match.group(1) if match else None


def resultado(
    identificador: str,
    requisito: str,
    cenario: str,
    esperado: str,
    ok: bool,
    evidencia: str,
) -> Resultado:
    return Resultado(
        id=identificador,
        requisito=requisito,
        cenario=cenario,
        esperado=esperado,
        status="aprovado" if ok else "falhou",
        evidencia=evidencia,
    )


def auditar_estatico(
    root: Path = ROOT,
    shell: Path | None = None,
) -> tuple[list[Resultado], dict[str, Any]]:
    shell = shell or root / "js" / "cfagro-shell.js"
    paginas = paginas_html(root)
    menu = extrair_menu(shell)
    saida: list[Resultado] = []
    inventarios: dict[Path, InventarioHTML] = {
        pagina: ler_html(pagina) for pagina in paginas
    }

    saida.append(
        resultado(
            "inventario:paginas",
            "Inventariar páginas HTML",
            "repositório com páginas ou vazio",
            "ao menos uma página HTML é encontrada",
            bool(paginas),
            f"{len(paginas)} página(s): "
            + (", ".join(p.name for p in paginas) if paginas else "nenhuma"),
        )
    )
    saida.append(
        resultado(
            "inventario:menu",
            "Inventariar itens de menu",
            "manifesto NAV do shell",
            "ao menos um item navegável é encontrado",
            bool(menu),
            f"{len(menu)} item(ns) navegáveis",
        )
    )

    for pagina, inv in inventarios.items():
        for tipo, referencia in inv.recursos:
            if (
                not referencia
                or referencia.startswith(("data:", "#", "mailto:", "javascript:"))
                or eh_url_externa(referencia)
            ):
                continue
            destino, _ = destino_local(referencia, root)
            saida.append(
                resultado(
                    f"recurso:{pagina.name}:{referencia}",
                    "Dependências locais existem",
                    f"{pagina.name} carrega {tipo} {referencia}",
                    "o arquivo referenciado existe",
                    destino.exists(),
                    str(destino.relative_to(root))
                    if destino.exists()
                    else f"arquivo inexistente: {destino.relative_to(root)}",
                )
            )

    for item in menu:
        prefixo = f"menu:{item.rotulo}"
        if eh_url_externa(item.href):
            mesma_janela = item.rotulo in EXTERNOS_MESMA_JANELA
            nova_janela = item.rotulo in EXTERNOS_NOVA_JANELA
            politica_conhecida = mesma_janela or nova_janela
            saida.append(
                resultado(
                    f"{prefixo}:politica_externa",
                    "Links externos têm política explícita",
                    f"menu {item.rotulo}",
                    "o destino externo é classificado",
                    politica_conhecida,
                    (
                        "mesma janela"
                        if mesma_janela
                        else "nova janela justificada"
                        if nova_janela
                        else "sem justificativa cadastrada"
                    ),
                )
            )
            saida.append(
                resultado(
                    f"{prefixo}:mesma_janela",
                    "Abertura de janela é justificada",
                    f"menu {item.rotulo}",
                    (
                        "navega na mesma janela"
                        if mesma_janela
                        else "abre nova janela por ser ferramenta externa"
                    ),
                    (not item.externo) if mesma_janela else item.externo,
                    f"ext={str(item.externo).lower()} href={item.href}",
                )
            )
            continue

        destino, ancora = destino_local(item.href, root)
        existe = destino.exists()
        saida.append(
            resultado(
                f"{prefixo}:arquivo",
                "Todo item interno do menu tem arquivo",
                f"menu {item.rotulo} → {item.href}",
                "o arquivo de destino existe",
                existe,
                (
                    str(destino.relative_to(root))
                    if existe
                    else f"arquivo inexistente: {destino.relative_to(root)}"
                ),
            )
        )
        if not existe:
            continue

        redirect = redirect_da_pagina(destino)
        saida.append(
            resultado(
                f"{prefixo}:sem_redirect_home",
                "Menu não redireciona silenciosamente à Visão Geral",
                f"abrir {item.rotulo}",
                "o destino não executa redirecionamento legado",
                redirect is None,
                f"sem redirect em {destino.name}"
                if redirect is None
                else f"{destino.name} redireciona para {redirect}",
            )
        )

        inv_destino = inventarios.get(destino) or ler_html(destino)
        destino_real = not ancora or ancora in inv_destino.ids
        saida.append(
            resultado(
                f"{prefixo}:destino_real",
                "Item do menu possui destino real",
                f"acessar {item.href}",
                "arquivo existe e a âncora, quando usada, existe",
                destino_real,
                (
                    f"{destino.name}#{ancora} existe"
                    if ancora and destino_real
                    else f"âncora inexistente: {destino.name}#{ancora}"
                    if ancora
                    else destino.name
                ),
            )
        )

    inventario = {
        "paginas": [
            {
                "arquivo": pagina.name,
                "redirect": redirect_da_pagina(pagina),
                "scripts": [
                    ref for tipo, ref in inventarios[pagina].recursos if tipo == "script"
                ],
            }
            for pagina in paginas
        ],
        "menu": [asdict(item) for item in menu],
        "scripts": sorted(
            {
                ref
                for inv in inventarios.values()
                for tipo, ref in inv.recursos
                if tipo == "script"
            }
        ),
        "testes": sorted(
            str(path.relative_to(root))
            for path in (
                list((root / "tools").glob("test_*.py"))
                + list((root / "tools").glob("test_*.js"))
            )
            if path.is_file()
        )
        if (root / "tools").exists()
        else [],
        "workflows": sorted(
            str(path.relative_to(root))
            for path in (root / ".github" / "workflows").glob("*")
            if path.is_file() and path.suffix in {".yml", ".yaml"}
        )
        if (root / ".github" / "workflows").exists()
        else [],
        "rotas_publicadas": [
            pagina["arquivo"]
            for pagina in [
                {
                    "arquivo": path.name,
                    "redirect": redirect_da_pagina(path),
                }
                for path in paginas
            ]
            if not pagina["redirect"]
        ],
    }
    return saida, inventario


class ServidorSilencioso(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return


@contextlib.contextmanager
def servidor_local(root: Path) -> Iterable[str]:
    with socket.socket() as reserva:
        reserva.bind(("127.0.0.1", 0))
        porta = reserva.getsockname()[1]
    handler = lambda *args, **kwargs: ServidorSilencioso(  # noqa: E731
        *args, directory=str(root), **kwargs
    )
    servidor = http.server.ThreadingHTTPServer(("127.0.0.1", porta), handler)
    thread = threading.Thread(target=servidor.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{porta}/"
    finally:
        servidor.shutdown()
        servidor.server_close()
        thread.join(timeout=5)


def auditar_navegador(
    inventario: dict[str, Any],
    *,
    root: Path = ROOT,
    artefatos: Path = ARTEFATOS,
    base_url: str | None = None,
) -> list[Resultado]:
    artefatos.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="confinex-auditoria-") as pasta:
        config = Path(pasta) / "config.json"
        bruto = Path(pasta) / "resultado.json"
        config.write_text(
            json.dumps(
                {
                    "paginas": inventario["paginas"],
                    "menu": inventario["menu"],
                    "artefatos": str(artefatos.resolve()),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        def executar(url: str) -> None:
            subprocess.run(
                [
                    "node",
                    str(root / "tools" / "auditar_ecossistema_browser.js"),
                    "--base-url",
                    url,
                    "--config",
                    str(config),
                    "--saida",
                    str(bruto),
                ],
                cwd=root,
                check=True,
                timeout=600,
            )

        if base_url:
            executar(base_url)
        else:
            with servidor_local(root) as url:
                executar(url)
        dados = json.loads(bruto.read_text(encoding="utf-8"))
    return [Resultado(**item) for item in dados["resultados"]]


def resumo(resultados: list[Resultado]) -> dict[str, int]:
    return {
        status: sum(item.status == status for item in resultados)
        for status in ("aprovado", "falhou", "não testado")
    }


def escrever_relatorios(
    resultados: list[Resultado],
    inventario: dict[str, Any],
    *,
    saida_json: Path | None,
    saida_md: Path | None,
) -> None:
    payload = {
        "resumo": resumo(resultados),
        "inventario": inventario,
        "resultados": [asdict(item) for item in resultados],
    }
    if saida_json:
        saida_json.parent.mkdir(parents=True, exist_ok=True)
        saida_json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if saida_md:
        saida_md.parent.mkdir(parents=True, exist_ok=True)
        linhas = [
            "# Relatório da auditoria do ecossistema",
            "",
            "| Requisito | Cenário | Esperado | Status | Evidência |",
            "|---|---|---|---|---|",
        ]
        for item in resultados:
            campos = (
                item.requisito,
                item.cenario,
                item.esperado,
                item.status,
                item.evidencia,
            )
            linhas.append(
                "| " + " | ".join(c.replace("|", "\\|") for c in campos) + " |"
            )
        saida_md.write_text("\n".join(linhas) + "\n", encoding="utf-8")


def validar_descoberta(
    resultados: list[Resultado], caminho: Path = FALHAS_CONHECIDAS
) -> tuple[bool, str]:
    config = json.loads(caminho.read_text(encoding="utf-8"))
    esperadas = set(config["falhas_esperadas"])
    derivadas = [
        re.compile(padrao)
        for padrao in config.get("falhas_derivadas_permitidas", [])
    ]
    falhas = {item.id for item in resultados if item.status == "falhou"}
    ausentes = sorted(esperadas - falhas)
    inesperadas = sorted(
        identificador
        for identificador in falhas - esperadas
        if not any(padrao.fullmatch(identificador) for padrao in derivadas)
    )
    if ausentes or inesperadas:
        partes = []
        if ausentes:
            partes.append("falhas conhecidas não detectadas: " + ", ".join(ausentes))
        if inesperadas:
            partes.append("falhas inesperadas: " + ", ".join(inesperadas))
        return False, "; ".join(partes)
    return True, "as falhas obrigatórias foram detectadas sem falha inesperada"


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--navegador", action="store_true")
    p.add_argument("--base-url")
    p.add_argument("--modo-descoberta", action="store_true")
    p.add_argument("--saida-json", type=Path)
    p.add_argument("--saida-md", type=Path)
    p.add_argument("--artefatos", type=Path, default=ARTEFATOS)
    return p


def main() -> int:
    args = parser().parse_args()
    resultados, inventario = auditar_estatico()
    if args.navegador:
        try:
            resultados.extend(
                auditar_navegador(
                    inventario,
                    artefatos=args.artefatos,
                    base_url=args.base_url,
                )
            )
        except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError) as exc:
            resultados.append(
                Resultado(
                    id="navegador:execucao",
                    requisito="Abrir páginas reais em navegador",
                    cenario="Chromium indisponível ou execução interrompida",
                    esperado="auditoria termina com evidências de desktop e celular",
                    status="falhou",
                    evidencia=str(exc),
                    camada="navegador",
                )
            )
    else:
        resultados.append(
            Resultado(
                id="navegador:execucao",
                requisito="Abrir páginas reais em navegador",
                cenario="execução sem --navegador",
                esperado="auditoria termina com evidências de desktop e celular",
                status="não testado",
                evidencia="use --navegador; o CI executa esta camada",
                camada="navegador",
            )
        )

    escrever_relatorios(
        resultados,
        inventario,
        saida_json=args.saida_json,
        saida_md=args.saida_md,
    )
    contagem = resumo(resultados)
    print(json.dumps(contagem, ensure_ascii=False))
    for item in resultados:
        if item.status == "falhou":
            print(f"FALHOU {item.id}: {item.evidencia}")

    if args.modo_descoberta:
        ok, detalhe = validar_descoberta(resultados)
        print("MODO DESCOBERTA:", detalhe)
        return 0 if ok else 1
    return 1 if contagem["falhou"] or contagem["não testado"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
