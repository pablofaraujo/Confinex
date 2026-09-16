import copy
import unittest

from tools.planejar_acerto_contrato_confinamento import (
    ContratoPlanejamentoInvalido,
    planejar_acerto_contrato as _planejar_acerto_contrato,
)


ETAPAS_PENDENTES = {
    "recebido": "pendente",
    "conferido": "desconhecido",
    "aprovado": "pendente",
    "recebimento_bancario": "desconhecido",
    "assinatura": "pendente",
    "envio": "pendente",
}

AUTORIZACOES = [{
    "contato_ref": "ct-adm", "escopos": ["acerto", "contrato", "aditivo"],
}]


def planejar_acerto_contrato(entrada, autorizacoes_leitura=None):
    return _planejar_acerto_contrato(
        entrada,
        autorizacoes_leitura=(
            copy.deepcopy(AUTORIZACOES)
            if autorizacoes_leitura is None else autorizacoes_leitura
        ),
    )


def fixture():
    return {
        "schema_version": "entrada-acerto-contrato-confinamento-v1",
        "operacao": {"operacao_ref": "op-fixture", "confinamento_ref": "conf-fixture"},
        "confinamento": {"confinamento_ref": "conf-fixture"},
        "vinculos_contatos": [{
            "confinamento_ref": "conf-fixture", "contato_ref": "ct-adm",
            "papel": "administrativo", "principal": False,
        }],
        "abates": [
            {
                "abate_ref": "abate-1", "operacao_ref": "op-fixture",
                "periodo_inicio": "2026-01-01", "periodo_fim": "2026-01-10",
                "animais_qtd": 40, "romaneio_ref": "rom-1",
            },
            {
                "abate_ref": "abate-2", "operacao_ref": "op-fixture",
                "periodo_inicio": "2026-01-11", "periodo_fim": "2026-01-20",
                "animais_qtd": 35, "romaneio_ref": None,
            },
        ],
        "acertos": [{
            "acerto_ref": "acerto-1", "abate_ref": "abate-1",
            "operacao_ref": "op-fixture", "status_textual": "pago",
            "custos": [{"item_ref": "custo-1", "valor": 125.5}],
            "descontos": [{"item_ref": "desc-1", "valor": 25}],
            "etapas": {
                "recebido": "confirmado", "conferido": "confirmado",
                "aprovado": "confirmado", "recebimento_bancario": "confirmado",
                "assinatura": "desconhecido", "envio": "desconhecido",
            },
        }],
        "documentos": [{
            "documento_ref": "doc-1", "conteudo_sha256": "a" * 64,
            "operacao_ref": "op-fixture", "tipo": "contrato",
            "extraido": {"quantidade": 75, "partes": ["Parte A", "Parte B"]},
            "negocio": {"quantidade": 75},
            "termos_aprovados": {"partes": ["Parte A", "Parte B"]},
            "etapas": copy.deepcopy(ETAPAS_PENDENTES),
        }],
        "evidencias_recebimento": [],
    }


