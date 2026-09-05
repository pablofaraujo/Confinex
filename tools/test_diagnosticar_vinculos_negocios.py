from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout, redirect_stderr
import io

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diagnosticar_vinculos_negocios as diagnostico


def snapshot(linhas=None):
    tabelas = {nome: [] for nome in diagnostico.TABELAS}
    tabelas["operacoes"] = [{
        "id": "op-1", "codigo": "NEG-001", "sexo": "macho", "tipo_negocio": "compra",
        "status": "ativo", "confinamento_id": None,
    }]
    tabelas["compras"] = [{
        "id": "comp-1", "operacao_id": "op-1", "quantidade": "3.30", "peso_total_kg": "330.00",
        "valor_total": "12.50", "data": "2026-09-01", "idempotency_key": "idem-1",
    }]
    tabelas["compras_componentes"] = [{
        "id": "comp-item-1", "compra_agregada_id": "comp-1", "quantidade": "1.10",
        "peso_total_kg": "110.00", "valor_total": "4.00", "chave_rastreio": "r-1",
        "dimensoes_origem": {"sexo": "macho", "categoria": "boi", "destino": "confinamento"},
        "dimensoes_formato_inesperado": False,
    }, {
        "id": "comp-item-2", "compra_agregada_id": "comp-1", "quantidade": "2.20",
        "peso_total_kg": "220.00", "valor_total": "8.50", "chave_rastreio": "r-2",
        "dimensoes_origem": {"sexo": "macho", "categoria": "boi", "destino": "confinamento"},
        "dimensoes_formato_inesperado": False,
    }]
    tabelas["confinex_avaliacoes"] = [{"id": "av-1", "codigo": "AV-1", "operacao_id": "op-1", "status": "ativa"}]
    tabelas["confinex_estimativas"] = [{"id": "est-1", "avaliacao_id": "av-1", "versao": "1", "tipo": "base"}]
    tabelas["negocios_candidatos"] = [{
        "id": "cand-1", "codigo_fonte": "NEG-001", "fonte_importacao_id": "fonte-1", "sexo": "macho",
        "categoria": "boi", "destino": "confinamento", "estado": "candidato", "operacao_id": None,
        "incorporado_no_candidato_id": None, "quantidade": "3.30",
    }]
    tabelas["transacoes_banco_staging"] = [{
        "id": "st-1", "conta": "conta-a", "fitid": "fit-1", "data": "2026-09-01",
        "valor": "12.50", "transacao_banco_id": "tb-1",
    }]
    tabelas["transacoes_banco"] = [{
        "id": "tb-1", "conta": "conta-a", "id_externo": "fit-1", "data": "2026-09-01",
        "valor": "12.50", "fluxo_caixa_id": None,
    }]
    if linhas:
        for tabela, registros in linhas.items():
            tabelas[tabela] = registros
    return {"versao": 1, "modo": "somente_leitura", "tabelas": tabelas,
            "contagens": {nome: len(registros) for nome, registros in tabelas.items()}}


