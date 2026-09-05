"""Guardas estáticas da consulta privada P1; nunca acessa banco real."""
from pathlib import Path
import re
import unittest

SQL = (Path(__file__).resolve().parents[1] / 'supabase/audits/vinculos_negocios_somente_leitura.sql').read_text()


class ConsultaVinculosTests(unittest.TestCase):
    def test_transacao_apenas_leitura_e_limites(self):
        self.assertIn('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;', SQL)
        self.assertIn("SET LOCAL statement_timeout = '15s';", SQL)
        self.assertIn("SET LOCAL lock_timeout = '1s';", SQL)
        self.assertIn("SET LOCAL idle_in_transaction_session_timeout = '20s';", SQL)
        self.assertRegex(SQL, r'ROLLBACK;\s*$')
        sem_comentarios = re.sub(r'--[^\n]*', '', SQL)
        self.assertNotRegex(sem_comentarios, r'(?i)\b(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE|MERGE|CALL|DO|COPY|COMMIT)\b')

    def test_oito_tabelas_projetadas_sem_truncamento(self):
        tabelas = re.findall(r'FROM public\.([a-z_]+)', SQL)
        self.assertEqual(set(tabelas), {
            'operacoes', 'compras', 'compras_componentes', 'confinex_avaliacoes',
            'confinex_estimativas', 'negocios_candidatos', 'transacoes_banco_staging',
            'transacoes_banco',
        })
        self.assertEqual(len(tabelas), 8)
        self.assertEqual(SQL.count('jsonb_agg(to_jsonb(t) ORDER BY id)'), 8)
        self.assertEqual(SQL.count("'[]'::jsonb"), 8)
        self.assertIn('jsonb_array_length(linhas)', SQL)
        self.assertNotRegex(SQL, r'(?i)\bLIMIT\s+\d|SELECT\s+\*')
        for campo in ('descricao', 'memo', 'obs', 'telefone', 'email', 'premissas', 'resultado', 'conteudo_origem'):
            self.assertNotRegex(SQL, rf'\b{campo}\b')

    def test_numeros_exatos_e_json_bruto_nao_exportado(self):
        for campo in ('quantidade', 'peso_total_kg', 'valor_total', 'versao', 'valor'):
            self.assertIn(campo + '::text', SQL)
        for campo in ('sexo', 'categoria', 'destino'):
            self.assertIn(f"jsonb_typeof(dados_origem->'{campo}') = 'string'", SQL)
            self.assertIn(f"length(dados_origem->>'{campo}') <= 80", SQL)
        self.assertIn("CASE WHEN jsonb_typeof(dados_origem) = 'object'", SQL)
        self.assertIn("THEN dados_origem ELSE '{}'::jsonb END", SQL)
        self.assertIn('AS dimensoes_formato_inesperado', SQL)
        self.assertNotRegex(SQL, r'(?i)SELECT[^;]*\bdados_origem\s*[,\n]\s*(?:FROM|observacoes)')


if __name__ == '__main__':
    unittest.main()
