import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRACOES = (
    ROOT / "supabase/migrations/202608290001_investigacoes_revisao.sql",
    ROOT / "supabase/migrations/202608290002_ativar_mediador_investigacoes.sql",
)
ROLLBACK_ATIVACAO = (
    ROOT / "supabase/rollbacks/202608290002_desativar_mediador_investigacoes.sql"
)


class MigracaoInvestigacoesRevisaoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = "\n".join(
            caminho.read_text(encoding="utf-8") for caminho in MIGRACOES
        )
        cls.normalizado = " ".join(cls.sql.lower().split())

    def test_cria_control_plane_com_contratos_completos(self):
        self.assertIn(
            "create or replace function public.investigacao_json_sanitizado",
            self.normalizado,
        )
        self.assertIn(
            "create or replace function public.investigacao_texto_sanitizado",
            self.normalizado,
        )
        tabelas = (
            "investigacoes_revisao", "investigacao_tarefas",
            "investigacao_evidencias", "investigacao_alternativas",
            "investigacao_alternativa_evidencias", "investigacao_pendencias",
            "investigacao_eventos", "investigacao_entregas",
        )
        for tabela in tabelas:
            self.assertIn(f"create table if not exists public.{tabela}", self.normalizado)
            self.assertIn(f"alter table public.{tabela} enable row level security", self.normalizado)
        for coluna in (
            "fingerprint_base", "policy_version", "gatilho_tipo", "prioridade",
            "plano_hash", "plano_canonico", "plano_tarefas", "plano_item_ref",
            "consulta_ref", "consulta_schema_version", "consulta_spec",
            "consulta_hash", "adaptador_version", "estado_cobertura", "tentativas",
            "proxima_execucao_em", "lease_token", "lease_expira_em", "linhagem",
            "fencing_token", "tarefa_lease_token", "tarefa_fencing_token",
            "chave_natural_hash", "fatos_normalizados", "campos_snapshot",
            "confianca_campos", "justificativa_sanitizada",
            "provas_campos", "provas_campos_canonico", "provas_campos_hash",
            "regra_confianca_version",
        ):
            self.assertIn(coluna, self.normalizado)
        self.assertGreaterEqual(self.normalizado.count("investigacao_json_sanitizado("), 4)
        self.assertGreaterEqual(
            self.normalizado.count("check (public.investigacao_texto_sanitizado("),
            5,
        )
        self.assertGreaterEqual(
            self.normalizado.count(
                "check (public.investigacao_texto_publico_sanitizado("
            ),
            4,
        )
        for chave in (
            "mensagem_bruta", "origem_mensagem_id", "origem_conversa_id",
            "authorization", "service_role_key",
        ):
            self.assertIn(f"'{chave}'", self.normalizado)
        for padrao in ("bearer", "[[:alnum:]._%+-]+@", "[0-9]{11,14}"):
            self.assertIn(padrao, self.normalizado)
        self.assertNotIn("jsonb_object_length", self.normalizado)
        self.assertIn("investigacao_jsonb_objeto_tamanho", self.normalizado)
        self.assertIn("extensions.digest", self.normalizado)
        self.assertIn(
            "to_regprocedure('extensions.digest(bytea,text)') is null",
            self.normalizado,
        )
        self.assertIn("investigacao_uuid_texto_seguro", self.normalizado)
        self.assertIn("grant usage on schema extensions to service_role", self.normalizado)
        self.assertIn(
            "grant execute on function extensions.digest(bytea, text) to service_role",
            self.normalizado,
        )
        self.assertIn(
            "has_schema_privilege('service_role', 'extensions', 'usage')",
            self.normalizado,
        )
        self.assertIn("v_chave !~ '^[a-za-z0-9_. -]+$'", self.normalizado)
        sanitizador = self.normalizado.split(
            "create or replace function public.investigacao_json_sanitizado", 1
        )[1].split(
            "create or replace function public.investigacao_json_publico_sanitizado", 1
        )[0]
        self.assertIn("jsonb_typeof(p_payload) = 'number'", sanitizador)
        self.assertIn(
            "v_texto ~ '(^|[^0-9])[0-9]{44}([^0-9]|$)'",
            sanitizador,
        )
        self.assertIn("^-?(?:[0-9]{11,14}|[0-9]{44})$", sanitizador)

    def test_plano_da_rodada_e_imutavel_com_uma_unica_sintese(self):
        self.assertIn(
            "create or replace function public.investigacao_plano_tarefas_valido",
            self.normalizado,
        )
        self.assertIn(
            "create or replace function public.validar_tarefa_no_plano_investigacao",
            self.normalizado,
        )
        self.assertIn(
            "create unique index if not exists investigacao_tarefas_sintese_unica_idx",
            self.normalizado,
        )
        self.assertIn("where adaptador = 'sintese'", self.normalizado)
        self.assertIn(
            "before insert on public.investigacao_tarefas",
            self.normalizado,
        )
        self.assertIn(
            "a tarefa não pertence ao plano imutável da investigação",
            self.normalizado,
        )
        self.assertIn("public.investigacao_plano_materializado", self.normalizado)
        self.assertIn("public.investigacao_cobertura_sintese", self.normalizado)
        self.assertIn("a síntese exige todas as tarefas do plano imutável", self.normalizado)
        self.assertIn(
            "a cobertura da síntese deve refletir todas as fontes planejadas",
            self.normalizado,
        )
        manifesto = self.normalizado.split(
            "create or replace function public.investigacao_plano_tarefas_valido", 1
        )[1].split(
            "create table if not exists public.investigacoes_revisao", 1
        )[0]
        self.assertIn("investigacao_jsonb_objeto_tamanho(v_item) <> 8", manifesto)
        for campo in (
            "consulta_schema_version", "consulta_spec", "consulta_canonico",
        ):
            self.assertIn(f"'{campo}'", manifesto)
        for campo in (
            "plano_item_ref", "adaptador", "adaptador_version",
            "consulta_ref", "consulta_schema_version", "consulta_canonico",
            "consulta_hash",
        ):
            self.assertIn(
                f"jsonb_typeof(v_item -> '{campo}') is distinct from 'string'",
                manifesto,
            )
        self.assertIn("(v_item ->> 'adaptador' in (", manifesto)
        self.assertIn(")) is not true", manifesto)
        self.assertIn(
            "'qref_' || left(v_item ->> 'consulta_hash', 32)", manifesto,
        )
        tabela = self.normalizado.split(
            "create table if not exists public.investigacoes_revisao", 1
        )[1].split(
            "create table if not exists public.investigacao_tarefas", 1
        )[0]
        self.assertIn(
            "plano_canonico::jsonb = jsonb_build_object( 'tarefas', plano_tarefas, 'campos_obrigatorios', to_jsonb(campos_obrigatorios), 'policy_schema_hash', policy_schema_hash )",
            tabela,
        )
        self.assertIn(
            "encode(extensions.digest(convert_to(plano_canonico, 'utf8'), 'sha256'), 'hex') = plano_hash",
            tabela,
        )
        protetor = self.normalizado.split(
            "create or replace function public.proteger_origem_investigacao_revisao", 1
        )[1].split(
            "drop trigger if exists investigacoes_revisao_origem_imutavel", 1
        )[0]
        for campo in (
            "plano_hash", "plano_canonico", "plano_tarefas",
            "policy_version", "campos_obrigatorios",
        ):
            self.assertIn(f"new.{campo} is distinct from old.{campo}", protetor)

    def test_confianca_e_recomputavel_e_fechada_por_allowlist(self):
        funcao = self.normalizado.split(
            "create or replace function public.investigacao_confianca_campos_valida", 1
        )[1].split(
            "create or replace function public.investigacao_plano_tarefas_valido", 1
        )[0]
        for campo in (
            "ruleset_hash", "inputs_hash", "inputs_contexto", "inputs_canonico",
            "linhagens", "penalidades", "caps",
        ):
            self.assertIn(f"'{campo}'", funcao)
        self.assertIn(
            "24982e6a934449e0881331ad16b126382fbbf62cf91295673a147a56c59107b7",
            funcao,
        )
        self.assertIn("confianca-deterministica-v2", funcao)
        self.assertIn("(select count(*) from jsonb_object_keys(v_avaliacao)) <> 13", funcao)
        self.assertIn(
            "encode(extensions.digest(convert_to(v_canonico, 'utf8'), 'sha256'), 'hex')",
            funcao,
        )
        self.assertIn("is distinct from v_avaliacao ->> 'inputs_hash'", funcao)
        self.assertIn("v_confianca > v_limite", funcao)
        self.assertIn("^lin_[0-9a-f]{32}$", funcao)
        for limite in (
            "universo_nao_comprovado", "unicidade_nao_comprovada",
            "coerencia_nao_comprovada", "extracao_nao_confirmada",
            "llm_somente_pista", "ambiguidade_no_campo",
            "grupo_correlacao_nao_verificado", "incoerencia_verificada",
            "divergencia_central",
        ):
            self.assertIn(f"'{limite}'", funcao)

    def test_sanitizacao_publica_e_mais_estrita_que_controle_interno(self):
        self.assertIn(
            "create or replace function public.investigacao_json_publico_sanitizado",
            self.normalizado,
        )
        estrita = self.normalizado.split(
            "create or replace function public.investigacao_json_publico_sanitizado", 1
        )[1].split(
            "create or replace function public.investigacao_texto_sanitizado", 1
        )[0]
        self.assertIn("right(v_chave_normalizada, 3) = '_id'", estrita)
        self.assertIn("v_chave_normalizada like 'origem\\_%' escape '\\'", estrita)
        self.assertIn("[0-9a-f]{8}-[0-9a-f]{4}", estrita)
        self.assertIn("^-?(?:[0-9]{11,14}|[0-9]{44})$", estrita)
        for tabela_campo in (
            "fatos_normalizados", "campos_snapshot",
        ):
            self.assertRegex(
                self.normalizado,
                rf"{tabela_campo}[^;]+investigacao_json_publico_sanitizado\({tabela_campo}\)",
            )

    def test_estados_execucao_resultado_e_cobertura_sao_separados(self):
        for estado in (
            "pendente", "em_execucao", "aguardando_retentativa", "concluida",
            "cancelada", "obsoleta",
        ):
            self.assertIn(f"'{estado}'", self.normalizado)
        for resultado in (
            "alternativa_unica", "alternativas_multiplas", "divergente",
            "evidencia_insuficiente", "cobertura_incompleta",
        ):
            self.assertIn(f"'{resultado}'", self.normalizado)
        for cobertura in (
            "completa", "vazio_com_cobertura", "indisponivel",
            "reautenticacao_necessaria", "erro_permanente",
        ):
            self.assertIn(f"'{cobertura}'", self.normalizado)

    def test_evidencia_tem_fk_composta_e_alternativa_liga_pros_e_contras(self):
        self.assertIn(
            "foreign key (investigacao_id, tarefa_id) references public.investigacao_tarefas(investigacao_id, id)",
            self.normalizado,
        )
        self.assertIn("papel in ('favoravel', 'contraria')", self.normalizado)
        self.assertIn("investigacao_alternativa_evidencias", self.normalizado)
        trecho = re.search(
            r"create table if not exists public\.investigacao_evidencias \((.*?)\n\);",
            self.sql, re.S | re.I,
        ).group(1).lower()
        self.assertNotRegex(trecho, r"\b(xml|ofx|mensagem|conversa|documento)_brut[oa]\b")

    def test_bases_ficam_privadas_e_views_sanitizadas_sao_a_leitura_humana(self):
        for tabela in (
            "investigacoes_revisao", "investigacao_tarefas", "investigacao_evidencias",
            "investigacao_alternativas", "investigacao_alternativa_evidencias",
            "investigacao_pendencias", "investigacao_eventos", "investigacao_entregas",
        ):
            self.assertIn(
                f"revoke all on public.{tabela} from public, anon, authenticated, service_role",
                self.normalizado,
            )
            self.assertNotRegex(
                self.normalizado,
                rf"grant\s+select\s+on\s+public\.{tabela}\s+to\s+authenticated",
            )
        for view in (
            "v_investigacoes_revisao", "v_investigacao_alternativas",
            "v_investigacao_evidencias", "v_investigacao_pendencias",
        ):
            self.assertIn(f"grant select on public.{view} to authenticated", self.normalizado)
        self.assertIn(
            "has_table_privilege('authenticated', 'public.' || v_tabela, 'select')",
            self.normalizado,
        )
        self.assertIn(
            "has_table_privilege('service_role', 'public.investigacao_eventos', 'update')",
            self.normalizado,
        )
        views = self.sql.split("CREATE OR REPLACE VIEW public.v_investigacoes_revisao", 1)[1]
        views = views.split(
            "CREATE OR REPLACE FUNCTION public.decidir_pendencia_investigacao", 1
        )[0].lower()
        self.assertNotIn("origem_conversa_id", views)
        self.assertNotIn("origem_mensagem_id", views)
        self.assertNotIn("referencia_opaca", views)
        self.assertNotIn("chave_natural_hash", views)
        self.assertNotIn("confianca_campos", views)
        self.assertIn("as campos_presentes", views)
        self.assertNotIn("alternativa.campos_snapshot,", views)
        self.assertIn("source_draft_id", views)
        self.assertGreaterEqual(
            views.count("tarefa.estado_execucao = 'concluida'"), 3,
        )
        self.assertIn(
            "tarefa.resultado_fencing_token = alternativa.tarefa_fencing_token",
            views,
        )
        self.assertIn(
            "tarefa.resultado_fencing_token = evidencia.tarefa_fencing_token",
            views,
        )
        self.assertIn(
            "tarefa.resultado_fencing_token = pendencia.tarefa_fencing_token",
            views,
        )

    def test_claim_e_fencing_impedem_dois_workers(self):
        self.assertIn("create or replace function public.assumir_tarefa_investigacao", self.normalizado)
        self.assertIn("for update of tarefa_candidata skip locked", self.normalizado)
        self.assertIn("lease_token = gen_random_uuid()", self.normalizado)
        self.assertIn("fencing_token = tarefa.fencing_token + 1", self.normalizado)
        self.assertIn("tarefa_candidata.estado_execucao = 'em_execucao'", self.normalizado)
        self.assertIn("tarefa_candidata.adaptador <> 'sintese'", self.normalizado)
        self.assertIn("order by (tarefa_candidata.adaptador = 'sintese')", self.normalizado)
        self.assertIn(
            "join public.investigacoes_revisao investigacao_pai",
            self.normalizado,
        )
        self.assertIn(
            "investigacao_pai.estado_execucao in ( 'pendente', 'em_execucao', 'aguardando_retentativa' )",
            self.normalizado,
        )
        self.assertIn("create or replace function public.concluir_tarefa_investigacao", self.normalizado)
        self.assertIn(
            "create or replace function public.publicar_resultado_tarefa_investigacao",
            self.normalizado,
        )
        publicador = self.normalizado.split(
            "create or replace function public.publicar_resultado_tarefa_investigacao", 1
        )[1].split(
            "create or replace function public.concluir_tarefa_investigacao", 1
        )[0]
        self.assertIn("for update", publicador)
        self.assertIn("v_tarefa.lease_token is distinct from p_lease_token", publicador)
        self.assertIn("v_tarefa.fencing_token is distinct from p_fencing_token", publicador)
        self.assertIn("v_tarefa.lease_expira_em <= clock_timestamp()", publicador)
        self.assertIn("if v_tarefa.adaptador = 'sintese' then", publicador)
        self.assertIn("a síntese não pode encerrar antes das tarefas-fonte", self.normalizado)
        self.assertIn(
            "create or replace function public.validar_fencing_resultado_investigacao",
            self.normalizado,
        )
        for tabela in (
            "investigacao_evidencias", "investigacao_alternativas",
            "investigacao_pendencias",
        ):
            self.assertIn(f"before insert on public.{tabela}", self.normalizado)
        self.assertIn("resultado_lease_token = p_lease_token", self.normalizado)
        self.assertIn("resultado_fencing_token = p_fencing_token", self.normalizado)
        self.assertIn(
            "investigacao_id, tarefa_id, tarefa_fencing_token, linhagem, chave_natural_hash",
            self.normalizado,
        )
        self.assertGreaterEqual(
            self.normalizado.count(
                "investigacao_id, tarefa_id, tarefa_fencing_token, chave_idempotencia"
            ),
            2,
        )
        ligacao = self.normalizado.split(
            "create or replace function public.validar_fencing_ligacao_investigacao", 1
        )[1].split("drop trigger if exists investigacao_alternativa_evidencias_fencing", 1)[0]
        self.assertIn("tarefa_fonte.estado_execucao = 'concluida'", ligacao)
        self.assertIn(
            "tarefa_fonte.resultado_fencing_token = evidencia.tarefa_fencing_token",
            ligacao,
        )
        self.assertIn("investigacao_tarefas_consulta_imutavel", self.normalizado)
        self.assertIn("public.investigacao_consulta_spec_valida", self.normalizado)
        self.assertIn("consulta_ref", self.normalizado)
        self.assertIn("consulta_spec", self.normalizado)
        self.assertIn("consulta_hash", self.normalizado)
        self.assertIn("evidência contém campo fora do contrato da fonte", publicador)
        self.assertIn("'inconclusivo', null", publicador)
        self.assertIn("'aguardando-correlator-v1'", publicador)
        self.assertIn("investigacao_confianca_campos_valida", self.normalizado)

    def test_evento_append_only_e_entrega_separada(self):
        self.assertIn("evento técnico append-only", self.sql.lower())
        self.assertIn("create table if not exists public.investigacao_entregas", self.normalizado)
        self.assertIn(
            "grant select, insert on public.investigacao_eventos to service_role",
            self.normalizado,
        )
        self.assertNotIn(
            "grant select, insert, update on public.investigacao_eventos to service_role",
            self.normalizado,
        )
        entregas = self.normalizado.split(
            "create table if not exists public.investigacao_entregas", 1
        )[1].split("create or replace function public.investigacao_alternativas_suportadas", 1)[0]
        self.assertIn("erro_codigo ~ '^[a-z0-9_.-]{1,80}$'", entregas)
        self.assertIn(
            "public.investigacao_texto_sanitizado(erro_sanitizado)", entregas,
        )

    def test_alternativas_sao_atomicas_e_contraprova_e_semantica(self):
        helper = self.normalizado.split(
            "create or replace function public.investigacao_alternativas_suportadas", 1
        )[1].split("create index if not exists investigacoes_revisao_fila_idx", 1)[0]
        self.assertIn(
            "evidencia_atomica.fatos_normalizados @> alternativa.campos_snapshot",
            helper,
        )
        self.assertIn("tarefa_atomica.estado_execucao = 'concluida'", helper)
        self.assertIn(
            "tarefa_a.resultado_fencing_token = evidencia_a.tarefa_fencing_token",
            helper,
        )
        self.assertIn(
            "tarefa_b.resultado_fencing_token = evidencia_b.tarefa_fencing_token",
            helper,
        )
        tabela = self.normalizado.split(
            "create table if not exists public.investigacao_alternativa_evidencias", 1
        )[1].split("create table if not exists public.investigacao_pendencias", 1)[0]
        self.assertIn("primary key (alternativa_id, evidencia_id)", tabela)
        self.assertNotIn("primary key (alternativa_id, evidencia_id, papel)", tabela)
        publicador = self.normalizado.split(
            "create or replace function public.publicar_resultado_tarefa_investigacao", 1
        )[1].split("create or replace function public.concluir_tarefa_investigacao", 1)[0]
        self.assertIn(
            "evidência contrária precisa contestar exatamente os campos declarados",
            publicador,
        )
        self.assertIn(
            "evidencia.fatos_normalizados -> campo.key is distinct from campo.value",
            publicador,
        )

    def test_obsolescencia_usa_mapa_completo_e_acl_fechada(self):
        self.assertIn(
            "create or replace function public.investigacao_fencing_snapshot",
            self.normalizado,
        )
        helper = self.normalizado.split(
            "create or replace function public.investigacao_fencing_snapshot", 1
        )[1].split(
            "create or replace function public.obsoletar_investigacao_por_mudanca_draft", 1
        )[0]
        for campo in (
            "fencing_token", "estado_execucao", "resultado_lease_token",
            "resultado_fencing_token",
        ):
            self.assertIn(f"'{campo}'", helper)
        self.assertIn("jsonb_object_agg", helper)
        self.assertNotIn("max(fencing_token)", self.normalizado)
        candidatos = self.normalizado.split(
            "create or replace function public.obsoletar_investigacao_por_mudanca_candidatos", 1
        )[1].split("create or replace function public.vincular_investigacao_rascunho", 1)[0]
        self.assertIn("array_agg(item order by item)", candidatos)
        self.assertIn("foreach v_id in array v_ids_lock", candidatos)
        self.assertIn(
            "v_fencing_atual is distinct from p_fencing_esperado", candidatos,
        )
        self.assertIn("investigacao_ja_obsoleta", candidatos)
        self.assertIn("chave_idempotencia = v_evento_chave", candidatos)
        for assinatura in (
            "public.obsoletar_investigacao_por_mudanca_candidatos(uuid, jsonb, jsonb)",
            "public.investigacao_fencing_snapshot(uuid)",
            "public.investigacao_snapshot_candidatos_atual(uuid[], jsonb)",
            "public.investigacao_alternativas_suportadas(uuid, uuid, uuid, bigint)",
        ):
            self.assertIn(
                f"revoke all on function {assinatura} from public, anon, authenticated, service_role",
                self.normalizado,
            )

    def test_gate_exige_atestado_do_retrato_atual(self):
        guard = self.normalizado.split(
            "create or replace function public.exigir_investigacao_anexada_para_promocao", 1
        )[1].split(
            "create or replace function public.bloquear_pending_action_com_investigacao", 1
        )[0]
        self.assertIn("anexado_draft_atualizado_em", guard)
        self.assertIn("is not distinct from v_draft.atualizado_em", guard)
        self.assertIn("investigacao_snapshot_candidatos_atual", guard)
        self.assertIn(
            "os dados mudaram; conclua e anexe a investigação do retrato atual",
            guard,
        )
        tabela = self.normalizado.split(
            "create table if not exists public.investigacoes_revisao", 1
        )[1].split("create table if not exists public.investigacao_tarefas", 1)[0]
        self.assertIn("anexado_draft_atualizado_em timestamptz", tabela)

    def test_materializacao_repetida_confere_tripla_exata(self):
        materializador = self.normalizado.split(
            "create or replace function public.materializar_revisao_investigada", 1
        )[1].split(
            "create or replace function public.preparar_promocao_revisao_investigada", 1
        )[0]
        self.assertIn("repetição diverge da tripla já materializada", materializador)
        self.assertIn("to_jsonb(acao) @> p_pending_action", materializador)
        self.assertIn("to_jsonb(evento) @> p_evento", materializador)
        self.assertIn(
            "v_investigacao.anexado_draft_id is distinct from v_draft_id",
            materializador,
        )

    def test_rpc_apenas_anexa_ao_rascunho_existente_e_e_restrita(self):
        vinculador = self.normalizado.split(
            "create or replace function public.vincular_investigacao_rascunho", 1
        )[1].split("create or replace function public.anexar_investigacao_revisao", 1)[0]
        self.assertIn("staging_candidato_id", vinculador)
        self.assertIn("investigacao_ids_candidatos_rascunho", vinculador)
        self.assertIn("staging_candidato_ids", self.normalizado)
        self.assertIn("staging_candidatos_atualizados_em", vinculador)
        self.assertIn("fingerprint_grupo", vinculador)
        self.assertIn("v_ids_draft is distinct from v_ids_investigacao", vinculador)
        self.assertIn("v_fingerprint_draft is distinct from v_investigacao.fingerprint_base", vinculador)
        self.assertIn("from public.negocios_candidatos", vinculador)
        self.assertIn("for update", vinculador)
        self.assertIn("rascunho_ja_vinculado_validado", vinculador)
        self.assertLess(
            vinculador.index("pg_advisory_xact_lock"),
            vinculador.index("select * into v_investigacao"),
        )
        self.assertIn("source_draft_atualizado_em = v_draft.atualizado_em", vinculador)
        self.assertNotIn("insert into public.operation_drafts", vinculador)
        self.assertNotIn("insert into public.pending_actions", vinculador)
        self.assertIn(
            "grant execute on function public.vincular_investigacao_rascunho(uuid, uuid) to service_role",
            self.normalizado,
        )
        marcador = "create or replace function public.anexar_investigacao_revisao"
        funcao = self.normalizado.split(marcador, 1)[1].split(
            "create or replace function public.materializar_revisao_investigada", 1
        )[0]
        inseridas = re.findall(r"insert\s+into\s+public\.([a-z0-9_]+)", funcao)
        self.assertEqual(
            [tabela for tabela in inseridas
             if tabela != "investigacao_autorizacoes_corretiva"],
            ["eventos", "investigacao_eventos"],
        )
        self.assertTrue(
            set(inseridas) <= {
                "investigacao_autorizacoes_corretiva",
                "eventos", "investigacao_eventos",
            }
        )
        self.assertIn("update public.operation_drafts", funcao)
        self.assertNotIn("insert into public.operation_drafts", funcao)
        self.assertNotIn("insert into public.pending_actions", funcao)
        self.assertNotIn("target_table", funcao)
        self.assertIn("source_draft_atualizado_em", funcao)
        self.assertIn(
            "return public.obsoletar_investigacao_por_mudanca_draft(", funcao
        )
        self.assertIn("perform public.vincular_investigacao_rascunho", funcao)
        self.assertLess(
            funcao.index("pg_advisory_xact_lock"),
            funcao.index("select * into v_investigacao"),
        )
        self.assertIn("a revisão já foi encerrada; preserve a evidência", funcao)
        self.assertIn("há promoção preparada, aguardando ou executada", funcao)
        self.assertIn("acao_tipo = 'promover_revisao_operacional'", funcao)
        self.assertIn("tentativa que concluiu a tarefa", funcao)
        self.assertGreaterEqual(funcao.count("estado_execucao = 'concluida'"), 8)
        self.assertIn("resultado_lease_token = evidencia.tarefa_lease_token", funcao)
        self.assertIn("resultado_fencing_token = alternativa.tarefa_fencing_token", funcao)
        self.assertIn("resultado_fencing_token = pendencia.tarefa_fencing_token", funcao)
        self.assertIn("as buscas da investigação ainda não estão encerradas", funcao)
        self.assertIn("a investigação precisa apontar um rascunho já existente", funcao)
        self.assertIn("resultado único exige exatamente uma alternativa", funcao)
        self.assertIn("resultado múltiplo exige ao menos duas versões realmente distintas", funcao)
        self.assertIn("toda alternativa precisa apontar sua evidência favorável", funcao)
        self.assertIn("resultado incompleto exige pendência humana explícita", funcao)
        self.assertIn("resultado divergente exige evidência contrária explícita", funcao)
        self.assertIn("'versoes_revisao'", funcao)
        self.assertIn(
            "revoke all on function public.anexar_investigacao_revisao(uuid) from public, anon, authenticated",
            self.normalizado,
        )
        self.assertIn(
            "grant execute on function public.anexar_investigacao_revisao(uuid) to service_role",
            self.normalizado,
        )
        for tabela in (
            "operacoes", "compras", "vendas", "abates", "pesagens_caderno",
            "fluxo_caixa", "transacoes_banco",
        ):
            self.assertNotRegex(
                funcao,
                rf"(?:insert\s+into|update|delete\s+from)\s+public\.{tabela}\b",
            )

    def test_obsolescencia_e_campos_obrigatorios_falham_fechados(self):
        self.assertIn(
            "create or replace function public.investigacao_campos_obrigatorios_validos",
            self.normalizado,
        )
        self.assertIn(
            "create or replace function public.investigacao_instante_texto_seguro",
            self.normalizado,
        )
        self.assertIn(
            "create or replace function public.investigacao_snapshots_candidatos_validos",
            self.normalizado,
        )
        tabela = self.normalizado.split(
            "create table if not exists public.investigacoes_revisao", 1
        )[1].split(
            "create table if not exists public.investigacao_tarefas", 1
        )[0]
        self.assertIn(
            "check (public.investigacao_campos_obrigatorios_validos(campos_obrigatorios))",
            tabela,
        )
        self.assertIn("negocio_candidato_id is not null", tabela)
        self.assertIn(
            "(negocio_candidato_id = any (negocio_candidato_ids)) is true",
            tabela,
        )
        self.assertIn(
            "check (public.investigacao_snapshots_candidatos_validos(", tabela
        )
        funcao = self.normalizado.split(
            "create or replace function public.obsoletar_investigacao_por_mudanca_draft",
            1,
        )[1].split(
            "create or replace function public.vincular_investigacao_rascunho", 1
        )[0]
        for trecho in (
            "p_source_draft_atualizado_em",
            "p_fencing_esperado",
            "for update",
            "estado_execucao = 'obsoleta'",
            "tipo, referencia_entidade",
            "'investigacao_obsoleta'",
        ):
            self.assertIn(trecho, funcao)
        self.assertIn(
            "revoke all on function public.obsoletar_investigacao_por_mudanca_draft(uuid, timestamptz, jsonb) from public, anon, authenticated, service_role",
            self.normalizado,
        )
        self.assertIn(
            "grant execute on function public.obsoletar_investigacao_por_mudanca_draft(uuid, timestamptz, jsonb) to service_role",
            self.normalizado,
        )
        for tabela_operacional in (
            "operacoes", "compras", "vendas", "abates", "pesagens_caderno",
            "fluxo_caixa", "transacoes_banco",
        ):
            self.assertNotRegex(
                funcao,
                rf"(?:insert\s+into|update|delete\s+from)\s+public\.{tabela_operacional}\b",
            )

    def test_materializacao_e_gate_de_promocao_sao_atomicos_no_backend(self):
        self.assertIn("negocio_candidato_ids uuid[]", self.normalizado)
        self.assertIn("source_candidatos_atualizados_em jsonb", self.normalizado)
        self.assertIn(
            "investigacao_uuid_array_corresponde_objeto( negocio_candidato_ids, source_candidatos_atualizados_em",
            self.normalizado,
        )
        views = self.normalizado.split(
            "create or replace view public.v_investigacoes_revisao", 1
        )[1].split("create or replace view public.v_investigacao_alternativas", 1)[0]
        self.assertIn("negocio_candidato_ids", views)
        self.assertIn(
            "create or replace view public.v_investigacoes_revisao_materializacao",
            self.normalizado,
        )
        self.assertIn(
            "revoke all on public.v_investigacoes_revisao_materializacao from public, anon, authenticated, service_role",
            self.normalizado,
        )
        self.assertIn(
            "grant select on public.v_investigacoes_revisao_materializacao to service_role",
            self.normalizado,
        )

        materializador = self.normalizado.split(
            "create or replace function public.materializar_revisao_investigada", 1
        )[1].split(
            "create or replace function public.preparar_promocao_revisao_investigada", 1
        )[0]
        insercoes = re.findall(
            r"insert\s+into\s+public\.([a-z0-9_]+)", materializador
        )
        self.assertEqual(
            [tabela for tabela in insercoes if tabela != "investigacao_autorizacoes_corretiva"],
            ["pending_actions", "operation_drafts", "eventos"],
        )
        self.assertTrue(
            set(insercoes) <= {
                "investigacao_autorizacoes_corretiva",
                "pending_actions", "operation_drafts", "eventos",
            }
        )
        self.assertIn("for update", materializador)
        self.assertIn("v_investigacao_pre", materializador)
        self.assertIn("a investigação mudou durante a aquisição de locks", materializador)
        self.assertLess(
            materializador.index("pg_advisory_xact_lock"),
            materializador.index("for update"),
        )
        self.assertIn("perform public.vincular_investigacao_rascunho", materializador)
        self.assertIn("public.anexar_investigacao_revisao", materializador)
        self.assertIn("campo fora do contrato fechado", materializador)
        self.assertIn(
            "public.investigacao_jsonb_objeto_tamanho(p_operation_draft) <> 16",
            materializador,
        )
        self.assertIn(
            "public.investigacao_jsonb_objeto_tamanho(p_pending_action) <> 17",
            materializador,
        )
        self.assertIn(
            "public.investigacao_jsonb_objeto_tamanho(p_evento) <> 18",
            materializador,
        )
        self.assertIn("investigacao_json_sanitizado(p_operation_draft)", materializador)
        self.assertIn("ids determinísticos já existem", materializador)
        for tabela in (
            "operacoes", "compras", "vendas", "abates", "pesagens_caderno",
            "fluxo_caixa", "transacoes_banco",
        ):
            self.assertNotRegex(
                materializador,
                rf"(?:insert\s+into|update|delete\s+from)\s+public\.{tabela}\b",
            )
        self.assertIn(
            "grant execute on function public.materializar_revisao_investigada(uuid, jsonb, jsonb, jsonb) to service_role",
            self.normalizado,
        )
        self.assertIn("create trigger pending_actions_bloqueia_investigacao", self.normalizado)
        self.assertIn("create trigger operation_drafts_bloqueia_investigacao", self.normalizado)
        self.assertIn("pg_advisory_xact_lock", self.normalizado)
        self.assertIn("pg_try_advisory_xact_lock", self.normalizado)
        self.assertIn("operation_drafts_investigacao_atualizado_em", self.normalizado)
        self.assertIn("pending_actions_investigacao_atualizado_em", self.normalizado)
        serializacao = self.normalizado.split(
            "create trigger investigacoes_revisao_serializacao", 1
        )[1].split("create or replace function public.proteger_origem", 1)[0]
        self.assertIn("before insert on public.investigacoes_revisao", serializacao)
        self.assertNotIn("before insert or update", serializacao)
        self.assertIn("new.estado_execucao := 'obsoleta'", self.normalizado)
        self.assertIn("tratar em investigação complementar", self.normalizado)
        self.assertIn("investigacao.estado_execucao = 'concluida'", self.normalizado)
        self.assertIn("investigacao.anexado_em is null", self.normalizado)
        self.assertIn(
            "a investigação precisa terminar e ser anexada antes da promoção",
            self.normalizado,
        )

    def test_publicacao_exige_cobertura_coerente(self):
        publicador = self.normalizado.split(
            "create or replace function public.publicar_resultado_tarefa_investigacao", 1
        )[1].split(
            "create or replace function public.concluir_tarefa_investigacao", 1
        )[0]
        for mensagem in (
            "cobertura e resultado precisam ser estados válidos",
            "cobertura geral incompleta exige resultado incompleto",
            "fonte declarada vazia não pode publicar evidência",
            "fonte completa sem evidência deve declarar vazio com cobertura",
            "falha de cobertura não pode declarar resultado conclusivo",
        ):
            self.assertIn(mensagem, publicador)
        self.assertIn("if v_tarefa.adaptador = 'sintese' then", publicador)
        self.assertIn("elsif p_estado_cobertura = 'vazio_com_cobertura'", publicador)
        self.assertIn("elsif p_estado_cobertura = 'completa'", publicador)
        self.assertIn(
            "alternativa parcial não pode encerrar a investigação como conclusiva",
            publicador,
        )
        self.assertIn(
            "alternativa parcial exige pendência aberta para cada campo ausente",
            publicador,
        )

    def test_preparacao_investigada_e_atomica_idempotente_e_fechada(self):
        marcador = (
            "create or replace function public.preparar_promocao_revisao_investigada"
        )
        self.assertIn(marcador, self.normalizado)
        funcao = self.normalizado.split(marcador, 1)[1].split(
            "revoke all on function public.assumir_tarefa_investigacao", 1
        )[0]
        self.assertIn("security definer", funcao)
        self.assertIn("pedido de preparação inválido ou não sanitizado", funcao)
        self.assertIn("source_draft_atualizado_em", funcao)
        self.assertIn("source_pending_action_atualizado_em", funcao)
        self.assertIn("retry incerto", funcao)
        retry = funcao.split("retry incerto", 1)[1].split(
            "select * into v_draft", 1
        )[0]
        self.assertNotIn("for share", retry)
        self.assertIn("source_pending_action_id", retry)
        self.assertLess(
            funcao.index("repeticao_idempotente"),
            funcao.index("source_draft_atualizado_em')::timestamptz"),
        )
        self.assertIn("perform public.exigir_investigacao_anexada_para_promocao", funcao)
        for origem in (
            "'origem_canal', v_draft.origem_canal",
            "'origem_conversa_id', v_draft.origem_conversa_id",
            "'origem_mensagem_id', v_draft.origem_mensagem_id",
        ):
            self.assertIn(origem, funcao)
        guard = self.normalizado.split(
            "create or replace function public.bloquear_pending_action_com_investigacao", 1
        )[1].split(
            "create or replace function public.proteger_draft_corretivo_permanente", 1
        )[0]
        self.assertIn(
            "new.acao_tipo is not distinct from 'promover_revisao_operacional'",
            guard,
        )
        self.assertIn("nova promoção exige controle concorrente lease-v1", guard)
        self.assertIn("promoção e rascunho de origem não são coerentes", guard)
        permanente = self.normalizado.split(
            "create or replace function public.proteger_pending_action_permanente", 1
        )[1].split(
            "create or replace function public.bloquear_pending_action_com_investigacao", 1
        )[0]
        self.assertIn(
            "old.acao_tipo is not distinct from 'promover_revisao_operacional'",
            permanente,
        )
        self.assertIn(
            "a origem e o conteúdo operacional da promoção são imutáveis",
            permanente,
        )
        self.assertIn("old.payload -> 'source_draft_id'", permanente)
        self.assertIn("new.payload -> 'source_draft_id'", permanente)
        self.assertIn("old.payload -> 'proposed_record'", permanente)
        self.assertIn(
            "promoção ativa exige um rascunho de origem explícito", permanente
        )
        trigger = self.normalizado.split(
            "create trigger pending_actions_bloqueia_investigacao", 1
        )[1].split(
            "create or replace function public.bloquear_draft_com_investigacao", 1
        )[0]
        self.assertIn("after insert or update on public.pending_actions", trigger)
        self.assertIn(
            "create policy pending_actions_authenticated_revisoes_select",
            self.normalizado,
        )
        self.assertIn(
            "acao_tipo is distinct from 'promover_revisao_operacional'",
            self.normalizado,
        )
        self.assertIn(
            "and acao_tipo is distinct from 'revisar_correcao_pos_gravacao'",
            self.normalizado,
        )
        tabela = self.normalizado.split(
            "create table if not exists public.investigacoes_revisao", 1
        )[1].split(
            "create table if not exists public.investigacao_tarefas", 1
        )[0]
        self.assertIn(
            "check (num_nonnulls(source_draft_id, source_draft_atualizado_em) in (0, 2))",
            tabela,
        )
        origem = self.normalizado.split(
            "create or replace function public.proteger_origem_investigacao_revisao", 1
        )[1].split(
            "drop trigger if exists investigacoes_revisao_origem_imutavel", 1
        )[0]
        self.assertIn("old.source_draft_id is not null", origem)
        self.assertIn("snapshot temporal não podem ser trocados", origem)
        insercoes = re.findall(
            r"insert\s+into\s+public\.([a-z0-9_]+)", funcao
        )
        internas = {
            "investigacao_autorizacoes_corretiva",
            "investigacao_autorizacoes_promocao",
            "investigacao_eventos",
        }
        self.assertEqual(
            [tabela for tabela in insercoes if tabela not in internas],
            ["pending_actions", "eventos"],
        )
        self.assertTrue(
            set(insercoes) <= internas | {"pending_actions", "eventos"}
        )
        for tabela in (
            "operacoes", "compras", "vendas", "abates", "pesagens_caderno",
            "fluxo_caixa", "transacoes_banco",
        ):
            self.assertNotRegex(
                funcao,
                rf"(?:insert\s+into|update|delete\s+from)\s+public\.{tabela}\b",
            )
        self.assertIn(
            "revoke all on function public.preparar_promocao_revisao_investigada(uuid, uuid, jsonb) from public, anon, authenticated, service_role",
            self.normalizado,
        )
        self.assertIn(
            "grant execute on function public.preparar_promocao_revisao_investigada(uuid, uuid, jsonb) to service_role",
            self.normalizado,
        )

    def test_cutover_inventaria_controle_de_preparacao_e_helper_snapshot(self):
        caminho_cutover = ROOT / "supabase/migrations/202608290002_ativar_mediador_investigacoes.sql"
        cutover = caminho_cutover.read_text(encoding="utf-8").lower()

        for assinatura in (
            "public.assumir_promocao_operacional(uuid,text,text,text,text,integer)",
            "public.reconciliar_promocao_em_execucao(uuid,bigint,text,text)",
            "public.substituir_investigacao_corretiva_stale(uuid,text,text,text,text)",
            "public.investigacao_snapshot_registro_promocao(text,uuid,uuid,jsonb)",
        ):
            self.assertIn(assinatura, cutover)

        self.assertIn("pending_actions_promocao_preparacao_interna_valida", cutover)
        for coluna in (
            "promocao_preparacao_chave",
            "promocao_preparacao_hash",
            "promocao_confirmacao_origem_conversa_id",
            "promocao_confirmacao_origem_mensagem_id",
            "promocao_lease_token",
            "promocao_fencing_token",
            "promocao_resultado_pedido_hash",
            "entidade_id",
        ):
            self.assertIn(coluna, cutover)

        # O grant humano continua existindo para a fila, mas não pode
        # incorporar controle interno quando novas colunas forem adicionadas.
        grants_select = cutover.split("grant select (", 2)
        self.assertEqual(len(grants_select), 3)
        selecao_acoes = grants_select[2].split(") on public.pending_actions to authenticated;", 1)[0]
        for coluna in (
            "entidade_id",
            "promocao_controle_version",
            "promocao_lease_executor",
            "promocao_lease_token",
            "promocao_lease_expira_em",
            "promocao_confirmacao_origem_conversa_id",
            "promocao_confirmacao_origem_mensagem_id",
            "promocao_preparacao_chave",
            "promocao_preparacao_hash",
            "promocao_fencing_token",
            "promocao_resultado_lease_token",
            "promocao_resultado_fencing_token",
            "promocao_resultado_pedido_hash",
        ):
            self.assertNotIn(coluna, selecao_acoes)

        self.assertIn(
            "(v_procedure = 'public.investigacao_snapshot_registro_promocao(text,uuid,uuid,jsonb)'::regprocedure\n"
            "               and linguagem.lanname = 'plpgsql' and funcao.provolatile = 'v'\n"
            "               and funcao.proisstrict)",
            cutover,
        )

    def test_rollout_em_duas_fases_e_validacoes_falham_fechadas(self):
        fundacao = MIGRACOES[0].read_text(encoding="utf-8").lower()
        ativacao = MIGRACOES[1].read_text(encoding="utf-8").lower()
        rollback = ROLLBACK_ATIVACAO.read_text(encoding="utf-8").lower()
        for trigger in (
            "pending_actions_bloqueia_investigacao",
            "operation_drafts_bloqueia_investigacao",
        ):
            self.assertNotIn(f"create trigger {trigger}", fundacao)
            self.assertIn(f"create trigger {trigger}", ativacao)
            self.assertIn(f"drop trigger {trigger} on public", rollback)
        self.assertIn(
            "acao_tipo is distinct from 'promover_revisao_operacional'",
            ativacao,
        )
        self.assertIn(
            "and acao_tipo is distinct from 'revisar_correcao_pos_gravacao'",
            ativacao,
        )
        self.assertIn("'revisar_correcao_pos_gravacao'", ativacao)
        self.assertIn(
            "create policy pending_actions_authenticated_revisoes", rollback
        )
        self.assertIn("for all", rollback)
        self.assertIn("using (true)", rollback)
        self.assertIn("with check (true)", rollback)
        preparador = self.normalizado.split(
            "create or replace function public.preparar_promocao_revisao_investigada", 1
        )[1].split(
            "revoke all on function public.assumir_tarefa_investigacao", 1
        )[0]
        self.assertIn(
            "(v_target in ('compras', 'vendas', 'pesagens_caderno', 'abates')) is not true",
            preparador,
        )
        self.assertIn(
            "(v_acao_origem.acao_tipo in (",
            preparador,
        )
        self.assertIn("(v_acao_origem.status in (", preparador)
        self.assertIn(")) is not true", preparador)
        anexador = self.normalizado.split(
            "create or replace function public.anexar_investigacao_revisao", 1
        )[1].split(
            "create or replace function public.materializar_revisao_investigada", 1
        )[0]
        self.assertIn("if (v_draft.status in (", anexador)
        self.assertIn(") is not true then", anexador)

    def test_cutover_e_rollback_atestam_e_preservam_vinculos_duraveis(self):
        fundacao = MIGRACOES[0].read_text(encoding="utf-8").lower()
        ativacao = MIGRACOES[1].read_text(encoding="utf-8").lower()
        rollback = ROLLBACK_ATIVACAO.read_text(encoding="utf-8").lower()
        for tabela, trigger in (
            ("compras", "compras_vinculo_promocao_protegido"),
            ("vendas", "vendas_vinculo_promocao_protegido"),
            ("pesagens_caderno", "pesagens_vinculo_promocao_protegido"),
            ("abates", "abates_vinculo_promocao_protegido"),
        ):
            self.assertIn(f"'public.{tabela}'::regclass", ativacao)
            self.assertIn(f"'{trigger}'", ativacao)
            self.assertIn(f"'{trigger}'", rollback)
            self.assertNotIn(f"create trigger {trigger}", fundacao)
            self.assertIn(f"create trigger {trigger}", ativacao)
            self.assertIn(f"drop trigger {trigger}", rollback)
        for trigger, funcao in (
            (
                "pending_actions_protecao_permanente",
                "public.proteger_pending_action_permanente()",
            ),
            (
                "operation_drafts_protecao_corretiva_permanente",
                "public.proteger_draft_corretivo_permanente()",
            ),
        ):
            self.assertIn(f"'{trigger}'", ativacao)
            self.assertIn(f"'{trigger}'", rollback)
            self.assertIn(funcao, ativacao)
            self.assertIn(funcao, rollback)
            self.assertNotIn(f"drop trigger {trigger}", rollback)
        self.assertGreaterEqual(
            ativacao.replace(" ", "").count("gatilho.tgtype=31"), 2
        )
        self.assertGreaterEqual(rollback.count("gatilho.tgtype = 31"), 2)
        self.assertIn("trigger before row operacional não allowlisted", ativacao)
        self.assertIn("gatilho.tgtype & 3", ativacao)
        self.assertIn("has_schema_privilege(v_authenticated, 'public', 'create')", ativacao)
        self.assertIn("owner/acl/trigger operacional divergente", ativacao)
        self.assertIn("indice.indisvalid", ativacao)
        self.assertIn("indice.indisready", ativacao)
        self.assertIn("indice.indislive", ativacao)
        self.assertIn("compras_idempotency_key_nao_vazia", ativacao)
        self.assertIn("pending_actions_promocao_lease_v1_valido", ativacao)
        self.assertIn("pending_actions_corretiva_nao_executavel", ativacao)
        self.assertIn("operation_drafts_corretiva_investigacao_unica", ativacao)
        for coluna, indice in (
            ("idempotency_key", "compras_idempotency_key_unique"),
            ("promocao_origem_id", "vendas_promocao_origem_id_unica"),
            ("promocao_origem_id", "pesagens_promocao_origem_id_unica"),
            ("promocao_origem_id", "abates_promocao_origem_id_unica"),
        ):
            self.assertIn(coluna, ativacao)
            self.assertIn(indice, ativacao)
        for capacidade in (
            "investigacao_autorizacoes_promocao",
            "investigacao_autorizacoes_corretiva",
        ):
            self.assertIn(capacidade, ativacao)
        self.assertIn("capacidade transitória residual impede o cutover", ativacao)

    def test_cutover_restringe_colunas_internas_da_fila_ao_mediador(self):
        ativacao = MIGRACOES[1].read_text(encoding="utf-8").lower()
        self.assertIn(
            "revoke select, insert, update on table public.operation_drafts",
            ativacao,
        )
        self.assertIn(
            "revoke select, insert, update on table public.pending_actions",
            ativacao,
        )
        self.assertIn("grant select (", ativacao)
        self.assertIn("grant update (", ativacao)
        self.assertIn("grant insert (", ativacao)
        for coluna in (
            "investigacao_origem_id",
            "promocao_origem_id",
            "entidade_final_id",
            "entidade_id",
            "promocao_lease_token",
            "promocao_fencing_token",
            "promocao_resultado_pedido_hash",
        ):
            self.assertIn(f"'{coluna}'", ativacao)
        self.assertIn(
            "revisões não pode manter grant amplo de tabela para authenticated",
            ativacao,
        )
        self.assertIn(
            "coluna interna de ação exposta",
            ativacao,
        )

    def test_alternativas_usam_um_unico_vocabulario(self):
        tabela = self.normalizado.split(
            "create table if not exists public.investigacao_alternativas", 1
        )[1].split(
            "create table if not exists public.investigacao_alternativa_evidencias", 1
        )[0]
        for classe in ("'possivel'", "'provavel'", "'forte'", "'ambiguo'"):
            self.assertIn(classe, tabela)
        self.assertNotIn("'inconclusivo'", tabela)
        self.assertNotIn("'descartada'", tabela)
        publicador = self.normalizado.split(
            "create or replace function public.publicar_resultado_tarefa_investigacao", 1
        )[1].split(
            "create or replace function public.concluir_tarefa_investigacao", 1
        )[0]
        self.assertNotIn("'inconclusivo'", publicador.split(
            "for v_item in select value from jsonb_array_elements(v_alternativas)", 1
        )[1].split(
            "for v_item in select value from jsonb_array_elements(v_pendencias)", 1
        )[0])


if __name__ == "__main__":
    unittest.main()
