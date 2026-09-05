from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

try:
    import perfilar_chaves_fontes as modulo
except ModuleNotFoundError:
    from tools import perfilar_chaves_fontes as modulo


def xlsx_teste():
    saida = io.BytesIO()
    with ZipFile(saida, "w") as z:
        z.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Itens" sheetId="1" r:id="r1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships><Relationship Id="r1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", '''<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
          <row r="2"><c r="A2" t="inlineStr"><is><t>Código</t></is></c><c r="B2" t="inlineStr"><is><t>Código</t></is></c></row>
          <row r="3"><c r="A3" t="inlineStr"><is><t>001</t></is></c><c r="B3"><f>2-2</f><v>0</v></c></row>
          <row r="4"><c r="A4" t="inlineStr"><is><t>002</t></is></c><c r="B4"><f>1+2</f></c></row>
          <row r="5"><c r="B5" t="e"><v>#REF!</v></c></row>
          <row r="6"><c r="A6" t="inlineStr"><is><t>Total</t></is></c><c r="B6"><v>3</v></c></row>
        </sheetData></worksheet>''')
    return saida.getvalue()


class LeitoresCatalogoTest(unittest.TestCase):
    def test_xlsx_preserva_zeros_colunas_e_formula_sem_executar(self):
        registros, campos, meta = modulo.ler_tabela(xlsx_teste(), ".xlsx", {
            "aba": "Itens", "linha_cabecalho": 2, "linha_final": 6, "linhas_ignorar": [6]})
        self.assertEqual(campos, ["A", "B"])
        self.assertEqual(registros[0], {"A": "001", "B": Decimal(0)})
        self.assertIsNone(registros[1]["B"])
        self.assertEqual(meta["formulas"], 2)
        self.assertEqual(meta["formulas_sem_valor_armazenado"], 1)
        self.assertEqual(meta["celulas_com_erro"], 1)
        self.assertEqual(meta["rotulos"], {"A": "Código", "B": "Código"})
        self.assertEqual(len(registros), 3)
        self.assertEqual(meta["linhas_lidas"], [3, 4, 5])
        self.assertEqual(registros[2], {"B": None})

    def test_xlsx_cabecalho_ausente_nao_e_inferido_de_dados(self):
        with self.assertRaises(ValueError):
            modulo.ler_xlsx(xlsx_teste(), {"aba": "Itens", "linha_cabecalho": 1})

    def test_xlsx_aba_inexistente(self):
        with self.assertRaises(ValueError):
            modulo.ler_xlsx(xlsx_teste(), {"aba": "Outra", "linha_cabecalho": 2})

    def test_xml_entidades_recusadas(self):
        with self.assertRaises(ValueError):
            modulo.xml_seguro(b'<!DOCTYPE x [<!ENTITY x "privado">]><x>&x;</x>')

    def test_xml_utf16_nao_contorna_protecao_de_entidades(self):
        with self.assertRaises(ValueError):
            modulo.xml_seguro('<!DOCTYPE x [<!ENTITY x "privado">]><x>&x;</x>'.encode("utf-16"))

    def test_linha_so_com_formula_sem_cache_nao_desaparece(self):
        saida = io.BytesIO()
        with ZipFile(io.BytesIO(xlsx_teste())) as origem, ZipFile(saida, "w") as destino:
            for membro in origem.namelist():
                dados = origem.read(membro)
                if membro.endswith("sheet1.xml"):
                    dados = dados.replace(b'<c r="B5" t="e"><v>#REF!</v></c>', b'<c r="B5"><f>1+1</f></c>')
                destino.writestr(membro, dados)
        registros, _, meta = modulo.ler_xlsx(saida.getvalue(), {
            "aba": "Itens", "linha_cabecalho": 2, "linha_final": 5})
        self.assertEqual(len(registros), 3)
        self.assertEqual(registros[2], {"B": None})
        self.assertEqual(meta["formulas_sem_valor_armazenado"], 2)

    def test_csv_texto_com_quebra_de_linha_e_zero_inicial(self):
        registros, campos, _ = modulo.ler_tabela(b'id;obs\n001;"linha 1\nlinha 2"\n', ".csv", {})
        self.assertEqual(campos, ["id", "obs"])
        self.assertEqual(registros[0], {"id": "001", "obs": "linha 1\nlinha 2"})

    def test_csv_nao_sobrescreve_cabecalho_repetido_ou_coluna_extra(self):
        for dados in (b'id;id\n1;2\n', b'id;\n1;2\n', b'id\n1;2\n'):
            with self.subTest(dados=dados), self.assertRaises(ValueError):
                modulo.ler_tabela(dados, ".csv", {})

    def test_json_snapshot_compativel(self):
        dados = b'{"tabelas":{"itens":[{"id":"01","valor":2.5}]}}'
        registros, _, _ = modulo.ler_tabela(dados, ".json", {"tabela": "itens"})
        self.assertEqual(registros[0]["valor"], Decimal("2.5"))

    def test_json_vazio_tem_schema_declarado_sem_inventar_linhas(self):
        registros, campos, _ = modulo.ler_tabela(b'[]', ".json", {"campos": ["id"]})
        self.assertEqual(registros, [])
        self.assertEqual(campos, ["id"])

    def test_json_nao_aceita_nao_finitos(self):
        with self.assertRaises(ValueError):
            modulo.ler_tabela(b'[{"id":NaN}]', ".json", {})

    def test_limite_de_arquivo(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "entrada.csv"
            p.write_bytes(b"abcde")
            with patch.object(modulo, "MAX_BYTES", 4), self.assertRaises(ValueError):
                modulo.ler_bytes(p)


class CatalogoIntegracaoTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.raiz = Path(self.temp.name)
        self.fonte = self.raiz / "itens.json"
        self.fonte.write_text(json.dumps([{"id": "01", "grupo": "a"}, {"id": "02", "grupo": "a"}]))
        self.manifesto = {"versao": 1, "fontes": [{"id": "itens", "arquivo": "itens.json",
                            "chaves": [{"nome": "identificador", "campos": ["id"]}]}]}

    def test_plano_estavel_hashes_preservados_sem_rede_ou_mutacao(self):
        fonte_antes = self.fonte.read_bytes()
        config_antes = copy.deepcopy(self.manifesto)
        with patch("socket.socket", side_effect=AssertionError("rede proibida")):
            a = modulo.gerar_catalogo(self.manifesto, self.raiz)
            b = modulo.gerar_catalogo(self.manifesto, self.raiz)
        self.assertEqual(a, b)
        self.assertEqual(self.manifesto, config_antes)
        self.assertEqual(self.fonte.read_bytes(), fonte_antes)
        self.assertEqual(a["catalogo"][0]["sha256_antes"], hashlib.sha256(fonte_antes).hexdigest())
        self.assertEqual(a["catalogo"][0]["sha256_antes"], a["catalogo"][0]["sha256_depois"])

    def test_manifesto_documentado_funciona_com_fontes_sinteticas(self):
        guia = Path(modulo.__file__).resolve().parent.parent / "docs/catalogo-chaves-fontes.md"
        manifesto = json.loads(re.search(r"```json\n(.*?)\n```", guia.read_text(), re.S)[1])
        (self.raiz / "negocios.json").write_text('{"negocios":[{"codigo":"001","lote":"l1"}]}')
        (self.raiz / "documentos.csv").write_text('numero;serie\n001;1\n')
        (self.raiz / "lotes.xlsx").write_bytes(xlsx_teste())
        plano = modulo.gerar_catalogo(manifesto, self.raiz)
        self.assertEqual(len(plano["catalogo"]), 3)
        self.assertEqual(plano["relacoes"][0]["correspondentes"], 1)
        self.assertEqual(plano["relacoes"][0]["orfaos"], 1)
        self.assertEqual(plano["relacoes"][0]["incompletos_origem"], 1)

    def test_fonte_alterada_impede_atestado(self):
        ler_original = modulo.ler_bytes
        vezes = 0
        def leitura(caminho):
            nonlocal vezes
            vezes += 1
            return ler_original(caminho) if vezes == 1 else b"[]"
        with patch.object(modulo, "ler_bytes", side_effect=leitura), self.assertRaises(ValueError):
            modulo.gerar_catalogo(self.manifesto, self.raiz)

    def test_escopo_diferente_altera_identidade_do_plano(self):
        antes = modulo.gerar_catalogo(self.manifesto, self.raiz)
        self.manifesto["escopo"] = "Amostra histórica, não base atual"
        depois = modulo.gerar_catalogo(self.manifesto, self.raiz)
        self.assertNotEqual(antes["manifesto_sha256"], depois["manifesto_sha256"])
        self.assertNotEqual(antes["plano_id"], depois["plano_id"])
        self.assertEqual(antes["catalogo"], depois["catalogo"])

    def test_duplicar_alias_ou_coluna_errada_bloqueia(self):
        self.manifesto["fontes"] *= 2
        with self.assertRaises(ValueError):
            modulo.gerar_catalogo(self.manifesto, self.raiz)

    def test_relacao_explica_orfaos(self):
        (self.raiz / "pais.json").write_text('[{"id":"a"}]')
        self.manifesto["fontes"].append({"id": "pais", "arquivo": "pais.json"})
        self.manifesto["relacoes"] = [{"origem": "itens", "destino": "pais",
                                       "campos_origem": ["grupo"], "campos_destino": ["id"]}]
        plano = modulo.gerar_catalogo(self.manifesto, self.raiz)
        self.assertEqual(plano["relacoes"][0]["cardinalidade_observada"], "N:1")
        self.assertEqual(plano["relacoes"][0]["orfaos"], 0)

    def test_relacao_de_coluna_inexistente_nao_parece_vazia(self):
        self.manifesto["relacoes"] = [{"origem": "itens", "destino": "itens",
                                       "campos_origem": ["inexistente"], "campos_destino": ["id"]}]
        with self.assertRaises(ValueError):
            modulo.gerar_catalogo(self.manifesto, self.raiz)

    def test_saida_privada_e_nao_sobrescreve(self):
        plano = modulo.gerar_catalogo(self.manifesto, self.raiz)
        saida = self.raiz / "saida"
        modulo.salvar_relatorios(plano, saida)
        self.assertEqual(stat.S_IMODE(saida.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((saida / "catalogo.json").stat().st_mode), 0o600)
        with self.assertRaises(FileExistsError):
            modulo.salvar_relatorios(plano, saida)

    def test_saida_fora_de_diretorio_privado_recusada(self):
        with self.assertRaises(ValueError):
            modulo.salvar_relatorios({}, Path("/dados-publicos/catalogo"))

    def test_cli_sucesso_e_erro_nao_mostram_registros(self):
        self.fonte.write_text('[{"id":"segredo-exemplo-123"}]')
        manifesto = self.raiz / "manifesto.json"
        manifesto.write_text(json.dumps(self.manifesto))
        cmd = [sys.executable, "-B", str(Path(modulo.__file__)), "--manifesto", str(manifesto),
               "--saida", str(self.raiz / "saida")]
        resposta = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        self.assertEqual(resposta.returncode, 0, resposta.stderr)
        self.assertNotIn("segredo-exemplo-123", resposta.stdout + resposta.stderr)
        resposta = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        self.assertNotEqual(resposta.returncode, 0)
        self.assertNotIn("segredo-exemplo-123", resposta.stdout + resposta.stderr)
        self.assertNotIn("Traceback", resposta.stderr)


if __name__ == "__main__":
    unittest.main()