class PlanejarAcertoContratoTests(unittest.TestCase):
    def test_determinismo_e_zero_acoes(self):
        entrada = fixture()
        a = planejar_acerto_contrato(entrada)
        b = planejar_acerto_contrato(copy.deepcopy(entrada))
        self.assertEqual(a, b)
        self.assertEqual(a["estado"], "somente_previa")
        self.assertFalse(a["autoriza_escrita"])
        self.assertEqual(a["escritas"], 0)
        self.assertTrue(all(valor is False for valor in a["acoes_externas"].values()))

    def test_acerto_final_de_um_abate_nao_fecha_outro(self):
        plano = planejar_acerto_contrato(fixture())
        por_ref = {item["abate_ref"]: item for item in plano["abates"]}
        self.assertEqual(por_ref["abate-1"]["estado"], "pendente_conferencia")
        self.assertEqual(por_ref["abate-2"]["estado"], "pendente_sem_acerto")
        self.assertTrue(por_ref["abate-1"]["outros_abates_nao_sao_fechados_por_este_acerto"])

    def test_status_pago_sem_extrato_nao_confirma_recebimento(self):
        plano = planejar_acerto_contrato(fixture())
        acerto = plano["abates"][0]["acertos"][0]
        self.assertEqual(acerto["status_textual_evidencia"], "pago")
        self.assertEqual(acerto["evidencias_recebimento_refs"], [])
        self.assertIn("recebimento_bancario_sem_evidencia_vinculada", acerto["alertas_etapas"])
        self.assertEqual(plano["abates"][0]["campos"]["recebimento_bancario"], "pendente")

    def test_extrato_com_vinculo_explicito_e_separado(self):
        entrada = fixture()
        entrada["evidencias_recebimento"] = [{
            "evidencia_ref": "ev-1", "operacao_ref": "op-fixture",
            "abate_ref": "abate-1", "acerto_ref": "acerto-1",
            "tipo": "extrato", "vinculo_explicito": True,
        }]
        acerto = planejar_acerto_contrato(entrada)["abates"][0]["acertos"][0]
        self.assertEqual(acerto["evidencias_recebimento_refs"], ["ev-1"])
        self.assertNotIn("recebimento_bancario_sem_evidencia_vinculada", acerto["alertas_etapas"])

    def test_principal_nao_autoriza_e_dois_autorizados_sao_ambiguos(self):
        entrada = fixture()
        entrada["vinculos_contatos"][0]["principal"] = True
        sem_auth = planejar_acerto_contrato(entrada, [])
        self.assertEqual(
            sem_auth["contatos_por_escopo"]["acerto"]["estado"],
            "pendente_sem_contato_autorizado",
        )
        entrada = fixture()
        entrada["vinculos_contatos"].append({
            "confinamento_ref": "conf-fixture", "contato_ref": "ct-finpec",
            "papel": "finpec", "principal": True,
        })
        autorizacoes = copy.deepcopy(AUTORIZACOES)
        autorizacoes.append({
            "contato_ref": "ct-finpec", "escopos": ["acerto"],
        })
        ambiguo = planejar_acerto_contrato(entrada, autorizacoes)["contatos_por_escopo"]["acerto"]
        self.assertEqual(ambiguo["estado"], "pendente_contatos_ambiguos")
        self.assertIsNone(ambiguo["contato_selecionado_ref"])

    def test_homonimo_nao_entra_no_contrato(self):
        entrada = fixture()
        entrada["vinculos_contatos"][0]["nome"] = "Nome repetido"
        with self.assertRaisesRegex(ContratoPlanejamentoInvalido, "vinculo_contato_invalido"):
            planejar_acerto_contrato(entrada)

    def test_papeis_fechados_e_vinculo_exato(self):
        entrada = fixture()
        entrada["vinculos_contatos"][0]["papel"] = "dono"
        with self.assertRaisesRegex(ContratoPlanejamentoInvalido, "papel_contato_invalido"):
            planejar_acerto_contrato(entrada)
        entrada = fixture()
        entrada["confinamento"]["confinamento_ref"] = "outro-conf"
        with self.assertRaisesRegex(ContratoPlanejamentoInvalido, "vinculo_operacao_confinamento_divergente"):
            planejar_acerto_contrato(entrada)

    def test_refs_duplicadas_e_orfas_falham_fechado(self):
        entrada = fixture()
        entrada["abates"].append(copy.deepcopy(entrada["abates"][0]))
        with self.assertRaisesRegex(ContratoPlanejamentoInvalido, "abate_ref_duplicada"):
            planejar_acerto_contrato(entrada)
        entrada = fixture()
        entrada["acertos"][0]["abate_ref"] = "abate-ausente"
        with self.assertRaisesRegex(ContratoPlanejamentoInvalido, "acerto_abate_inexistente"):
            planejar_acerto_contrato(entrada)
        entrada = fixture()
        entrada["evidencias_recebimento"] = [{
            "evidencia_ref": "ev-orfa", "operacao_ref": "op-fixture",
            "abate_ref": "abate-1", "acerto_ref": "acerto-ausente",
            "tipo": "comprovante", "vinculo_explicito": True,
        }]
        with self.assertRaisesRegex(ContratoPlanejamentoInvalido, "evidencia_recebimento_sem_acerto_exato"):
            planejar_acerto_contrato(entrada)

    def test_documento_sem_referencias_permanece_pendente(self):
        entrada = fixture()
        entrada["documentos"][0]["negocio"] = {}
        entrada["documentos"][0]["termos_aprovados"] = {}
        entrada["documentos"][0]["etapas"]["conferido"] = "confirmado"
        documento = planejar_acerto_contrato(entrada)["documentos"][0]
        self.assertEqual(documento["estado"], "pendente_referencia_ou_divergencia")
        self.assertEqual(
            documento["pendencias_referencia"],
            ["negocio_referencia_ausente", "termos_aprovados_ausentes"],
        )
        self.assertTrue(documento["nao_classificado_como_conferido_automaticamente"])

    def test_referencias_sem_campo_util_continuam_pendentes(self):
        for negocio, termos in [
            ({"irrelevante": 1}, {"irrelevante": 1}),
            ({"quantidade": None}, {"partes": ""}),
            ({"pagamento": "   "}, {"foro": "\t"}),
            ({"pagamento": ["  ", "\t"]}, {"partes": ["   "]}),
            ({"pagamento": {"detalhe": "  "}}, {"partes": {"item": ["\n"]}}),
            ({"pagamento": []}, {"partes": {}}),
        ]:
            with self.subTest(negocio=negocio, termos=termos):
                entrada = fixture()
                entrada["documentos"][0]["negocio"] = negocio
                entrada["documentos"][0]["termos_aprovados"] = termos
                documento = planejar_acerto_contrato(entrada)["documentos"][0]
                self.assertIn("negocio_referencia_ausente", documento["pendencias_referencia"])
                self.assertIn("termos_aprovados_ausentes", documento["pendencias_referencia"])
        documento_util = planejar_acerto_contrato(fixture())["documentos"][0]
        self.assertNotIn("negocio_referencia_ausente", documento_util["pendencias_referencia"])
        self.assertNotIn("termos_aprovados_ausentes", documento_util["pendencias_referencia"])

    def test_divergencia_de_negocio_e_aditivo_permanece_pendente(self):
        entrada = fixture()
        documento = entrada["documentos"][0]
        documento["tipo"] = "aditivo"
        documento["extraido"]["quantidade"] = 70
        documento["extraido"]["partes"] = ["Parte divergente"]
        saida = planejar_acerto_contrato(entrada)["documentos"][0]
        self.assertEqual(saida["estado"], "pendente_referencia_ou_divergencia")
        self.assertEqual(saida["divergencias_negocio"], [{"campo": "quantidade", "tipo": "divergente"}])
        self.assertEqual(saida["divergencias_termos"], [{"campo": "partes", "tipo": "divergente"}])

    def test_cobertura_e_somas_sao_do_snapshot(self):
        cobertura = planejar_acerto_contrato(fixture())["cobertura"]
        self.assertEqual(cobertura["abates_total"], 2)
        self.assertEqual(cobertura["abates_com_acerto"], 1)
        self.assertEqual(cobertura["abates_sem_acerto"], 1)
        self.assertEqual(cobertura["animais_qtd_soma_do_snapshot"], 75)
        self.assertEqual(cobertura["custos_soma_candidatos_do_snapshot"], "125.5")
        self.assertEqual(cobertura["descontos_soma_candidatos_do_snapshot"], "25")
        self.assertEqual(cobertura["custos_soma_consolidavel"], "125.5")
        self.assertTrue(cobertura["nao_atesta_completude"])

    def test_hash_declarado_deduplica_sem_autenticar(self):
        entrada = fixture()
        duplicado = copy.deepcopy(entrada["documentos"][0])
        duplicado["documento_ref"] = "doc-2"
        entrada["documentos"].append(duplicado)
        documentos = planejar_acerto_contrato(entrada)["documentos"]
        self.assertTrue(documentos[0]["duplicado_no_snapshot"])
        self.assertTrue(documentos[1]["duplicado_no_snapshot"])
        self.assertTrue(documentos[1]["hash_nao_autentica_conteudo"])
        self.assertEqual(documentos[0]["documentos_refs_mesmo_conteudo"], ["doc-1", "doc-2"])
        self.assertIn("conteudo_duplicado_no_snapshot", documentos[1]["pendencias_referencia"])
        plano_a = planejar_acerto_contrato(entrada)
        entrada["documentos"].reverse()
        plano_b = planejar_acerto_contrato(entrada)
        self.assertEqual(plano_a, plano_b)

    def test_data_iso_real_e_hash_valido_sao_obrigatorios(self):
        for data in ["31/01/2026", "2026-02-30", "2026-1-2"]:
            entrada = fixture()
            entrada["abates"][0]["periodo_inicio"] = data
            with self.subTest(data=data):
                with self.assertRaisesRegex(ContratoPlanejamentoInvalido, "periodo_inicio_invalido"):
                    planejar_acerto_contrato(entrada)
        entrada = fixture()
        entrada["documentos"][0]["conteudo_sha256"] = "nao-e-hash"
        with self.assertRaisesRegex(ContratoPlanejamentoInvalido, "conteudo_sha256_invalido"):
            planejar_acerto_contrato(entrada)

    def test_etapas_sao_independentes_e_nao_inferidas(self):
        entrada = fixture()
        etapas = entrada["documentos"][0]["etapas"]
        etapas.update({"conferido": "desconhecido", "aprovado": "confirmado", "assinatura": "confirmado", "envio": "confirmado"})
        documento = planejar_acerto_contrato(entrada)["documentos"][0]
        self.assertEqual(documento["etapas_declaradas"], etapas)
        self.assertIn("aprovacao_sem_conferencia_confirmada", documento["alertas_etapas"])

    def test_shape_tipos_limites_e_schema_invalidos(self):
        casos = []
        entrada = fixture(); entrada["extra"] = True; casos.append(entrada)
        entrada = fixture(); entrada["schema_version"] = "outra"; casos.append(entrada)
        entrada = fixture(); entrada["vinculos_contatos"][0]["principal"] = 1; casos.append(entrada)
        entrada = fixture(); entrada["acertos"][0]["custos"][0]["valor"] = True; casos.append(entrada)
        for caso in casos:
            with self.subTest(caso=casos.index(caso)):
                with self.assertRaises(ContratoPlanejamentoInvalido):
                    planejar_acerto_contrato(caso)
        with self.assertRaises(ContratoPlanejamentoInvalido):
            planejar_acerto_contrato(fixture(), [{
                "contato_ref": "ct-adm", "escopos": ["acerto", "acerto"],
            }])

    def test_autorizacao_e_envelope_separado_e_nao_credencial(self):
        plano = planejar_acerto_contrato(fixture())
        self.assertEqual(
            plano["autorizacoes_leitura"]["natureza"],
            "declaradas_pelo_chamador_nao_verificadas",
        )
        self.assertTrue(plano["autorizacoes_leitura"]["nao_sao_credencial"])
        entrada = fixture()
        entrada["autorizacoes_leitura"] = copy.deepcopy(AUTORIZACOES)
        with self.assertRaisesRegex(ContratoPlanejamentoInvalido, "entrada_invalido"):
            planejar_acerto_contrato(entrada)

    def test_conferido_sem_recebido_gera_alerta(self):
        entrada = fixture()
        etapas = entrada["acertos"][0]["etapas"]
        etapas["recebido"] = "pendente"
        etapas["conferido"] = "confirmado"
        acerto = planejar_acerto_contrato(entrada)["abates"][0]["acertos"][0]
        self.assertIn("conferencia_sem_recebimento_confirmado", acerto["alertas_etapas"])

    def test_zero_animais_e_invalido(self):
        entrada = fixture()
        entrada["abates"][0]["animais_qtd"] = 0
        with self.assertRaisesRegex(ContratoPlanejamentoInvalido, "animais_qtd_invalida"):
            planejar_acerto_contrato(entrada)

    def test_multiplos_acertos_nao_produzem_soma_consolidavel(self):
        entrada = fixture()
        outro = copy.deepcopy(entrada["acertos"][0])
        outro["acerto_ref"] = "acerto-2"
        entrada["acertos"].append(outro)
        cobertura = planejar_acerto_contrato(entrada)["cobertura"]
        self.assertEqual(cobertura["financeiro_estado"], "indisponivel_para_consolidacao_por_ambiguidade")
        self.assertIsNone(cobertura["custos_soma_consolidavel"])
        self.assertEqual(cobertura["custos_soma_candidatos_do_snapshot"], "251")


if __name__ == "__main__":
    unittest.main()
