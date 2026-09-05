from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import inventariar_esquema_chaves as inventario


class RespostaFalsa:
    def __init__(self, dados):
        self.dados = dados

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, limite=-1):
        return self.dados


def catalogo_sql_sintetico():
    return {
        "versao": 1,
        "fonte": "pg_catalog",
        "esquema": "public",
        "somente_leitura": True,
        "objetos": [
            {
                "nome": "filhas",
                "tipo": "r",
                "rls": False,
                "rls_forcada": False,
                "colunas": [
                    {"nome": "codigo", "posicao": 2, "tipo": "text", "nao_nulo": True},
                    {"nome": "serie", "posicao": 1, "tipo": "integer", "nao_nulo": True},
                ],
            },
            {
                "nome": "pais",
                "tipo": "r",
                "rls": True,
                "rls_forcada": True,
                "colunas": [
                    {"nome": "codigo", "posicao": 1, "tipo": "text", "nao_nulo": True},
                    {"nome": "serie", "posicao": 2, "tipo": "integer", "nao_nulo": True},
                ],
            },
            {
                "nome": "visao_pais",
                "tipo": "v",
                "rls": False,
                "rls_forcada": False,
                "colunas": [],
            },
        ],
        "restricoes": [
            {
                "tabela": "pais",
                "nome": "pais_codigo_serie_uq",
                "tipo": "u",
                "colunas": ["codigo", "serie"],
                "referencia": None,
                "validada": True,
                "herdada": False,
                "indice": "pais_codigo_serie_uq",
            },
            {
                "tabela": "filhas",
                "nome": "filhas_pais_fk",
                "tipo": "f",
                "colunas": ["codigo", "serie"],
                "referencia": {"esquema": "public", "tabela": "pais", "colunas": ["codigo", "serie"]},
                "validada": True,
                "herdada": False,
                "indice": None,
            },
            {
                "tabela": "pais",
                "nome": "pais_pkey",
                "tipo": "p",
                "colunas": ["codigo"],
                "referencia": None,
                "validada": True,
                "herdada": False,
                "indice": "pais_pkey",
            },
        ],
        "indices": [
            {
                "tabela": "pais",
                "nome": "pais_parcial",
                "unico": True,
                "primario": False,
                "valido": True,
                "pronto": True,
                "vivo": True,
                "parcial": True,
                "expressao": False,
                "colunas": ["serie"],
                "incluidas": [],
                "restricao_propria": None,
                "nulos_nao_distintos": False,
            },
            {
                "tabela": "pais",
                "nome": "pais_codigo_serie_uq",
                "unico": True,
                "primario": False,
                "valido": True,
                "pronto": True,
                "vivo": True,
                "parcial": False,
                "expressao": False,
                "colunas": ["codigo", "serie"],
                "incluidas": [],
                "restricao_propria": "pais_codigo_serie_uq",
                "nulos_nao_distintos": False,
            },
            {
                "tabela": "pais",
                "nome": "pais_pkey",
                "unico": True,
                "primario": True,
                "valido": True,
                "pronto": True,
                "vivo": True,
                "parcial": False,
                "expressao": False,
                "colunas": ["codigo"],
                "incluidas": [],
                "restricao_propria": "pais_pkey",
                "nulos_nao_distintos": False,
            },
            {
                "tabela": "pais",
                "nome": "pais_include",
                "unico": False,
                "primario": False,
                "valido": True,
                "pronto": True,
                "vivo": True,
                "parcial": False,
                "expressao": False,
                "colunas": ["codigo"],
                "incluidas": ["serie"],
                "restricao_propria": None,
                "nulos_nao_distintos": False,
            },
            {
                "tabela": "pais",
                "nome": "pais_expressao",
                "unico": False,
                "primario": False,
                "valido": True,
                "pronto": True,
                "vivo": True,
                "parcial": False,
                "expressao": True,
                "colunas": [None],
                "incluidas": [],
                "restricao_propria": None,
                "nulos_nao_distintos": False,
            },
        ],
    }


