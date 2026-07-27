from __future__ import annotations

import unittest
from datetime import date

try:
    from consolidar_fontes_operacionais import gerar_plano
except ModuleNotFoundError:
    from tools.consolidar_fontes_operacionais import gerar_plano


def snapshot_base():
    nomes = [
        "operacoes", "compras", "vendas", "abates", "abate_animais",
        "pesagens_caderno", "gtas", "entradas_confinamento", "confinamentos",
        "custos_operacao", "eventos_operacao", "fechamentos_operacao",
        "fluxo_caixa", "transacoes_banco", "emprestimos", "promissorias",
        "pendencias_documentos", "operation_drafts", "pending_actions",
        "eventos", "memorias_agentes", "contexto_handoff", "fazenda_ametista",
        "notas_fiscais_xml_raw",
    ]
    return {"tabelas": {nome: [] for nome in nomes}, "escritas": 0}


class ConsolidacaoFontesTest(unittest.TestCase):
    def test_cruza_banco_gta_e_preserva_dry_run(self):
        snap = snapshot_base()
        snap["tabelas"]["transacoes_banco"] = [
            {"id": "b1", "id_externo": "fit-1", "data": "2026-07-26", "valor": -100, "conciliada": False},
        ]
        snap["tabelas"]["fluxo_caixa"] = [
            {"id": "f1", "data": "2026-07-26", "valor": 100, "realizado": True},
        ]
        snap["tabelas"]["notas_fiscais_xml_raw"] = [
            {"numero": "10", "gta": "GTA-20", "data": "2026-07-26"},
        ]
        snap["tabelas"]["entradas_confinamento"] = [{"gta": "20"}]
        juan = {"mensagens": [{"papel": "user", "conteudo": "compra com GTA", "medias": []}]}
        gta = {"NFs e GTA": [["Nº Nota Fiscal", "GTA"], ["10", "20"]]}
        banco = {"Lançamentos Mar-Jul 2026": [["Data", "Valor (R$)", "FITID"], ["2026-07-26", -100, "fit-1"]]}
        plano = gerar_plano(snap, juan, gta, banco, date(2026, 7, 26))
        self.assertEqual(plano["modo"], "dry_run_somente_leitura")
        self.assertEqual(plano["escritas_executadas"], 0)
        self.assertEqual(len(plano["banco"]["candidatos_data_valor"]), 1)
        self.assertFalse(plano["banco"]["candidatos_data_valor"][0]["confirmado"])
        self.assertEqual(plano["gta_documentos"]["gtas_raw_e_entradas"], 1)

    def test_ambiguidade_nao_vira_candidato(self):
        snap = snapshot_base()
        snap["tabelas"]["transacoes_banco"] = [
            {"id": "b1", "data": "2026-07-20", "valor": 10},
            {"id": "b2", "data": "2026-07-20", "valor": 10},
        ]
        snap["tabelas"]["fluxo_caixa"] = [{"id": "f1", "data": "2026-07-20", "valor": 10}]
        vazio = {"mensagens": []}
        planilha = {"NFs e GTA": []}
        banco = {"Lançamentos Mar-Jul 2026": []}
        plano = gerar_plano(snap, vazio, planilha, banco, date(2026, 7, 26))
        self.assertEqual(plano["banco"]["candidatos_data_valor"], [])
        self.assertEqual(len(plano["banco"]["ambiguidades_data_valor"]), 1)

    def test_data_futura_atipica_e_detectada(self):
        snap = snapshot_base()
        snap["tabelas"]["fluxo_caixa"] = [{"id": "futuro", "data": "2036-07-03", "valor": 1}]
        plano = gerar_plano(
            snap, {"mensagens": []}, {"NFs e GTA": []},
            {"Lançamentos Mar-Jul 2026": []}, date(2026, 7, 26),
        )
        self.assertEqual(len(plano["qualidade"]["fluxo_datas_outlier"]), 1)
        self.assertIn("fluxo_caixa_com_data_futura_atipica", plano["pendencias"])

    def test_plano_id_e_deterministico(self):
        snap = snapshot_base()
        argumentos = (
            snap, {"mensagens": []}, {"NFs e GTA": []},
            {"Lançamentos Mar-Jul 2026": []}, date(2026, 7, 26),
        )
        primeiro = gerar_plano(*argumentos)
        segundo = gerar_plano(*argumentos)
        self.assertEqual(primeiro["plano_id"], segundo["plano_id"])


if __name__ == "__main__":
    unittest.main()
