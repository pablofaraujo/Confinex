import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import unittest


SQL = (
    Path(__file__).resolve().parents[1]
    / "supabase"
    / "audits"
    / "catalogo_chaves_somente_leitura.sql"
).read_text(encoding="utf-8")
SQL_PATH = Path(__file__).resolve().parents[1] / "supabase" / "audits" / "catalogo_chaves_somente_leitura.sql"


def _configuracao_postgresql_local(ambiente):
    """Retorna somente parâmetros explicitamente locais e seguros para o teste.

    O teste opcional nunca deve aceitar uma URI/conninfo em ``-d`` nem deixar
    variáveis ``PG*`` redirecionarem o cliente para outro banco.
    """

    socket_bruto = ambiente.get("CATALOGO_CHAVES_TESTE_SOCKET", "")
    if not socket_bruto:
        return None
    if (not socket_bruto.startswith("/") or "://" in socket_bruto
            or "=" in socket_bruto or "\x00" in socket_bruto):
        raise ValueError("socket local inválido")
    try:
        socket = Path(socket_bruto).resolve()
    except (OSError, RuntimeError, ValueError) as erro:
        raise ValueError("socket local inválido") from erro
    raizes_tmp = (Path("/tmp").resolve(), Path("/private/tmp").resolve())
    if not any(socket.is_relative_to(raiz) for raiz in raizes_tmp):
        raise ValueError("socket fora de diretório temporário local")

    def identificador(chave, padrao):
        valor = ambiente.get(chave, padrao)
        if not isinstance(valor, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", valor):
            raise ValueError(f"{chave} deve ser identificador simples")
        return valor

    usuario = identificador("CATALOGO_CHAVES_TESTE_USUARIO", "catalogoteste")
    banco = identificador("CATALOGO_CHAVES_TESTE_BANCO", "postgres")
    porta = ambiente.get("CATALOGO_CHAVES_TESTE_PORT", "5432")
    if not isinstance(porta, str) or not re.fullmatch(r"[0-9]+", porta):
        raise ValueError("porta local inválida")
    numero_porta = int(porta)
    if not 1 <= numero_porta <= 65535:
        raise ValueError("porta local inválida")
    return str(socket), str(numero_porta), usuario, banco


def _ambiente_psql_local(ambiente):
    """Remove overrides de libpq para os argumentos explícitos do teste."""

    return {chave: valor for chave, valor in ambiente.items() if not chave.startswith("PG")}


class CatalogoChavesSqlTests(unittest.TestCase):
    def test_transacao_e_limites_sao_somente_leitura(self):
        self.assertRegex(SQL, r"BEGIN\s+ISOLATION\s+LEVEL\s+REPEATABLE\s+READ\s+READ\s+ONLY\s*;")
        self.assertIn("SET LOCAL statement_timeout = '15s';", SQL)
        self.assertIn("SET LOCAL lock_timeout = '2s';", SQL)
        self.assertRegex(SQL, r"ROLLBACK\s*;\s*$")

    def test_nao_contem_ddl_dml_nem_leitura_de_linhas_de_usuario(self):
        # O inventário deve consultar apenas os catálogos; literais obrigatórios
        # de tipo/relkind não constituem dados de negócio.
        self.assertNotRegex(
            SQL,
            r"(?is)\b(?:CREATE|ALTER|DROP|TRUNCATE|INSERT|UPDATE|DELETE|MERGE|CALL|DO)\b",
        )
        self.assertNotRegex(SQL, r"(?i)\b(?:pg_get_expr|pg_attrdef|pg_description)\b")
        self.assertNotRegex(SQL, r"(?i)\b(?:FROM|JOIN)\s+public\.")
        for catalogo in (
            "pg_class",
            "pg_namespace",
            "pg_attribute",
            "pg_constraint",
            "pg_index",
        ):
            self.assertIn(f"pg_catalog.{catalogo}", SQL)

    def test_formato_top_level_e_objetos(self):
        for chave in (
            "versao",
            "fonte",
            "esquema",
            "somente_leitura",
            "objetos",
            "restricoes",
            "indices",
        ):
            self.assertIn(f"'{chave}'", SQL)
        for chave in ("nome", "tipo", "rls", "rls_forcada", "colunas"):
            self.assertIn(f"'{chave}'", SQL)
        self.assertRegex(SQL, r"rel\.relkind\s+IN\s*\('r',\s*'p',\s*'v',\s*'m'\)")
        self.assertIn("NOT atributo.attisdropped", SQL)
        self.assertRegex(SQL, r"ORDER BY\s+objeto\.relname,\s*objeto\.relkind")

    def test_restricoes_preservam_ordinais_e_referencias_fk(self):
        for chave in (
            "tabela",
            "nome",
            "tipo",
            "referencia",
            "validada",
            "herdada",
            "indice",
        ):
            self.assertIn(f"'{chave}'", SQL)
        self.assertIn("restricao.conkey, 1", SQL)
        self.assertIn("restricao.confkey, 1", SQL)
        self.assertIn("ORDER BY ordinal.ordinalidade", SQL)
        self.assertIn("ORDER BY referencia_ordinal.ordinalidade", SQL)
        self.assertRegex(SQL, r"WHEN 'f' THEN 'f'")
        self.assertIn("restricao.contype IN ('p', 'u', 'x')", SQL)

    def test_indices_separam_chave_include_expressao_e_restricao(self):
        for chave in (
            "tabela",
            "nome",
            "unico",
            "primario",
            "valido",
            "pronto",
            "vivo",
            "parcial",
            "expressao",
            "colunas",
            "incluidas",
            "restricao_propria",
            "nulos_nao_distintos",
        ):
            self.assertIn(f"'{chave}'", SQL)
        self.assertIn("ordinal.ordinalidade <= pg_indice.indnkeyatts", SQL)
        self.assertIn("inclusao_ordinal.ordinalidade > pg_indice.indnkeyatts", SQL)
        self.assertIn("pg_indice.indexprs IS NOT NULL", SQL)
        self.assertIn("pg_catalog.unnest(pg_indice.indkey) WITH ORDINALITY", SQL)
        self.assertIn("expressao_ordinal.indice_attnum = 0", SQL)
        self.assertIn("restricao.contype IN ('p', 'u', 'x')", SQL)
        self.assertIn("restricao.conindid = pg_indice.indexrelid", SQL)
        self.assertIn("pg_indice.indnullsnotdistinct", SQL)

    def test_arrays_tem_coalesce_e_ordem_estavel(self):
        self.assertGreaterEqual(SQL.count("'[]'::pg_catalog.jsonb"), 5)
        self.assertGreaterEqual(SQL.count("ORDER BY"), 8)
        self.assertIn("pg_catalog.to_jsonb", SQL)
        self.assertNotIn("pg_catalog.json_agg", SQL)

    def test_guard_local_rejeita_uri_conninfo_identificadores_porta_e_socket_remoto(self):
        base = {
            "CATALOGO_CHAVES_TESTE_SOCKET": "/private/tmp/catalogo-chaves.sock",
            "CATALOGO_CHAVES_TESTE_USUARIO": "catalogoteste",
            "CATALOGO_CHAVES_TESTE_BANCO": "postgres",
            "CATALOGO_CHAVES_TESTE_PORT": "5432",
        }
        self.assertEqual(
            _configuracao_postgresql_local(base),
            ("/private/tmp/catalogo-chaves.sock", "5432", "catalogoteste", "postgres"),
        )
        for campo, valor in (
            ("CATALOGO_CHAVES_TESTE_BANCO", "postgresql://host/db"),
            ("CATALOGO_CHAVES_TESTE_BANCO", "dbname=postgres host=evil"),
            ("CATALOGO_CHAVES_TESTE_USUARIO", "usuario ruim"),
            ("CATALOGO_CHAVES_TESTE_PORT", "5432abc"),
            ("CATALOGO_CHAVES_TESTE_PORT", "0"),
            ("CATALOGO_CHAVES_TESTE_PORT", "65536"),
            ("CATALOGO_CHAVES_TESTE_SOCKET", "postgresql://evil/db"),
            ("CATALOGO_CHAVES_TESTE_SOCKET", "/var/run/postgresql"),
        ):
            configuracao = dict(base)
            configuracao[campo] = valor
            with self.subTest(campo=campo, valor=valor), self.assertRaises(ValueError):
                _configuracao_postgresql_local(configuracao)

    def test_guard_local_higieniza_variaveis_pg(self):
        ambiente = {"PGHOST": "db.evil", "PGSERVICE": "remoto", "LANG": "C", "PGPORT": "6543"}
        higienizado = _ambiente_psql_local(ambiente)
        self.assertEqual(higienizado, {"LANG": "C"})

    @unittest.skipUnless(
        os.environ.get("CATALOGO_CHAVES_TESTE_LOCAL") == "1",
        "teste PostgreSQL local opcional; não executado por padrão",
    )
    def test_execucao_postgresql_local_opcional(self):
        """Executa a consulta apenas quando apontada a um socket temporário local."""

        try:
            configuracao = _configuracao_postgresql_local(os.environ)
        except ValueError as erro:
            self.skipTest(str(erro))
        if configuracao is None:
            self.skipTest("socket local não configurado")
        socket, porta, usuario, banco = configuracao
        psql = shutil.which("psql")
        if not psql:
            self.skipTest("psql não disponível")
        ambiente = _ambiente_psql_local(os.environ)
        comando = [
            psql,
            "-X",
            "-v",
            "ON_ERROR_STOP=1",
            "-At",
            "-w",
            "-h",
            socket,
            "-p",
            porta,
            "-U",
            usuario,
            "-d",
            banco,
            "-f",
            str(SQL_PATH),
        ]
        resultado = subprocess.run(comando, check=True, capture_output=True, text=True,
                                   env=ambiente, timeout=20)
        documento = json.loads(next(linha for linha in resultado.stdout.splitlines() if linha.startswith("{")))
        self.assertEqual(documento["versao"], 1)
        self.assertEqual(documento["fonte"], "pg_catalog")
        self.assertTrue(documento["somente_leitura"])
        self.assertIsInstance(documento["objetos"], list)
        self.assertIsInstance(documento["restricoes"], list)
        self.assertIsInstance(documento["indices"], list)


if __name__ == "__main__":
    unittest.main()