class InventarioEsquemaChavesTests(unittest.TestCase):
    def test_pk_e_unique_com_include_nao_expandem_chave(self):
        # Forma confirmada em PostgreSQL efêmero: conkey exclui INCLUDE,
        # enquanto indkey inclui e indnkeyatts delimita as colunas-chave.
        bruto = catalogo_sql_sintetico()
        pai = next(o for o in bruto["objetos"] if o["nome"] == "pais")
        pai["colunas"].append({"nome": "descricao", "posicao": 3, "tipo": "text", "nao_nulo": False})
        for indice in bruto["indices"]:
            if indice["nome"] == "pais_pkey":
                indice["incluidas"] = ["serie"]
            if indice["nome"] == "pais_codigo_serie_uq":
                indice["incluidas"] = ["descricao"]
        catalogo = inventario.projetar_catalogo_sql(bruto)
        pk = next(r for r in catalogo["restricoes"] if r["tipo"] == "p")
        unique = next(r for r in catalogo["restricoes"] if r["tipo"] == "u")
        self.assertEqual(pk["colunas"], ["codigo"])
        self.assertEqual(unique["colunas"], ["codigo", "serie"])
        self.assertEqual(inventario.resumir(catalogo)["chaves_compostas_declaradas"], 1)

    def test_openapi_sanitiza_descricao_exemplos_host_paths_e_nao_promove_pk_fk(self):
        bruto = {
            "swagger": "2.0",
            "host": "malicioso.example",
            "basePath": "/rest/v1",
            "paths": {"/segredo": {"get": {"description": "payload"}}},
            "securityDefinitions": {"api_key": {"default": "sb_secret"}},
            "definitions": {
                "Pais": {
                    "type": "object",
                    "required": ["id"],
                    "properties": {
                        "id": {
                            "type": "integer",
                            "description": "<pk/> credencial sb_secret payload",
                            "default": "sb_secret",
                            "example": "payload",
                            "examples": ["credencial"],
                        },
                        "pai": {
                            "type": "string",
                            "description": "<fk table='Pais'> referência",
                            "default": "segredo",
                        },
                    },
                }
            },
        }
        projetado = inventario.projetar_openapi(bruto)
        texto = json.dumps(projetado, ensure_ascii=False)
        for segredo in ("malicioso.example", "sb_secret", "payload", "credencial", "segredo", "paths"):
            self.assertNotIn(segredo, texto)
        self.assertIsNone(projetado["restricoes"])
        self.assertIsNone(projetado["indices"])
        self.assertTrue(projetado["objetos"][0]["colunas"][0]["pk_anotada"])
        self.assertTrue(projetado["objetos"][0]["colunas"][1]["fk_anotada"])

    def test_sql_ordena_e_preserva_fk_composta_include_expressao_parcial(self):
        catalogo = inventario.projetar_catalogo_sql(catalogo_sql_sintetico())
        self.assertEqual([o["nome"] for o in catalogo["objetos"]], ["filhas", "pais", "visao_pais"])
        self.assertEqual([c["nome"] for c in catalogo["objetos"][0]["colunas"]], ["serie", "codigo"])
        fk = next(c for c in catalogo["restricoes"] if c["tipo"] == "f")
        self.assertEqual(fk["colunas"], ["codigo", "serie"])
        self.assertEqual(fk["referencia"]["colunas"], ["codigo", "serie"])
        indices = {i["nome"]: i for i in catalogo["indices"]}
        self.assertEqual(indices["pais_include"]["colunas"], ["codigo"])
        self.assertEqual(indices["pais_include"]["incluidas"], ["serie"])
        self.assertEqual(indices["pais_expressao"]["colunas"], [None])
        self.assertTrue(indices["pais_expressao"]["expressao"])
        self.assertTrue(indices["pais_parcial"]["parcial"])

    def test_resumo_distingue_null_openapi_de_zero_sql_e_nao_duplica_indice_de_suporte(self):
        openapi = inventario.projetar_openapi({"swagger": "2.0", "definitions": {}})
        resumo_openapi = inventario.resumir(openapi)
        for campo in ("pk", "unique_restricoes", "fk", "indices_unicos_ativos", "tabelas_com_rls"):
            self.assertIsNone(resumo_openapi[campo])
        catalogo_vazio = deepcopy(catalogo_sql_sintetico())
        catalogo_vazio["restricoes"] = []
        catalogo_vazio["indices"] = []
        resumo_vazio = inventario.resumir(inventario.projetar_catalogo_sql(catalogo_vazio))
        self.assertEqual(resumo_vazio["pk"], 0)
        self.assertEqual(resumo_vazio["unique_restricoes"], 0)
        self.assertEqual(resumo_vazio["fk"], 0)
        self.assertEqual(resumo_vazio["indices_unicos_ativos"], 0)
        resumo_sql = inventario.resumir(inventario.projetar_catalogo_sql(catalogo_sql_sintetico()))
        self.assertEqual(resumo_sql["pk"], 1)
        self.assertEqual(resumo_sql["unique_restricoes"], 1)
        self.assertEqual(resumo_sql["fk"], 1)
        self.assertEqual(resumo_sql["fk_compostas"], 1)
        self.assertEqual(resumo_sql["indices_unicos_autonomos"], 1)
        self.assertEqual(resumo_sql["indices_unicos_parciais"], 1)

    def test_hash_estavel_e_mudanca_detectada(self):
        base = catalogo_sql_sintetico()
        self.assertEqual(inventario.assinatura(base), inventario.assinatura(deepcopy(base)))
        alterado = deepcopy(base)
        alterado["objetos"][0]["nome"] = "filhas_alteradas"
        with self.assertRaises(ValueError):
            inventario.gerar_relatorio(base, alterado)

    def test_relatorio_offline_assina_catalogo_sem_comparar_antes_depois(self):
        catalogo = inventario.projetar_catalogo_sql(catalogo_sql_sintetico())
        relatorio = inventario.gerar_relatorio(catalogo, None)
        verificacao = relatorio["verificacao"]
        self.assertEqual(verificacao["assinatura_catalogo"], inventario.assinatura(catalogo))
        self.assertIsNone(verificacao["arquivo_inalterado"])
        self.assertIsNone(verificacao["metadados_inalterados"])
        self.assertIsNone(verificacao["assinatura_antes"])
        self.assertIsNone(verificacao["assinatura_depois"])
        self.assertEqual(verificacao["acessos_rede"], 0)

    def test_cli_arquivo_valido_confirma_bytes_inalterados(self):
        bruto = json.dumps(catalogo_sql_sintetico(), ensure_ascii=False, indent=2).encode()
        with tempfile.TemporaryDirectory() as temporario:
            arquivo = Path(temporario) / "catalogo.json"
            arquivo.write_bytes(bruto)
            processo = subprocess.run(
                [sys.executable, str(Path(__file__).resolve().parent / "inventariar_esquema_chaves.py"),
                 "--arquivo", str(arquivo), "--stdout"],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(processo.returncode, 0, processo.stderr)
            relatorio = json.loads(processo.stdout)
            verificacao = relatorio["verificacao"]
            self.assertTrue(verificacao["arquivo_inalterado"])
            self.assertEqual(verificacao["assinatura_arquivo"], hashlib.sha256(bruto).hexdigest())
            self.assertIsNone(verificacao["metadados_inalterados"])
            self.assertIsNone(verificacao["assinatura_antes"])
            self.assertIsNone(verificacao["assinatura_depois"])

    def test_sql_rejeita_coerencias_de_pk_listas_e_ligacoes(self):
        casos = []

        pk_coluna_ausente = deepcopy(catalogo_sql_sintetico())
        pk_coluna_ausente["restricoes"][2]["colunas"] = ["nao_existe"]
        casos.append(("pk_coluna_ausente", pk_coluna_ausente))

        listas_objeto = deepcopy(catalogo_sql_sintetico())
        listas_objeto["indices"] = {}
        casos.append(("indices_nao_lista", listas_objeto))

        indice_suporte_autonomo = deepcopy(catalogo_sql_sintetico())
        indice_suporte_autonomo["indices"][1]["restricao_propria"] = None
        casos.append(("indice_suporte_autonomo", indice_suporte_autonomo))

        ligacao_quebrada = deepcopy(catalogo_sql_sintetico())
        ligacao_quebrada["restricoes"][0]["indice"] = "indice_inexistente"
        casos.append(("ligacao_quebrada", ligacao_quebrada))

        for nome, invalido in casos:
            with self.subTest(nome=nome), self.assertRaises(ValueError):
                inventario.projetar_catalogo_sql(invalido)

    def test_consulta_eh_get_com_timeout_15_sem_corpo_e_headers_corretos(self):
        capturado = {}

        def abrir(request, timeout):
            capturado["request"] = request
            capturado["timeout"] = timeout
            return RespostaFalsa(b'{"swagger":"2.0","definitions":{}}')

        inventario.consultar_openapi(
            {"SUPABASE_URL": "https://abc-123.supabase.co", "SUPABASE_SERVICE_KEY": "sb_secret"},
            abrir=abrir,
        )
        request = capturado["request"]
        self.assertEqual(request.method, "GET")
        self.assertIsNone(request.data)
        self.assertEqual(capturado["timeout"], 15)
        self.assertEqual(request.get_header("Apikey"), "sb_secret")
        self.assertIsNone(request.get_header("Authorization"))

        inventario.consultar_openapi(
            {"SUPABASE_URL": "https://abc-123.supabase.co", "SUPABASE_SERVICE_KEY": "eyJheader.payload.sig"},
            abrir=abrir,
        )
        request_jwt = capturado["request"]
        self.assertEqual(request_jwt.get_header("Apikey"), "eyJheader.payload.sig")
        self.assertEqual(request_jwt.get_header("Authorization"), "Bearer eyJheader.payload.sig")
        self.assertIsNone(request_jwt.data)

    def test_url_de_destino_rejeita_usuario_porta_e_subdominio_malicioso(self):
        for url in (
            "http://abc.supabase.co",
            "https://abc.supabase.co:443",
            "https://usuario@abc.supabase.co",
            "https://abc.supabase.co.evil.test",
            "https://abc.supabase.co.attacker.supabase.co",
            "https://abc.supabase.co/rest/v1",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                inventario.consultar_openapi(
                    {"SUPABASE_URL": url, "SUPABASE_SERVICE_KEY": "sb_secret"},
                    abrir=Mock(),
                )

    def test_redirect_recusado_e_resposta_acima_de_10mb(self):
        with self.assertRaisesRegex(ValueError, "redirecionamento_recusado"):
            inventario.SemRedirecionamento().redirect_request(None, None, 302, "", {}, "https://evil.test")

        with self.assertRaisesRegex(ValueError, "resposta_acima_do_limite"):
            inventario.consultar_openapi(
                {"SUPABASE_URL": "https://abc.supabase.co", "SUPABASE_SERVICE_KEY": "sb_secret"},
                abrir=lambda *args, **kwargs: RespostaFalsa(b"x" * (inventario.MAX_BYTES + 1)),
            )

    def test_saida_privada_nova_nao_sobrescreve_existente(self):
        relatorio = inventario.gerar_relatorio(
            inventario.projetar_catalogo_sql(catalogo_sql_sintetico()),
            inventario.projetar_catalogo_sql(catalogo_sql_sintetico()),
        )
        with tempfile.TemporaryDirectory() as temporario:
            destino = Path(temporario) / "saida"
            destino.mkdir()
            marcador = destino / "nao-apagar.txt"
            marcador.write_text("intacto", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                inventario.salvar(relatorio, destino)
            self.assertEqual(marcador.read_text(encoding="utf-8"), "intacto")

    def test_saida_fora_de_area_privada_e_rejeitada_antes_de_criar_diretorio(self):
        relatorio = inventario.gerar_relatorio(
            inventario.projetar_catalogo_sql(catalogo_sql_sintetico()),
            inventario.projetar_catalogo_sql(catalogo_sql_sintetico()),
        )
        with self.assertRaisesRegex(ValueError, "saida_deve_ser_privada"):
            inventario.salvar(relatorio, Path("/private/catalogo-chaves-nao-privado"))

    def test_cli_falha_sem_vazar_payload_ou_credencial(self):
        with tempfile.TemporaryDirectory() as temporario:
            arquivo = Path(temporario) / "invalido.json"
            arquivo.write_text(json.dumps({"segredo": "sb_secret", "payload": "privado"}), encoding="utf-8")
            processo = subprocess.run(
                [sys.executable, str(Path(__file__).resolve().parent / "inventariar_esquema_chaves.py"),
                 "--arquivo", str(arquivo), "--stdout"],
                capture_output=True,
                text=True,
                env={**os.environ, "SUPABASE_SERVICE_KEY": "sb_secret"},
                check=False,
            )
            self.assertNotEqual(processo.returncode, 0)
            self.assertIn("Inventário não gerado", processo.stderr)
            self.assertNotIn("sb_secret", processo.stderr)
            self.assertNotIn("privado", processo.stderr)


if __name__ == "__main__":
    unittest.main()
