import hashlib
import json
import unittest
from decimal import Decimal

from atestar_cobertura_adaptador import (
    assinar_atestado_cobertura,
    hash_pedido,
    json_canonico_postgres,
)


class AtestarCoberturaAdaptadorTest(unittest.TestCase):
    def base(self):
        return {
            "segredo": b"s" * 32,
            "chave_id": "key_teste.0001",
            "adaptador": "ofx",
            "adaptador_version": "v1",
            "artefato_hash": "a" * 64,
            "familia_fonte": "financeira_estruturada",
            "consulta_hash": "b" * 64,
            "consulta_ref": "qref_" + "b" * 32,
            "tarefa_id": "11111111-1111-4111-8111-111111111111",
            "investigacao_id": "22222222-2222-4222-8222-222222222222",
            "lease_token": "33333333-3333-4333-8333-333333333333",
            "fencing_token": 7,
            "estado_cobertura": "completa",
            "estado_resultado": "evidencia_insuficiente",
            "bundle": {"evidencias": [{"valor": Decimal("10.50")}],
                       "alternativas": [], "pendencias": [], "ligacoes": []},
            "inicio_confirmado": True,
            "fim_confirmado": True,
            "paginas_confirmadas": 1,
            "registros_confirmados": 1,
            "paginacao_modo": "nao_paginado",
            "artefato_cobertura_tipo": "snapshot_fonte",
            "cursor_final_hash": None,
            "snapshot_fonte_hash": "c" * 64,
        }

    def test_json_canonico_cobre_unicode_arrays_nulos_booleanos_e_decimais(self):
        valor = {
            "z": [None, True, False, Decimal("1.2300"), Decimal("1E+2")],
            "á": {"b": "boi", "a": "ação"},
        }
        self.assertEqual(
            json_canonico_postgres(valor),
            '{"z":[null,true,false,1.2300,100],"á":{"a":"ação","b":"boi"}}',
        )

    def test_numeros_zero_canonicos_e_booleano_nao_vira_contagem(self):
        # Postgres jsonb não preserva o sinal nem a notação exponencial de
        # zero. O assinador deve produzir exatamente a mesma representação.
        self.assertEqual(json_canonico_postgres(0), "0")
        self.assertEqual(json_canonico_postgres(Decimal("-0")), "0")
        self.assertEqual(json_canonico_postgres(Decimal("0E+2")), "0")
        self.assertEqual(json_canonico_postgres(Decimal("0.00")), "0.00")

        base = self.base()
        base["paginas_confirmadas"] = True
        with self.assertRaisesRegex(ValueError, "contagem_cobertura_invalida"):
            assinar_atestado_cobertura(**base)
        base = self.base()
        base["registros_confirmados"] = False
        with self.assertRaisesRegex(ValueError, "contagem_cobertura_invalida"):
            assinar_atestado_cobertura(**base)
        base = self.base()
        base["fencing_token"] = True
        with self.assertRaisesRegex(ValueError, "fencing_token_invalido"):
            assinar_atestado_cobertura(**base)

    def test_assinatura_e_deterministica_e_vincula_bundle_integral(self):
        base = self.base()
        primeira = assinar_atestado_cobertura(**base)
        segunda = assinar_atestado_cobertura(**base)
        self.assertEqual(primeira, segunda)
        alterado = self.base()
        alterado["bundle"] = {**alterado["bundle"], "evidencias": [{"valor": 11}]}
        terceira = assinar_atestado_cobertura(**alterado)
        self.assertNotEqual(primeira["pedido_hash"], terceira["pedido_hash"])
        self.assertNotEqual(primeira["hmac"], terceira["hmac"])

    def test_hash_pedido_nao_inclui_segredo(self):
        base = self.base()
        pedido = hash_pedido(
            estado_cobertura=base["estado_cobertura"],
            estado_resultado=base["estado_resultado"], bundle=base["bundle"],
            resumo_sanitizado=None, erro_codigo=None, erro_sanitizado=None,
        )
        self.assertRegex(pedido, r"^[0-9a-f]{64}$")
        self.assertNotIn("s" * 16, json.dumps({"pedido_hash": pedido}))

    def test_falha_antes_da_primeira_pagina_nao_inventa_snapshot(self):
        base = self.base()
        base.update({
            "estado_cobertura": "reautenticacao_necessaria",
            "estado_resultado": "cobertura_incompleta",
            "bundle": {"evidencias": [], "alternativas": [],
                       "pendencias": [], "ligacoes": []},
            "inicio_confirmado": False, "fim_confirmado": False,
            "paginas_confirmadas": 0, "registros_confirmados": 0,
            "paginacao_modo": "nao_iniciada",
            "artefato_cobertura_tipo": "erro_pre_resposta",
            "cursor_final_hash": None, "snapshot_fonte_hash": None,
        })
        prova = assinar_atestado_cobertura(**base)
        self.assertEqual(prova["paginas_confirmadas"], 0)
        self.assertIsNone(prova["snapshot_fonte_hash"])

    def test_falha_parcial_exige_cursor_e_snapshot(self):
        base = self.base()
        base.update({
            "estado_cobertura": "cobertura_incompleta",
            "estado_resultado": "cobertura_incompleta",
            "fim_confirmado": False, "paginas_confirmadas": 2,
            "paginacao_modo": "parcial",
            "artefato_cobertura_tipo": "snapshot_parcial",
            "cursor_final_hash": "d" * 64,
        })
        self.assertRegex(assinar_atestado_cobertura(**base)["hmac"], r"^[0-9a-f]{64}$")
        base["cursor_final_hash"] = None
        with self.assertRaisesRegex(ValueError, "cobertura_de_falha_incoerente"):
            assinar_atestado_cobertura(**base)

    def test_pre_resposta_rejeita_evidencia(self):
        base = self.base()
        base.update({
            "estado_cobertura": "indisponivel",
            "estado_resultado": "cobertura_incompleta",
            "inicio_confirmado": False, "fim_confirmado": False,
            "paginas_confirmadas": 0, "registros_confirmados": 0,
            "paginacao_modo": "nao_iniciada",
            "artefato_cobertura_tipo": "erro_pre_resposta",
            "cursor_final_hash": None, "snapshot_fonte_hash": None,
        })
        # A igualdade exata entre registros confirmados e evidências é
        # verificada antes da classificação da falha; assim nenhuma evidência
        # consegue atravessar um erro pré-resposta.
        with self.assertRaisesRegex(ValueError, "evidencias_divergem_dos_registros_confirmados"):
            assinar_atestado_cobertura(**base)


if __name__ == "__main__":
    unittest.main()
