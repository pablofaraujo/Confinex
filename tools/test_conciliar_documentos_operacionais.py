import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from tools.conciliar_documentos_operacionais import (
    extrair_gtas,
    extrair_gtas_campo,
    gerar_plano,
    ler_agronotas,
)


def fonte_agronotas(registros):
    return {"arquivo": "notas.xlsx", "sha256": "a" * 64,
            "registros": registros, "duplicados": 0,
            "ignorados_nao_pecuarios": 0}


def fonte_ima(movimentos):
    return {"sha256": "b" * 64, "periodo_inicial": "2026-08-01",
            "periodo_final": "2026-08-11", "movimentos": movimentos,
            "saldo_rebanho": 100}


def fonte_banco(transacoes):
    return {"arquivo": "extrato.ofx", "sha256": "c" * 64,
            "transacoes": transacoes}


class ConciliarDocumentosOperacionaisTest(unittest.TestCase):
    def test_extrai_formatos_de_gta_sem_inventar_numero(self):
        self.assertEqual(extrair_gtas("GTA MG U 654321 e GTA06_109905"),
                         ["654321", "109905"])
        self.assertEqual(extrair_gtas("documento sem referência"), [])
        self.assertEqual(extrair_gtas_campo("654321; 109905"),
                         ["654321", "109905"])

    def test_cruza_fontes_mas_nao_confirma_nem_escreve(self):
        nota = {"linha": 2, "nf": "123", "gtas": ["654321"],
                "data": "2026-08-10", "valor": Decimal("1200.00"),
                "quantidade": Decimal("20.00")}
        plano = gerar_plano(
            fonte_agronotas([nota]),
            fonte_ima([{"gta": "654321", "data": "2026-08-10",
                        "quantidade": 20, "sentido": "saida"}]),
            fonte_banco([{"fitid_hash": "hash-seguro", "data": "2026-08-11",
                          "valor": Decimal("-1200.00")}]),
            {"arquivo": "negocios.xlsx", "sha256": "d" * 64,
             "registros": [{"codigo": "NEG-1", "aba": "Negocios", "linha": 3,
                              "gtas": ["654321"], "nfs": [], "data": None,
                              "valor": None}]},
            date(2026, 8, 11),
        )
        self.assertEqual(plano["resumo"]["vinculos_nf_gta"], {"forte": 1})
        self.assertEqual(plano["resumo"]["candidatos_banco"], {"provavel": 1})
        self.assertEqual(plano["resumo"]["candidatos_negocio"], {"forte": 1})
        self.assertFalse(plano["candidatos_banco"][0]["confirmado"])
        self.assertFalse(plano["candidatos_negocio"][0]["confirmado"])
        self.assertEqual(plano["escritas_executadas"], 0)
        self.assertEqual(plano["tabelas_operacionais_alteradas"], 0)
        self.assertFalse(plano["plano_gera_escrita"])

    def test_valor_bancario_repetido_fica_ambiguo(self):
        nota = {"linha": 2, "nf": "123", "gtas": [], "data": "2026-08-10",
                "valor": Decimal("1200.00"), "quantidade": None}
        banco = fonte_banco([
            {"fitid_hash": "a", "data": "2026-08-10", "valor": Decimal("-1200")},
            {"fitid_hash": "b", "data": "2026-08-11", "valor": Decimal("-1200")},
        ])
        plano = gerar_plano(fonte_agronotas([nota]), fonte_ima([]), banco, None,
                            date(2026, 8, 11))
        candidato = plano["candidatos_banco"][0]
        self.assertEqual(candidato["classificacao"], "ambiguo")
        self.assertNotIn("fitid_hash", candidato)
        self.assertFalse(candidato["confirmado"])

    def test_plano_id_e_deterministico(self):
        nota = {"linha": 2, "nf": "123", "gtas": [], "data": None,
                "valor": None, "quantidade": None}
        argumentos = (fonte_agronotas([nota]), fonte_ima([]), fonte_banco([]),
                      None, date(2026, 8, 11))
        self.assertEqual(gerar_plano(*argumentos)["plano_id"],
                         gerar_plano(*argumentos)["plano_id"])

    def test_le_xlsx_e_extrai_gta_da_observacao(self):
        linhas = [
            ["NF", "Data emissão", "Valor total", "Quantidade", "Comentários"],
            ["900", "10/08/2026", "1500,00", "25", "Documento GTA MG U 654321"],
        ]

        def celula(coluna, linha, valor):
            referencia = chr(65 + coluna) + str(linha)
            return f'<c r="{referencia}" t="inlineStr"><is><t>{valor}</t></is></c>'

        xml_linhas = "".join(
            f'<row r="{numero}">' + "".join(celula(i, numero, v) for i, v in enumerate(valores)) + "</row>"
            for numero, valores in enumerate(linhas, start=1)
        )
        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / "notas.xlsx"
            with ZipFile(caminho, "w", ZIP_DEFLATED) as pacote:
                pacote.writestr("xl/workbook.xml",
                    '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                    '<sheets><sheet name="Notas" sheetId="1" r:id="rId1"/></sheets></workbook>')
                pacote.writestr("xl/_rels/workbook.xml.rels",
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
                pacote.writestr("xl/worksheets/sheet1.xml",
                    '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                    f'<sheetData>{xml_linhas}</sheetData></worksheet>')
            resultado = ler_agronotas(caminho, "Notas")
        self.assertEqual(len(resultado["registros"]), 1)
        self.assertEqual(resultado["registros"][0]["gtas"], ["654321"])
        self.assertEqual(resultado["registros"][0]["valor"], Decimal("1500.00"))

    def test_ignora_documento_sem_relacao_com_gado(self):
        linhas = [
            ["tipo_documento", "numero_documento", "data_emissao",
             "quantidade_cabecas", "valor_documento", "observacoes"],
            ["nfe", "100", "10/08/2026", "1", "100,00", "material de escritório"],
            ["gta", "654321", "10/08/2026", "25", "", "movimentação animal"],
        ]

        def celula(coluna, linha, valor):
            referencia = chr(65 + coluna) + str(linha)
            return f'<c r="{referencia}" t="inlineStr"><is><t>{valor}</t></is></c>'

        xml_linhas = "".join(
            f'<row r="{numero}">' + "".join(celula(i, numero, v) for i, v in enumerate(valores)) + "</row>"
            for numero, valores in enumerate(linhas, start=1)
        )
        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / "documentos.xlsx"
            with ZipFile(caminho, "w", ZIP_DEFLATED) as pacote:
                pacote.writestr("xl/workbook.xml",
                    '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                    '<sheets><sheet name="Documentos" sheetId="1" r:id="rId1"/></sheets></workbook>')
                pacote.writestr("xl/_rels/workbook.xml.rels",
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
                pacote.writestr("xl/worksheets/sheet1.xml",
                    '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                    f'<sheetData>{xml_linhas}</sheetData></worksheet>')
            resultado = ler_agronotas(caminho, "Documentos")
        self.assertEqual(resultado["ignorados_nao_pecuarios"], 1)
        self.assertEqual(resultado["registros"][0]["nf"], None)
        self.assertEqual(resultado["registros"][0]["gtas"], ["654321"])


if __name__ == "__main__":
    unittest.main()
