import unittest

from tools.materializar_revisoes_staging import (
    EscritorRevisao,
    associar_investigacoes_concluidas,
    executar_investigado,
    fingerprint_retrato_candidato,
    metadados_grupo_staging,
    listar_investigacoes_materializaveis,
    montar_registros,
    planejar,
)


def candidato(**mudancas):
    base = {
        "id": "11111111-1111-1111-1111-111111111111",
        "estado": "em_revisao", "prioridade": "alta", "campos_faltantes": [],
        "codigo_fonte": "NEG-26-900", "chave_rastreio": "fonte-900",
        "nome": "Fornecedor teste", "contexto": "Compras Fazenda",
        "quantidade": 10, "peso_total_kg": "3000", "preco_arroba": "300",
        "valor_total": "30000", "data_base": "2026-08-01",
        "dados_origem": {"confirmado_na_planilha": True},
    }
    return {**base, **mudancas}


class MaterializarRevisoesStagingTest(unittest.TestCase):
    def test_planeja_tripla_sem_operacao(self):
        plano = planejar([candidato()], [], [], [], [])
        self.assertEqual(plano["resumo"]["revisoes_planejadas"], 1)
        self.assertEqual(plano["resumo"]["tabelas_operacionais_alteradas"], 0)
        registros = plano["registros"][0]
        self.assertEqual(set(registros), {"operation_drafts", "pending_actions", "eventos"})

    def test_preserva_duplicidade_e_referencia_operacional(self):
        duplicados = [candidato(), candidato(id="22222222-2222-2222-2222-222222222222")]
        plano = planejar(duplicados, [], [], [], [])
        self.assertEqual(plano["resumo"]["revisoes_planejadas"], 1)
        draft = plano["registros"][0]["operation_drafts"]
        self.assertEqual(len(draft["dados_extraidos"]["versoes_revisao"]), 1)
        self.assertNotIn(
            "staging_candidato_id",
            draft["dados_extraidos"]["versoes_revisao"][0],
        )
        self.assertEqual(plano["registros"][0]["pending_actions"]["id"], plano["registros"][0]["operation_drafts"]["pending_action_id"])
        operacional = planejar([candidato()], [{"id": "o1", "codigo": "NEG-26-900"}], [], [], [])
        self.assertEqual(operacional["resumo"]["revisoes_planejadas"], 0)

    def test_duplicidades_com_snapshots_distintos_formam_uma_revisao_com_versoes(self):
        duplicados = [
            candidato(id="z-candidato", peso_total_kg="3000"),
            candidato(id="a-candidato", peso_total_kg="3100", valor_total="31000"),
        ]
        plano = planejar(duplicados, [], [], [], [])
        self.assertEqual(plano["resumo"]["revisoes_planejadas"], 1)
        draft = plano["registros"][0]["operation_drafts"]
        versoes = draft["dados_extraidos"]["versoes_revisao"]
        self.assertEqual(len(versoes), 2)
        self.assertEqual(
            {v["peso_total_kg"] for v in versoes}, {"3000", "3100"},
        )
        self.assertEqual(len({draft["id"]}), 1)

    def test_versoes_humanas_ignoram_ids_timestamps_e_fingerprints_tecnicos(self):
        duplicados = [
            candidato(
                id="z-candidato", atualizado_em="2026-08-29T12:00:00+00:00",
            ),
            candidato(
                id="a-candidato", atualizado_em="2026-08-29T10:00:00+00:00",
            ),
        ]
        draft = planejar(duplicados, [], [], [], [])["registros"][0]["operation_drafts"]
        versoes = draft["dados_extraidos"]["versoes_revisao"]

        self.assertEqual(len(versoes), 1)
        for campo in (
            "staging_candidato_id", "staging_candidato_ids",
            "staging_candidato_atualizado_em",
            "staging_candidatos_atualizados_em", "fingerprint_base",
            "fingerprint_grupo",
        ):
            self.assertNotIn(campo, versoes[0])
        self.assertEqual(
            draft["inferencias"]["staging_candidato_ids"],
            ["a-candidato", "z-candidato"],
        )

    def test_grupo_duplicado_preserva_ids_timestamps_e_fingerprint_ordenados(self):
        duplicados = [
            candidato(
                id="z-candidato", peso_total_kg="3000",
                atualizado_em="2026-08-29T12:00:00+00:00",
            ),
            candidato(
                id="a-candidato", peso_total_kg="3100", valor_total="31000",
                atualizado_em="2026-08-29T10:00:00+00:00",
            ),
        ]
        registros = planejar(duplicados, [], [], [], [])["registros"][0]
        registros_em_ordem_inversa = planejar(
            list(reversed(duplicados)), [], [], [], [],
        )["registros"][0]
        esperado = metadados_grupo_staging(duplicados)

        self.assertEqual(
            registros["operation_drafts"]["dados_extraidos"]["staging_candidato_id"],
            "a-candidato",
        )
        self.assertEqual(esperado["staging_candidato_ids"], ["a-candidato", "z-candidato"])
        self.assertEqual(
            esperado["staging_candidatos_atualizados_em"],
            {
                "a-candidato": "2026-08-29T10:00:00+00:00",
                "z-candidato": "2026-08-29T12:00:00+00:00",
            },
        )
        for registro in (
            registros["operation_drafts"]["dados_extraidos"],
            registros["operation_drafts"]["inferencias"],
            registros["pending_actions"]["payload"],
            registros["pending_actions"]["resultado"],
            registros["eventos"]["dados"],
        ):
            self.assertEqual(registro["staging_candidato_ids"], esperado["staging_candidato_ids"])
            self.assertEqual(
                registro["staging_candidatos_atualizados_em"],
                esperado["staging_candidatos_atualizados_em"],
            )
            self.assertEqual(registro["fingerprint_grupo"], esperado["fingerprint_grupo"])
        self.assertEqual(
            registros["operation_drafts"]["dados_extraidos"]["fingerprint_grupo"],
            registros_em_ordem_inversa["operation_drafts"]["dados_extraidos"]["fingerprint_grupo"],
        )

    def test_prioridade_alta_com_faltantes_permanece_na_revisao(self):
        plano = planejar([candidato(peso_total_kg=None, campos_faltantes=["peso_total_kg", "pagamento"])], [], [], [], [])
        self.assertEqual(plano["resumo"]["revisoes_planejadas"], 1)
        draft = plano["registros"][0]["operation_drafts"]
        self.assertEqual(draft["campos_pendentes"][:2], ["peso_total_kg", "pagamento"])
        self.assertIsNone(draft["dados_extraidos"]["peso_total_kg"])

    def test_ids_sao_deterministicos_e_pendente_preserva_confirmacao(self):
        primeiro = montar_registros(candidato())
        segundo = montar_registros(candidato())
        self.assertEqual(primeiro, segundo)
        self.assertIn(
            "confirmar vínculo com negócio operacional existente ou novo",
            primeiro["operation_drafts"]["campos_pendentes"],
        )
        self.assertFalse(primeiro["eventos"]["dados"]["promovido_para_operacional"])

    def test_fingerprint_do_retrato_e_estavel_sem_expor_origem_bruta(self):
        original = candidato(
            atualizado_em="2026-08-29T10:00:00+00:00",
            dados_origem={
                "confirmado_na_planilha": True,
                "xml_bruto": "<cpf>conteudo-sensivel</cpf>",
            },
        )
        reexecucao = candidato(
            atualizado_em="2026-08-29T11:00:00+00:00",
            dados_origem={
                "xml_bruto": "<cpf>outro-conteudo-sensivel</cpf>",
                "confirmado_na_planilha": True,
            },
        )
        fingerprint = fingerprint_retrato_candidato(original)

        self.assertEqual(fingerprint, fingerprint_retrato_candidato(reexecucao))
        self.assertEqual(len(fingerprint), 64)
        self.assertTrue(all(caractere in "0123456789abcdef" for caractere in fingerprint))
        self.assertNotIn("conteudo-sensivel", fingerprint)
        self.assertNotIn("Fornecedor teste", fingerprint)
        self.assertNotEqual(
            fingerprint,
            fingerprint_retrato_candidato(candidato(valor_total="30001")),
        )

    def test_fingerprint_e_snapshot_de_staging_se_propagam_na_tripla(self):
        item = candidato(atualizado_em="2026-08-29T10:00:00+00:00")
        registros = montar_registros(item)
        fingerprint = metadados_grupo_staging([item])["fingerprint_grupo"]
        draft = registros["operation_drafts"]
        action = registros["pending_actions"]
        evento = registros["eventos"]

        self.assertEqual(draft["dados_extraidos"]["fingerprint_base"], fingerprint)
        self.assertEqual(draft["inferencias"]["fingerprint_base"], fingerprint)
        self.assertEqual(action["payload"]["fingerprint_base"], fingerprint)
        self.assertEqual(action["resultado"]["fingerprint_base"], fingerprint)
        self.assertEqual(evento["dados"]["fingerprint_base"], fingerprint)
        for registro in (draft["dados_extraidos"], draft["inferencias"], action["payload"], action["resultado"], evento["dados"]):
            self.assertEqual(
                registro["staging_candidato_atualizado_em"],
                "2026-08-29T10:00:00+00:00",
            )

    def test_conjunto_parcial_e_completado_sem_duplicar(self):
        registros = montar_registros(candidato())
        plano = planejar(
            [candidato()], [], [registros["operation_drafts"]], [], [],
        )
        self.assertEqual(plano["resumo"]["revisoes_planejadas"], 1)
        self.assertEqual(
            plano["resumo"]["ignorados_por_motivo"]["conjunto_parcial_a_completar"],
            1,
        )

        completo = planejar(
            [candidato()], [], [registros["operation_drafts"]],
            [registros["pending_actions"]], [registros["eventos"]],
        )
        self.assertEqual(completo["resumo"]["revisoes_planejadas"], 0)

    def test_escritor_bloqueia_operacional(self):
        escritor = EscritorRevisao("https://exemplo.invalid", "segredo")
        with self.assertRaisesRegex(ValueError, "escrita não permitida"):
            escritor.inserir("compras", {})

    def test_associa_somente_investigacao_concluida_com_grupo_e_snapshot_exatos(self):
        plano = planejar([candidato(atualizado_em="2026-08-29T10:00:00Z")], [], [], [], [])
        inferencias = plano["registros"][0]["operation_drafts"]["inferencias"]
        investigacao = {
            "id": "99999999-9999-4999-8999-999999999999",
            "estado_execucao": "concluida",
            "anexado_em": None,
            "source_draft_id": None,
            "negocio_candidato_ids": inferencias["staging_candidato_ids"],
            "fingerprint_base": inferencias["fingerprint_grupo"],
        }
        associar_investigacoes_concluidas(plano, [investigacao])
        self.assertEqual(
            plano["registros"][0]["investigacao_id"], investigacao["id"]
        )

        outro = planejar([candidato(atualizado_em="2026-08-29T10:00:00Z")], [], [], [], [])
        associar_investigacoes_concluidas(
            outro, [{**investigacao, "fingerprint_base": "0" * 64}]
        )
        self.assertNotIn("investigacao_id", outro["registros"][0])

    def test_execucao_investigada_prevalida_todo_lote_e_usa_uma_rpc_por_revisao(self):
        class EscritorFalso:
            def __init__(self):
                self.rpcs = []
                self.posts = []

            def materializar_investigada(self, investigacao_id, conjunto):
                self.rpcs.append((investigacao_id, conjunto))
                return {"materializada": True}

            def inserir(self, tabela, payload):
                self.posts.append((tabela, payload))

        plano = planejar([candidato()], [], [], [], [])
        escritor = EscritorFalso()
        with self.assertRaisesRegex(ValueError, "investigacao_concluida_obrigatoria"):
            executar_investigado(plano, escritor, 1)
        self.assertEqual(escritor.rpcs, [])
        self.assertEqual(escritor.posts, [])

        plano["registros"][0]["investigacao_id"] = (
            "99999999-9999-4999-8999-999999999999"
        )
        resultado = executar_investigado(plano, escritor, 1)
        self.assertEqual(resultado["revisoes_criadas"], 1)
        self.assertEqual(resultado["revisoes_idempotentes"], 0)
        self.assertEqual(resultado["revisoes_nao_materializadas"], 0)
        self.assertEqual(len(escritor.rpcs), 1)
        self.assertEqual(escritor.posts, [])

        escritor.materializar_investigada = lambda *_: {
            "materializada": False,
            "motivo": "investigacao_ja_materializada",
        }
        repetido = executar_investigado(plano, escritor, 1)
        self.assertEqual(repetido["revisoes_criadas"], 0)
        self.assertEqual(repetido["revisoes_idempotentes"], 1)
        self.assertEqual(repetido["revisoes_nao_materializadas"], 0)

        escritor.materializar_investigada = lambda *_: {
            "materializada": False, "motivo": "investigacao_obsoleta",
        }
        conflito = executar_investigado(plano, escritor, 1)
        self.assertEqual(conflito["revisoes_criadas"], 0)
        self.assertEqual(conflito["revisoes_nao_materializadas"], 1)

        escritor.materializar_investigada = lambda *_: {"motivo": "invalido"}
        with self.assertRaisesRegex(RuntimeError, "não informou"):
            executar_investigado(plano, escritor, 1)

    def test_leitura_protegida_usa_view_fechada_sem_ampliar_allowlist_do_snapshot(self):
        class Resposta:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b"[]"

        class LeitorFalso:
            url = "https://exemplo.invalid"
            chave = "segredo"
            timeout = 5

            def __init__(self):
                self.requisicoes = []

            def opener(self, requisicao, timeout):
                self.requisicoes.append((requisicao, timeout))
                return Resposta()

            def listar(self, tabela):
                raise AssertionError("a tabela-base privada não pode entrar na allowlist")

        leitor = LeitorFalso()
        self.assertEqual(listar_investigacoes_materializaveis(leitor), [])
        self.assertEqual(len(leitor.requisicoes), 1)
        requisicao, timeout = leitor.requisicoes[0]
        self.assertEqual(timeout, 5)
        self.assertEqual(requisicao.method, "GET")
        self.assertIn("/rest/v1/v_investigacoes_revisao_materializacao?", requisicao.full_url)
        self.assertNotIn("/rest/v1/investigacoes_revisao?", requisicao.full_url)


if __name__ == "__main__":
    unittest.main()
