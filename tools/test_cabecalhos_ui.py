#!/usr/bin/env python3
"""Contrato visual permanente dos títulos e subtítulos das áreas Confinex."""

from __future__ import annotations

import re
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGINAS = (
    "index.html",
    "confinados.html",
    "confinamento.html",
    "fazenda-ametista.html",
    "bgi.html",
    "bb.html",
    "abate.html",
    "ocr-pesagem.html",
    "painel-boi-gordo.html",
    "financeiro.html",
    "parcerias.html",
    "parceria-ricardo.html",
    "parceria-xande.html",
    "pendencias.html",
    "revisoes.html",
    "eventos.html",
    "crm.html",
    "ops.html",
)


class LeitorTopbar(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.profundidade = 0
        self.profundidade_topbar: int | None = None
        self.h1 = 0
        self.subtitulos = 0
        self.imagens = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        atributos = dict(attrs)
        classes = set((atributos.get("class") or "").split())
        if tag == "div":
            self.profundidade += 1
            if self.profundidade_topbar is None and "topbar" in classes:
                self.profundidade_topbar = self.profundidade
        if self.profundidade_topbar is None:
            return
        if tag == "h1":
            self.h1 += 1
        elif tag == "img":
            self.imagens += 1
        elif tag == "div" and "sub" in classes:
            self.subtitulos += 1

    def handle_endtag(self, tag: str) -> None:
        if tag != "div":
            return
        if self.profundidade_topbar == self.profundidade:
            self.profundidade_topbar = None
        self.profundidade -= 1


class CabecalhosUiTest(unittest.TestCase):
    MODULOS_SEM_RESUMO_TECNICO = (
        "abate.html",
        "bb.html",
        "bgi.html",
        "confinados.html",
        "confinamento.html",
        "fazenda-ametista.html",
        "ops.html",
        "parceria-ricardo.html",
        "parceria-xande.html",
        "js/financeiro.js",
        "js/pendencias.js",
        "js/eventos.js",
        "revisoes.js",
    )

    def test_subtitulos_nao_repetem_horario_nem_contagem(self) -> None:
        for nome in self.MODULOS_SEM_RESUMO_TECNICO:
            fonte = (ROOT / nome).read_text(encoding="utf-8")
            self.assertNotRegex(
                fonte,
                r"(?:subtitle|el\('subtitle'\)).{0,100}(?:Atualizado|Dados atualizados)",
                msg=f"{nome} ainda usa o subtítulo como carimbo técnico",
            )

    def test_titulos_dos_modulos_nao_repetem_nome_pessoal(self) -> None:
        for pagina in PAGINAS:
            fonte = (ROOT / pagina).read_text(encoding="utf-8")
            titulos = " ".join(re.findall(r"<h1[^>]*>(.*?)</h1>", fonte, re.S))
            self.assertNotIn("PABLO FERREIRA", titulos, msg=pagina)

    def test_excecoes_de_frescor_continuam_visiveis(self) -> None:
        self.assertIn("Atualizado", (ROOT / "index.html").read_text(encoding="utf-8"))
        self.assertIn(
            "Atualizado em",
            (ROOT / "painel-boi-gordo.html").read_text(encoding="utf-8"),
        )

    def test_todas_as_paginas_ativas_usam_titulo_de_aba_padrao(self) -> None:
        for pagina in (*PAGINAS, "confinex.html"):
            fonte = (ROOT / pagina).read_text(encoding="utf-8")
            titulo = re.search(r"<title>(.*?)</title>", fonte, re.S)
            self.assertIsNotNone(titulo, msg=f"{pagina} sem título")
            self.assertTrue(titulo.group(1).startswith("CFAgro — "), msg=pagina)

    def test_paginas_usam_titulo_e_subtitulo_sem_logo_repetido(self) -> None:
        for pagina in PAGINAS:
            with self.subTest(pagina=pagina):
                fonte = (ROOT / pagina).read_text(encoding="utf-8")
                leitor = LeitorTopbar()
                leitor.feed(fonte)
                self.assertEqual(leitor.h1, 1)
                self.assertEqual(leitor.subtitulos, 1)
                self.assertEqual(leitor.imagens, 0)
                self.assertIn("design/components.css?v=20260804-1", fonte)

    def test_design_system_define_o_mesmo_contrato_da_visao_geral(self) -> None:
        css = (ROOT / "design/components.css").read_text(encoding="utf-8")
        self.assertIn(
            ".topbar h1{margin:0;color:var(--text);font-family:var(--font);"
            "font-size:28px;font-weight:700;",
            css,
        )
        self.assertIn(
            ".topbar .sub{margin-top:4px;color:var(--muted);font-family:var(--font);"
            "font-size:var(--fs-13);font-weight:400;",
            css,
        )
        self.assertIn(".topbar h1{font-size:24px}", css)

    def test_confinex_react_repete_o_mesmo_contrato_nos_dois_pacotes(self) -> None:
        contrato_titulo = (
            ".logo{font-family:var(--font);font-size:28px;font-weight:700;"
            "color:var(--text);letter-spacing:-.02em;line-height:1.2}"
        )
        contrato_subtitulo = (
            ".logo-sub{font-family:var(--font);font-size:var(--fs-13);"
            "color:var(--muted);letter-spacing:0;text-transform:none;"
        )
        for arquivo in ("confinex-app.latest.js", "confinex-app.mobile.js"):
            with self.subTest(arquivo=arquivo):
                fonte = (ROOT / arquivo).read_text(encoding="utf-8")
                self.assertIn(contrato_titulo, fonte)
                self.assertIn(contrato_subtitulo, fonte)
                self.assertIn(".logo{font-size:24px}", fonte)
                self.assertIn("body.has-shell .shell-content .app{padding:0 0 100px}", fonte)


if __name__ == "__main__":
    unittest.main()