class DiagnosticoVinculosTests(unittest.TestCase):
    def test_plano_inclui_recorte_codigo_e_sexo_mesmo_com_arquivo_igual(self):
        fontes = {"fontes_inalteradas": True, "fontes": [{
            "id": "fonte", "aba": "Itens", "sha256": "a" * 64,
            "linhas": [{"linha": 2, "codigo": "NEG-001", "sexo": None}],
        }]}
        original = diagnostico.diagnosticar(snapshot(), snapshot(), fontes)["plano_id"]
        for campo, valor in (("linha", 3), ("codigo", "NEG-002"), ("sexo", "macho")):
            alterado = deepcopy(fontes)
            alterado["fontes"][0]["linhas"][0][campo] = valor
            with self.subTest(campo=campo):
                self.assertNotEqual(original, diagnostico.diagnosticar(snapshot(), snapshot(), alterado)["plano_id"])
        self.assertIsNone(diagnostico.diagnosticar(snapshot(), snapshot())["verificacao"]["fontes_inalteradas"])

    def test_soma_decimal_preserva_distancia_de_expoentes(self):
        caso = snapshot()
        caso["tabelas"]["compras"][0]["valor_total"] = "1E+40"
        caso["tabelas"]["compras_componentes"][0]["valor_total"] = "1E+40"
        caso["tabelas"]["compras_componentes"][1]["valor_total"] = "0.01"
        resultado = diagnostico.diagnosticar(caso, caso)
        achado = next(a for a in resultado["achados"] if a["tipo"] == "totais_componentes_divergentes")
        self.assertEqual(achado["evidencia"]["soma_filhos"], "1" + "0" * 40 + ".01")
        self.assertIn(achado["evidencia"]["soma_filhos"], diagnostico.markdown(resultado))
        caso["tabelas"]["compras"][0]["valor_total"] = achado["evidencia"]["soma_filhos"]
        self.assertNotIn("totais_componentes_divergentes", [a["tipo"] for a in diagnostico.diagnosticar(caso, caso)["achados"]])

    def test_campos_obrigatorios_dimensoes_e_escala_limitados(self):
        for fonte in ({}, {"id": "fonte", "aba": "A"}):
            with self.subTest(fonte=fonte), self.assertRaisesRegex(ValueError, "fonte_invalida"):
                diagnostico.validar_planilha_registros({"fontes_inalteradas": True, "fontes": [fonte]})
        for valor in ("1e10001", "NaN", "Infinity", 0.1, True, ""):
            caso = snapshot()
            caso["tabelas"]["compras"][0]["valor_total"] = valor
            with self.subTest(valor=valor), self.assertRaises(ValueError):
                diagnostico.validar_snapshot(caso)
        caso = snapshot()
        caso["tabelas"]["compras_componentes"][0]["dimensoes_origem"]["sexo"] = "x" * 81
        with self.assertRaisesRegex(ValueError, "dimensoes_invalidas"):
            diagnostico.validar_snapshot(caso)

    def test_dimensoes_desconhecidas_nao_criam_divisao_distinta(self):
        for valor, tipo in ((None, "candidato_dimensoes_incompletas"), ("desconhecido", "candidato_dimensoes_desconhecidas")):
            caso = snapshot()
            incompleto = deepcopy(caso["tabelas"]["negocios_candidatos"][0])
            incompleto.update(id="cand-2", sexo=valor)
            caso["tabelas"]["negocios_candidatos"].append(incompleto)
            caso["contagens"]["negocios_candidatos"] += 1
            tipos = {a["tipo"] for a in diagnostico.diagnosticar(caso, caso)["achados"]}
            self.assertIn(tipo, tipos)
            self.assertNotIn("candidatos_divisoes_distintas", tipos)

    def test_cli_bloqueia_alteracao_de_entrada_e_nao_sobrescreve(self):
        with tempfile.TemporaryDirectory() as temporario:
            raiz = Path(temporario)
            antes, depois = raiz / "antes.json", raiz / "depois.json"
            for caminho in (antes, depois):
                caminho.write_text(json.dumps(snapshot()))
            saida = raiz / "resultado"
            original = diagnostico.diagnosticar
            def mudar_arquivo(*args):
                relatorio = original(*args)
                antes.write_text("{}")
                return relatorio
            with patch.object(diagnostico, "diagnosticar", side_effect=mudar_arquivo), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(diagnostico.main(["--antes", str(antes), "--depois", str(depois), "--saida", str(saida)]), 1)
            self.assertFalse(saida.exists())
            relatorio = original(snapshot(), snapshot())
            diagnostico.salvar(relatorio, saida)
            anterior = (saida / "analise.json").read_bytes()
            with self.assertRaises(FileExistsError):
                diagnostico.salvar(relatorio, saida)
            self.assertEqual((saida / "analise.json").read_bytes(), anterior)

    def test_snapshot_canonico_por_id_preserva_input_e_detecta_mudanca(self):
        antes = snapshot()
        depois = deepcopy(antes)
        depois["tabelas"]["operacoes"] = list(reversed(depois["tabelas"]["operacoes"]))
        self.assertEqual(diagnostico.assinatura_snapshot(antes), diagnostico.assinatura_snapshot(depois))
        copia = deepcopy(antes)
        diagnostico.validar_snapshot(antes)
        self.assertEqual(antes, copia)
        longo_a = snapshot()
        longo_b = deepcopy(longo_a)
        longo_a["tabelas"]["compras"][0]["valor_total"] = "1." + "2" * 35
        longo_b["tabelas"]["compras"][0]["valor_total"] = "1." + "2" * 34 + "3"
        self.assertNotEqual(diagnostico.assinatura_snapshot(longo_a), diagnostico.assinatura_snapshot(longo_b))
        depois["tabelas"]["operacoes"][0]["status"] = "cancelada"
        with self.assertRaisesRegex(ValueError, "snapshots_mudaram"):
            diagnostico.diagnosticar(antes, depois)

    def test_validacao_rejeita_fonte_faltante_id_duplicado_truncamento_e_decimal(self):
        incompleto = snapshot()
        del incompleto["tabelas"]["compras"]
        with self.assertRaises(ValueError):
            diagnostico.validar_snapshot(incompleto)
        duplicado = snapshot({"operacoes": [snapshot()["tabelas"]["operacoes"][0]] * 2})
        duplicado["contagens"]["operacoes"] = 2
        with self.assertRaisesRegex(ValueError, "id_duplicado"):
            diagnostico.validar_snapshot(duplicado)
        truncado = snapshot()
        truncado["contagens"]["compras"] = 999
        with self.assertRaisesRegex(ValueError, "contagem_incoerente"):
            diagnostico.validar_snapshot(truncado)
        decimal_invalido = snapshot()
        decimal_invalido["tabelas"]["compras"][0]["valor_total"] = "1,25"
        with self.assertRaisesRegex(ValueError, "decimal_invalido"):
            diagnostico.validar_snapshot(decimal_invalido)
        campo_omitido = snapshot()
        del campo_omitido["tabelas"]["compras"][0]["data"]
        with self.assertRaisesRegex(ValueError, "campo_nao_projetado"):
            diagnostico.validar_snapshot(campo_omitido)
        id_nao_texto = snapshot()
        id_nao_texto["tabelas"]["operacoes"][0]["id"] = 1
        with self.assertRaisesRegex(ValueError, "id_obrigatorio"):
            diagnostico.validar_snapshot(id_nao_texto)
        fonte_vazia = {"fontes_inalteradas": True, "fontes": []}
        with self.assertRaisesRegex(ValueError, "fontes_invalidas"):
            diagnostico.validar_planilha_registros(fonte_vazia)
        linha_repetida = {
            "fontes_inalteradas": True,
            "fontes": [{"id": "f", "aba": "A", "sha256": "a" * 64,
                        "linhas": [{"linha": 2, "codigo": "X", "sexo": None},
                                   {"linha": 2, "codigo": "Y", "sexo": None}]}],
        }
        with self.assertRaisesRegex(ValueError, "linha_planilha_duplicada"):
            diagnostico.validar_planilha_registros(linha_repetida)

    def test_planilha_codigo_exato_nulos_ambiguidade_e_avisos_preservados(self):
        fontes = {
            "fontes_inalteradas": True,
            "fontes": [{
                "id": "planilha-1", "aba": "Compras", "sha256": "a" * 64,
                "leitura": {"formulas_sem_valor_armazenado": 2, "celulas_com_erro": 1,
                            "aviso": "revisar fórmula"},
                "linhas": [
                    {"linha": 2, "codigo": "NEG-001", "sexo": None},
                    {"linha": 3, "codigo": "NEG-001", "sexo": "macho"},
                    {"linha": 4, "codigo": "neg-001", "sexo": "macho"},
                    {"linha": 5, "codigo": "001", "sexo": None},
                    {"linha": 6, "codigo": None, "sexo": None},
                ],
            }],
        }
        relatorio = diagnostico.diagnosticar(snapshot(), snapshot(), fontes)
        tipos = [achado["tipo"] for achado in relatorio["achados"]]
        self.assertIn("codigo_planilha_multiplas_linhas", tipos)
        self.assertIn("codigo_operacao_ausente", tipos)
        self.assertIn("codigo_planilha_nulo", tipos)
        self.assertIn("aviso_leitura_planilha", tipos)
        self.assertEqual(relatorio["fontes"][0]["leitura"]["celulas_com_erro"], 1)
        self.assertEqual(relatorio["fontes"][0]["leitura"]["aviso"], "revisar fórmula")
        ambiguo = next(a for a in relatorio["achados"] if a["tipo"] == "codigo_planilha_multiplas_linhas")
        self.assertEqual([linha["linha"] for linha in ambiguo["linhas"]], [2, 3])

    def test_avaliacoes_estimativas_componentes_e_dimensoes(self):
        alterado = snapshot()
        alterado["tabelas"]["confinex_avaliacoes"] += [
            {"id": "av-2", "codigo": "AV-2", "operacao_id": "op-1", "status": "rascunho"},
            {"id": "av-3", "codigo": "AV-3", "operacao_id": None, "status": "rascunho"},
            {"id": "av-4", "codigo": "AV-4", "operacao_id": "op-inexistente", "status": "rascunho"},
            {"id": "av-5", "codigo": "AV-5", "operacao_id": None, "status": "cancelado"},
            {"id": "av-6", "codigo": "AV-6", "operacao_id": None, "status": "ativa"},
        ]
        alterado["tabelas"]["confinex_estimativas"] += [{"id": "est-orf", "avaliacao_id": "av-inexistente", "versao": "2", "tipo": "base"}]
        alterado["tabelas"]["compras_componentes"] += [{
            "id": "comp-orfa", "compra_agregada_id": "comp-inexistente", "quantidade": None,
            "peso_total_kg": None, "valor_total": None, "chave_rastreio": None,
            "dimensoes_origem": None, "dimensoes_formato_inesperado": True,
        }]
        alterado["contagens"] = {nome: len(registros) for nome, registros in alterado["tabelas"].items()}
        relatorio = diagnostico.diagnosticar(alterado, alterado)
        tipos = {achado["tipo"] for achado in relatorio["achados"]}
        self.assertIn("multiplas_avaliacoes_operacao", tipos)
        self.assertIn("avaliacao_operacao_nula", tipos)
        self.assertIn("avaliacao_operacao_orfa", tipos)
        self.assertIn("avaliacao_cancelada_sem_operacao", tipos)
        cancelada = next(a for a in relatorio["achados"] if a["tipo"] == "avaliacao_cancelada_sem_operacao")
        self.assertIn("Preservar", cancelada["proxima_verificacao"])
        self.assertNotIn("promover", cancelada["proxima_verificacao"])
        self.assertIn("estimativa_avaliacao_orfa", tipos)
        self.assertIn("componente_pai_orfao", tipos)
        self.assertIn("dimensoes_componente_formato_inesperado", tipos)

    def test_totais_decimal_nulo_nao_e_zero_e_divisoes_candidatos_preservadas(self):
        caso = snapshot()
        caso["tabelas"]["compras"][0]["valor_total"] = "0.30"
        caso["tabelas"]["compras_componentes"][0]["valor_total"] = "0.1"
        caso["tabelas"]["compras_componentes"][1]["valor_total"] = "0.2"
        caso["tabelas"]["negocios_candidatos"] += [{
            "id": "cand-2", "codigo_fonte": "NEG-001", "fonte_importacao_id": "fonte-1", "sexo": "femea",
            "categoria": "novilha", "destino": "venda", "estado": "candidato", "operacao_id": None,
            "incorporado_no_candidato_id": None, "quantidade": "1",
        }]
        caso["contagens"] = {nome: len(registros) for nome, registros in caso["tabelas"].items()}
        relatorio = diagnostico.diagnosticar(caso, caso)
        tipos = {achado["tipo"] for achado in relatorio["achados"]}
        self.assertNotIn("totais_componentes_divergentes", tipos)
        self.assertIn("candidatos_divisoes_distintas", tipos)
        candidatos_incompletos = deepcopy(caso)
        candidatos_incompletos["tabelas"]["negocios_candidatos"] += [{
            "id": "cand-incompleto", "codigo_fonte": "NEG-002", "fonte_importacao_id": "fonte-1", "sexo": None,
            "categoria": "boi", "destino": "venda", "estado": "candidato", "operacao_id": "op-ausente",
            "incorporado_no_candidato_id": None, "quantidade": "1",
        }]
        candidatos_incompletos["contagens"] = {nome: len(registros) for nome, registros in candidatos_incompletos["tabelas"].items()}
        tipos_candidatos = {a["tipo"] for a in diagnostico.diagnosticar(candidatos_incompletos, candidatos_incompletos)["achados"]}
        self.assertIn("candidato_dimensoes_incompletas", tipos_candidatos)
        self.assertIn("candidato_operacao_orfa", tipos_candidatos)
        nulo = deepcopy(caso)
        nulo["tabelas"]["compras"][0]["valor_total"] = None
        nulo["contagens"] = {nome: len(registros) for nome, registros in nulo["tabelas"].items()}
        tipos_nulo = {a["tipo"] for a in diagnostico.diagnosticar(nulo, nulo)["achados"]}
        self.assertIn("totais_componentes_incompletos", tipos_nulo)

    def test_banco_prioriza_id_explicito_sem_heuristica_e_sinaliza_riscos(self):
        caso = snapshot()
        caso["tabelas"]["transacoes_banco_staging"] += [
            {"id": "st-nulo", "conta": "conta-a", "fitid": "fit-1", "data": "2026-09-01", "valor": "12.50", "transacao_banco_id": None},
            {"id": "st-orf", "conta": "conta-a", "fitid": "fit-2", "data": "2026-09-02", "valor": "2", "transacao_banco_id": "tb-inexistente"},
            {"id": "st-dup", "conta": "conta-b", "fitid": "fit-1", "data": "2026-09-01", "valor": "12.50", "transacao_banco_id": "tb-1"},
        ]
        caso["tabelas"]["transacoes_banco"][0]["id_externo"] = None
        caso["contagens"] = {nome: len(registros) for nome, registros in caso["tabelas"].items()}
        relatorio = diagnostico.diagnosticar(caso, caso)
        tipos = {achado["tipo"] for achado in relatorio["achados"]}
        self.assertIn("transacao_banco_id_nulo", tipos)
        self.assertIn("transacao_banco_orfa", tipos)
        self.assertIn("transacao_banco_multiplamente_vinculada", tipos)
        self.assertIn("risco_escopo_fitid_entre_contas", tipos)
        self.assertIn("transacoes_banco_id_externo_nulo", tipos)
        self.assertNotIn("transacao_banco_por_fitid", tipos)

    def test_cli_so_imprime_resumo_e_saida_privada_nova(self):
        bruto = json.dumps(snapshot(), ensure_ascii=False).encode()
        with tempfile.TemporaryDirectory() as temporario:
            antes = Path(temporario) / "antes.json"
            depois = Path(temporario) / "depois.json"
            antes.write_bytes(bruto)
            depois.write_bytes(bruto)
            saida = Path(temporario) / "analise"
            processo = subprocess.run(
                [sys.executable, str(Path(__file__).resolve().parent / "diagnosticar_vinculos_negocios.py"),
                 "--antes", str(antes), "--depois", str(depois), "--saida", str(saida)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(processo.returncode, 0, processo.stderr)
            resumo = json.loads(processo.stdout)
            self.assertNotIn("achados", resumo)
            self.assertNotIn("op-1", processo.stdout)
            self.assertEqual((saida / "analise.json").stat().st_mode & 0o777, 0o600)
            self.assertEqual((saida / "analise.md").stat().st_mode & 0o777, 0o600)
            self.assertNotEqual((saida / "analise.json").read_text(encoding="utf-8"), "")
            processo_exec = subprocess.run(
                [sys.executable, str(Path(__file__).resolve().parent / "diagnosticar_vinculos_negocios.py"),
                 "--antes", str(antes), "--depois", str(depois), "--executar"],
                capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(processo_exec.returncode, 0)


if __name__ == "__main__":
    unittest.main()
