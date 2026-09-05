"""Guardas da consulta de proveniência; não acessa banco real."""
from pathlib import Path
import re
import unittest

SQL = (Path(__file__).resolve().parents[1] /
       'supabase/audits/proveniencia_ofx_somente_leitura.sql').read_text()


class ProvenienciaOfxSqlTests(unittest.TestCase):
    def test_apenas_leitura_com_limites_e_rollback(self):
        self.assertIn('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;', SQL)
        for limite in ("statement_timeout = '15s'", "lock_timeout = '1s'",
                       "idle_in_transaction_session_timeout = '20s'"):
            self.assertIn('SET LOCAL ' + limite, SQL)
        self.assertRegex(SQL, r'ROLLBACK;\s*$')
        sem_comentarios = re.sub(r'--[^\n]*', '', SQL)
        self.assertNotRegex(sem_comentarios,
                            r'(?i)\b(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE|MERGE|CALL|DO|COPY|COMMIT)\b')

    def test_projecao_completa_sem_payloads(self):
        self.assertEqual(re.findall(r'FROM public\.([a-z_]+)', SQL),
                         ['fontes_importacao', 'transacoes_banco_staging'])
        self.assertEqual(SQL.count('jsonb_agg(to_jsonb(t) ORDER BY id)'), 2)
        self.assertIn('jsonb_array_length(linhas)', SQL)
        self.assertNotRegex(SQL, r'(?i)\bLIMIT\s+\d|SELECT\s+\*')
        for campo in ('nome_arquivo', 'descricao', 'memo', 'metadados',
                      'observacoes', 'origem_referencia'):
            self.assertNotRegex(SQL, rf'\b{campo}\b')

    def test_proveniencia_valores_exatos_e_hash_validado(self):
        self.assertIn('fonte_importacao_id', SQL)
        self.assertIn('hash_sha256', SQL)
        self.assertIn('valor::text', SQL)
        self.assertIn('quantidade_registros::text', SQL)
        self.assertIn("~ '^[a-fA-F0-9]{64}$'", SQL)
        self.assertIn('AS hash_origem_presente', SQL)
        self.assertIn("'papel_sql', current_user", SQL)


if __name__ == '__main__':
    unittest.main()
