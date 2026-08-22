import unittest

from tools.auditar_staging_consolidacao import (
    auditar,
    chave_aparente,
    separar_campos_faltantes,
)
from tools.exportar_snapshot_consolidacao import TABELAS_PERMITIDAS


class LeitorFalso:
    def __init__(self, dados):
        self.dados = dados

    def listar(self, tabela):
        return list(self.dados.get(tabela, []))


class AuditarStagingConsolidacaoTest(unittest.TestCase):
    def test_detecta_duplicidade_e_nao_escreve(self):
        candidato = {
            "id": "c1", "nome": "Lote A", "contexto": "Grupo",
            "data_base": "2026-08-01", "sexo": "macho", "categoria": "garrote",
            "destino": "confinamento", "quantidade": 20,
            "estado": "rascunho", "prioridade": "alta", "campos_faltantes": ["peso"],
        }
        dados = {tabela: [] for tabela in TABELAS_PERMITIDAS}
        dados["negocios_candidatos"] = [candidato, {**candidato, "id": "c2"}]
        resultado = auditar(LeitorFalso(dados))
        self.assertEqual(resultado["escritas_executadas"], 0)
        self.assertEqual(resultado["tabelas_operacionais_alteradas"], 0)
        self.assertEqual(resultado["candidatos"]["grupos_duplicidade_aparente"], 1)
        self.assertEqual(resultado["candidatos"]["campos_faltantes"], {"peso": 2})

    def test_chave_normaliza_variacoes_de_espaco(self):
        primeiro = {"nome": "Lote A", "contexto": "Grupo Um", "quantidade": 10}
        segundo = {"nome": " lote-a ", "contexto": "grupo  um", "quantidade": 10}
        self.assertEqual(chave_aparente(primeiro), chave_aparente(segundo))

    def test_separa_campos_faltantes_legados(self):
        self.assertEqual(
            separar_campos_faltantes(["peso total, valor total", "pagamento"]),
            ["peso total", "valor total", "pagamento"],
        )

    def test_detecta_fitid_ja_operacional_e_referencia_exata(self):
        dados = {tabela: [] for tabela in TABELAS_PERMITIDAS}
        dados["negocios_candidatos"] = [{
            "id": "c1", "codigo_fonte": "NEG-26-001", "chave_rastreio": "fonte-1",
            "nome": "Lote", "contexto": "Grupo", "prioridade": "alta",
            "estado": "em_revisao", "campos_faltantes": [],
        }]
        dados["operacoes"] = [{"id": "o1", "codigo": "NEG-26-001"}]
        dados["transacoes_banco_staging"] = [{"id": "s1", "fitid": "pix-1"}]
        dados["transacoes_banco"] = [{"id": "b1", "id_externo": "pix-1"}]
        resultado = auditar(LeitorFalso(dados))
        self.assertEqual(
            resultado["candidatos"]["correspondencias_por_referencia_em_operacoes"]["única"],
            1,
        )
        self.assertEqual(resultado["banco_staging"]["fitid_ja_existe_em_transacoes_banco"], 1)


if __name__ == "__main__":
    unittest.main()
