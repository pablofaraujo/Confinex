import hashlib
import json
import re
import unittest
import uuid

from investigacoes_revisao import (
    ESTADOS_COBERTURA,
    HASH_REGRAS_CONFIANCA,
    REGRAS_CONFIANCA,
    TABELAS_CONTROLE,
    TABELAS_ANEXO,
    chave_estavel,
    chaves_investigacao,
    classificar_cobertura,
    confianca_explicavel,
    contrato_consulta,
    planejar_investigacao,
    resolver_consulta_tarefa,
    sanitizar_payload,
    selar_fonte_adaptador,
    validar_plano,
)


FP = "a" * 64


class InvestigacoesRevisaoTest(unittest.TestCase):
    def setUp(self):
        self.assunto = {
            "tipo": "revisao", "titulo": " Acerto de gado ",
            "contexto_nome": "Grupo A",
        }
        self.origem = {
            "canal": "WhatsApp", "conversa_id": "tecnico-1",
            "mensagem_id": "m1", "linhagem": "wey",
        }
        self.consulta = {"pergunta": " localizar PIX ", "termos": ["PIX", "valor"]}

    def fonte(self, bruto, indice=0):
        bruto = dict(bruto)
        consulta = bruto.get("consulta") or self.consulta
        cobertura = bruto.get("cobertura") or "completa"
        adaptador = bruto.get("adaptador") or "agronotas"
        candidatos = []
        for original in bruto.get("candidatos", ()):
            candidato = dict(original)
            tipo = candidato.get("tipo_correspondencia")
            if tipo in {"identificador_exato", "documento_referenciado"}:
                identidade = candidato.get("chave_natural") or candidato.get("valor")
                candidato["identidade_registro"] = {
                    "tipo": "hash_anexo",
                    "valor": hashlib.sha256(
                        str(identidade).encode("utf-8")
                    ).hexdigest(),
                }
            # ``registro_ref`` é a referência opaca de um documento inteiro,
            # não uma chave natural legível.  As fixtures antigas usavam
            # "versao" ou "chave_natural" diretamente e, portanto, não
            # representavam uma saída real do adaptador.
            referencia_bruta = (
                candidato.get("registro_ref")
                or candidato.get("chave_natural")
                or candidato.get("versao")
                or {
                    "campo": candidato.get("campo"),
                    "valor": candidato.get("valor"),
                    "tipo": candidato.get("tipo_correspondencia"),
                }
            )
            candidato["registro_ref"] = hashlib.sha256(
                json.dumps(
                    referencia_bruta,
                    sort_keys=True,
                    ensure_ascii=False,
                    default=str,
                ).encode("utf-8")
            ).hexdigest()
            # Saídas estruturadas reais carregam proveniência de uma fonte
            # permitida.  Sem ela, a política deliberadamente não reconhece
            # independência nem eleva uma coincidência a confirmação.
            tabela_nativa = {
                "agronotas": "notas_fiscais_xml_raw",
                "ofx": "transacoes_banco_staging",
                "ima": "fontes_importacao",
            }.get(adaptador)
            if tabela_nativa:
                candidato.setdefault("fonte_tabela", tabela_nativa)
                candidato.setdefault(
                    "fonte_registro_id",
                    str(uuid.uuid5(
                        uuid.UUID("9d3b2670-6c13-4b40-8d11-9c1d7ef52279"),
                        f"{adaptador}:{candidato['registro_ref']}",
                    )),
                )
                candidato.setdefault("fonte_registro_xmin", "1")
            candidatos.append(candidato)
        consulta_hash = contrato_consulta(consulta)["consulta_hash"]
        prova = {
            "estado": "concluida" if cobertura in {
                "completa", "vazio_com_cobertura"
            } else "incompleta",
            "inicio_confirmado": cobertura in {"completa", "vazio_com_cobertura"},
            "fim_confirmado": cobertura in {"completa", "vazio_com_cobertura"},
            "consulta_hash": consulta_hash,
        }
        return selar_fonte_adaptador(
            adaptador=adaptador,
            versao_adaptador=bruto.get("versao_adaptador") or "v1",
            consulta=consulta,
            cobertura=cobertura,
            candidatos=candidatos,
            linhagem_registrada=(
                bruto.get("linhagem") or f"linhagem-{adaptador}-{indice}"
            ),
            prova_cobertura=prova,
        )

    def assunto_tipo(self, tipo):
        return {**self.assunto, "tipo": tipo, "titulo": f"Conferir {tipo}"}

    def plano(self, **kwargs):
        kwargs = dict(kwargs)
        assunto = kwargs.pop("assunto", self.assunto)
        consulta = kwargs.pop("consulta", self.consulta)
        if "fontes" in kwargs:
            kwargs["fontes"] = [
                fonte if not isinstance(fonte, dict) else self.fonte(fonte, indice)
                for indice, fonte in enumerate(kwargs["fontes"])
            ]
        else:
            candidatos = kwargs.pop("candidatos", ())
            kwargs["fontes"] = [self.fonte({
                "adaptador": kwargs.get("adaptador", "agronotas"),
                "consulta": consulta,
                "cobertura": "completa",
                "linhagem": self.origem["linhagem"],
                "candidatos": candidatos,
            })]
        return planejar_investigacao(
            assunto, self.origem, consulta,
            fingerprint_base=FP, cobertura="completa",
            instante_referencia="2026-08-29T12:00:00Z", **kwargs,
        )

    def test_chaves_incluem_politica_fingerprint_e_versao_do_adaptador(self):
        base = chaves_investigacao(
            self.assunto, self.origem, self.consulta,
            fingerprint_base=FP, versao_politica="p1", versao_adaptador="a1",
        )
        self.assertEqual(base, chaves_investigacao(
            self.assunto, self.origem, self.consulta,
            fingerprint_base=FP, versao_politica="p1", versao_adaptador="a1",
        ))
        alterada = chaves_investigacao(
            self.assunto, self.origem, self.consulta,
            fingerprint_base=FP, versao_politica="p1", versao_adaptador="a2",
        )
        self.assertNotEqual(base["investigacao"], alterada["investigacao"])
        self.assertNotEqual(base["tarefa"], alterada["tarefa"])
        self.assertTrue(all(
            re.fullmatch(r"(?:inv|tar|evi|mat)_[0-9a-f]{32}", valor)
            for valor in base.values()
        ))
        outra_evidencia = chaves_investigacao(
            self.assunto, self.origem, self.consulta,
            fingerprint_base=FP, chave_natural_evidencia="outra-chave",
        )
        self.assertNotEqual(base["evidencia"], outra_evidencia["evidencia"])

    def test_contexto_equivalente_e_ordem_dos_candidatos_nao_mudam_identidade(self):
        origem = dict(self.origem)
        chave_a = chaves_investigacao(
            {"titulo": "Acerto", "contexto_nome": "Grupo Á"},
            origem, self.consulta, fingerprint_base=FP,
        )
        chave_b = chaves_investigacao(
            {"titulo": " acerto ", "contexto_nome": " grupo a "},
            origem, self.consulta, fingerprint_base=FP,
        )
        self.assertEqual(chave_a["investigacao"], chave_b["investigacao"])
        candidatos = [
            {"campo": "peso", "valor": 1000, "linhagem": "gta", "tipo_correspondencia": "documento_referenciado"},
            {"campo": "valor", "valor": 2000, "linhagem": "ofx", "tipo_correspondencia": "valor_data"},
        ]
        self.assertEqual(
            self.plano(candidatos=candidatos)["registros"],
            self.plano(candidatos=list(reversed(candidatos)))["registros"],
        )

    def test_ids_opacos_preservam_caixa_e_plano_muda_a_rodada(self):
        caixa_a = chaves_investigacao(
            self.assunto, {**self.origem, "mensagem_id": "Msg-Á"}, self.consulta,
            fingerprint_base=FP,
        )
        caixa_b = chaves_investigacao(
            self.assunto, {**self.origem, "mensagem_id": "msg-á"}, self.consulta,
            fingerprint_base=FP,
        )
        self.assertNotEqual(caixa_a["investigacao"], caixa_b["investigacao"])
        assunto_pesagem = {
            **self.assunto, "tipo": "pesagem", "titulo": "Conferir pesagem",
        }
        base = self.plano(assunto=assunto_pesagem)
        outro_campo = self.plano(assunto={
            **self.assunto, "tipo": "compra", "titulo": "Conferir compra",
        })
        outra_consulta = self.plano(
            assunto=assunto_pesagem,
            consulta={"pergunta": "localizar GTA"},
        )
        inv_base = base["registros"]["investigacoes_revisao"][0]
        self.assertNotEqual(base["chaves"]["investigacao"], outro_campo["chaves"]["investigacao"])
        self.assertNotEqual(base["chaves"]["investigacao"], outra_consulta["chaves"]["investigacao"])
        contexto = json.loads(inv_base["plano_canonico"])
        self.assertEqual(contexto["tarefas"], inv_base["plano_tarefas"])
        self.assertEqual(
            hashlib.sha256(inv_base["plano_canonico"].encode()).hexdigest(),
            inv_base["plano_hash"],
        )

    def test_cobertura_e_existencia_de_resultado_sao_independentes(self):
        self.assertEqual(classificar_cobertura("ok", resultados=[1]), "completa")
        self.assertEqual(classificar_cobertura("ok"), "vazio_com_cobertura")
        self.assertEqual(
            classificar_cobertura("ok", resultados=[1], cobertura_completa=False),
            "cobertura_incompleta",
        )
        for estado in ESTADOS_COBERTURA - {"completa", "vazio_com_cobertura"}:
            self.assertEqual(classificar_cobertura(estado), estado)

    def test_uma_fonte_fraca_nunca_recebe_confianca_total(self):
        campos = confianca_explicavel([{
            "campo": "contraparte", "valor": "candidato",
            "linhagem": "wey", "tipo_correspondencia": "nome",
        }])
        alternativa = campos["contraparte"]["alternativas"][0]
        self.assertEqual(alternativa["classificacao"], "possivel")
        self.assertLessEqual(alternativa["confianca"], 0.35)

    def test_hash_identifica_o_ruleset_completo_e_muda_com_regra(self):
        calculado = hashlib.sha256(json.dumps(
            REGRAS_CONFIANCA, sort_keys=True,
        ).encode("utf-8")).hexdigest()
        self.assertEqual(HASH_REGRAS_CONFIANCA, calculado)
        mutado = dict(REGRAS_CONFIANCA)
        mutado["linhagem_duplicada_nao_eleva"] = False
        self.assertNotEqual(HASH_REGRAS_CONFIANCA, hashlib.sha256(json.dumps(
            mutado, sort_keys=True,
        ).encode("utf-8")).hexdigest())

    def test_mesma_linhagem_nao_conta_duas_confirmacoes(self):
        campos = confianca_explicavel([
            {"campo": "data", "valor": "2026-08-01", "linhagem": "xml-1", "tipo_correspondencia": "ocr"},
            {"campo": "data", "valor": "2026-08-01", "linhagem": "xml-1", "tipo_correspondencia": "extracao_llm"},
        ])
        alternativa = campos["data"]["alternativas"][0]
        self.assertEqual(len(alternativa["linhagens"]), 1)
        self.assertRegex(alternativa["linhagens"][0], r"^lin_[0-9a-f]{32}$")
        self.assertNotIn("xml-1", alternativa["linhagens"])
        self.assertEqual(alternativa["classificacao"], "possivel")

    def test_identificador_exato_so_e_forte_quando_unico_e_coerente(self):
        forte = confianca_explicavel([
            {
                "campo": "gta", "valor": "ref", "linhagem": "ima",
                "tipo_correspondencia": "identificador_exato",
            },
            {
                "campo": "gta", "valor": "ref", "linhagem": "nf",
                "tipo_correspondencia": "documento_referenciado",
            },
        ], cobertura_padrao="completa")["gta"]["alternativas"][0]
        nao_unico = confianca_explicavel([
            {
                "campo": "gta", "valor": "ref", "linhagem": "ima",
                "tipo_correspondencia": "identificador_exato",
            },
            {
                "campo": "gta", "valor": "ref", "linhagem": "ima",
                "tipo_correspondencia": "identificador_exato",
            },
            {
                "campo": "gta", "valor": "ref", "linhagem": "nf",
                "tipo_correspondencia": "documento_referenciado",
            },
        ], cobertura_padrao="completa")["gta"]["alternativas"][0]
        sem_coerencia = confianca_explicavel([{
            "campo": "gta", "valor": "ref", "linhagem": "ima",
            "tipo_correspondencia": "identificador_exato",
        }])["gta"]["alternativas"][0]
        # A API pública de cálculo não recebe o envelope selado; sem
        # proveniência atestada, coincidências exatas continuam apenas pistas.
        # Nenhuma fixture pode fabricar uma confirmação forte injetando campos.
        self.assertEqual(forte["classificacao"], "possivel")
        self.assertEqual(nao_unico["classificacao"], "possivel")
        self.assertEqual(sem_coerencia["classificacao"], "possivel")
        self.assertEqual(forte["regra_version"], "confianca-deterministica-v2")
        self.assertRegex(forte["inputs_hash"], r"^[0-9a-f]{64}$")
        self.assertEqual(json.loads(forte["inputs_canonico"]), forte["inputs_contexto"])
        self.assertEqual(
            hashlib.sha256(forte["inputs_canonico"].encode()).hexdigest(),
            forte["inputs_hash"],
        )
        self.assertIn("extracao_nao_confirmada", nao_unico["caps"])

    def test_flags_legados_do_adaptador_nao_comprovam_confianca_forte(self):
        resultado = confianca_explicavel([{
            "campo": "gta", "valor": "ref", "linhagem": "ima",
            "tipo_correspondencia": "identificador_exato",
            "unico": True, "coerente": True,
        }])["gta"]["alternativas"][0]
        self.assertEqual(resultado["classificacao"], "possivel")
        self.assertIn("extracao_nao_confirmada", resultado["caps"])
        with self.assertRaisesRegex(ValueError, "metadado_reservado_ao_mediador"):
            self.plano(candidatos=[{
                "campo": "gta", "valor": "ref", "linhagem": "ima",
                "tipo_correspondencia": "identificador_exato",
                "unico": True,
            }])

    def test_llm_permanece_pista_e_nao_promove_confianca(self):
        resultado = confianca_explicavel([{
            "campo": "peso", "valor": 1000, "linhagem": "modelo",
            "tipo_correspondencia": "extracao_llm",
        }], cobertura_padrao="completa")["peso"]["alternativas"][0]
        self.assertEqual(resultado["classificacao"], "possivel")
        self.assertLessEqual(resultado["confianca"], 0.35)

    def test_ambiguidade_divergencia_e_campos_ausentes_nao_confirmam(self):
        ambiguo = self.plano(candidatos=[
            {"campo": "contraparte", "valor": "A", "linhagem": "telegram", "tipo_correspondencia": "nome"},
            {"campo": "contraparte", "valor": "B", "linhagem": "wey", "tipo_correspondencia": "nome"},
        ])
        def documento(numero):
            return [
                {
                    "campo": campo, "valor": valor,
                    "chave_natural": "documento-compartilhado",
                    "tipo_correspondencia": "documento_referenciado",
                }
                for campo, valor in {
                    "data_emissao": "2026-08-01", "numero_nf": numero,
                    "quantidade": 10, "relacao_negocio": "negocio-a",
                    "valor_total": 100,
                }.items()
            ]
        divergente = self.plano(
            assunto=self.assunto_tipo("documento_fiscal"), fontes=[
            {
                "adaptador": "ima", "linhagem": "ficha-a",
                "cobertura": "completa", "consulta": "buscar gta ima",
                "candidatos": documento("G1"),
            },
            {
                "adaptador": "agronotas", "linhagem": "nota-b",
                "cobertura": "completa", "consulta": "buscar gta nota",
                "candidatos": documento("G2"),
            },
        ])
        incompleto = self.plano(
            assunto=self.assunto_tipo("pesagem"), candidatos=[{
            "campo": "data_folha", "valor": "2026-08-01", "linhagem": "nf",
            "tipo_correspondencia": "documento_referenciado",
        }])
        # Em uma revisão genérica, duas pistas de contraparte não satisfazem a
        # decisão humana obrigatória. Elas permanecem visíveis, mas a rodada
        # não pode se declarar resolvida.
        self.assertEqual(ambiguo["resultado"], "evidencia_insuficiente")
        self.assertEqual(divergente["resultado"], "divergente")
        self.assertEqual(incompleto["resultado"], "evidencia_insuficiente")

    def test_fingerprint_obsoleto_nao_publica_tripla(self):
        plano = self.plano(fingerprint_atual="b" * 64, candidatos=[{
            "campo": "gta", "valor": "ref", "linhagem": "ima",
            "tipo_correspondencia": "identificador_exato",
        }])
        inv = plano["registros"]["investigacoes_revisao"][0]
        self.assertEqual(inv["estado_execucao"], "obsoleta")
        self.assertEqual(plano["resultado"], "evidencia_insuficiente")

    def test_planeja_so_control_plane_e_anexa_somente_apos_materializacao_canonica(self):
        plano = self.plano(candidatos=[{
            "campo": "gta", "valor": "ref", "linhagem": "ima",
            "tipo_correspondencia": "identificador_exato",
        }])
        validar_plano(plano)
        self.assertEqual(set(plano["registros"]), set(TABELAS_CONTROLE))
        self.assertEqual(set(plano["tabelas_anexo"]), set(TABELAS_ANEXO))
        self.assertNotIn("operation_drafts", plano["registros"])
        self.assertEqual(plano["materializador_canonico"], "tools/materializar_revisoes_staging.py")
        self.assertTrue(plano["anexo_exige_rascunho_existente"])
        self.assertTrue(plano["registros_resultado_sao_projecao_logica"])
        self.assertTrue(plano["dml_exige_binding_lease_fencing_ativo"])
        self.assertEqual(len(plano["registros"]["investigacao_evidencias"]), 1)
        self.assertEqual(len(plano["registros"]["investigacao_alternativas"]), 1)
        self.assertEqual(
            len(plano["registros"]["investigacao_alternativa_evidencias"]), 1,
        )
        self.assertEqual(len(plano["registros"]["investigacao_eventos"]), 1)
        # A entrega só nasce no gatilho atômico de publicação; o dry-run não
        # antecipa nem duplica esse vínculo.
        self.assertEqual(len(plano["registros"]["investigacao_entregas"]), 0)

    def test_alternativas_ambiguas_exibem_pros_contras_e_pendencia_humana(self):
        plano = self.plano(candidatos=[
            {
                "campo": "peso", "valor": 1000, "linhagem": "gta-1",
                "fonte_tipo": "gta", "tipo_correspondencia": "documento_referenciado",
                "evidencia": "Peso informado na GTA.",
            },
            {
                "campo": "peso", "valor": 1100, "linhagem": "nf-1",
                "fonte_tipo": "nf", "tipo_correspondencia": "documento_referenciado",
                "evidencia": "Peso informado na nota.",
            },
        ])
        registros = plano["registros"]
        self.assertEqual(plano["resultado"], "evidencia_insuficiente")
        self.assertEqual(len(registros["investigacao_alternativas"]), 2)
        self.assertEqual(
            {vinculo["papel"] for vinculo in registros["investigacao_alternativa_evidencias"]},
            {"favoravel", "contraria"},
        )
        self.assertIn(
            "decisao_humana",
            {pendencia["campo"] for pendencia in registros["investigacao_pendencias"]},
        )

    def test_multiplos_campos_ambiguos_nao_sao_combinados_sem_versao(self):
        candidatos = [
            {"campo": "peso", "valor": 1000, "linhagem": "a", "tipo_correspondencia": "valor"},
            {"campo": "peso", "valor": 1100, "linhagem": "b", "tipo_correspondencia": "valor"},
            {"campo": "valor_total", "valor": 2000, "linhagem": "a", "tipo_correspondencia": "valor"},
            {"campo": "valor_total", "valor": 2200, "linhagem": "b", "tipo_correspondencia": "valor"},
        ]
        alternativas = self.plano(candidatos=candidatos)["registros"]["investigacao_alternativas"]
        self.assertTrue(alternativas)
        self.assertTrue(all(len(item["campos_snapshot"]) == 1 for item in alternativas))

    def test_documentos_atomicos_preservam_seus_proprios_campos(self):
        candidatos = []
        for versao, chave, quantidade, valor_total in (
            ("Documento A", "doc-a", 10, 2000),
            ("Documento B", "doc-b", 11, 2200),
        ):
            for campo, valor in {
                "data_emissao": "2026-08-01", "numero_nf": chave,
                "quantidade": quantidade, "relacao_negocio": "negocio-a",
                "valor_total": valor_total,
            }.items():
                candidatos.append({
                    "versao": versao, "chave_natural": chave,
                    "campo": campo, "valor": valor,
                    "tipo_correspondencia": "documento_referenciado",
                })
        plano = self.plano(
            assunto=self.assunto_tipo("documento_fiscal"), fontes=[{
            "adaptador": "agronotas", "linhagem": "xml",
            "cobertura": "completa", "consulta": "buscar documentos",
            "candidatos": candidatos,
        }])
        alternativas = plano["registros"]["investigacao_alternativas"]
        snapshots = {item["titulo"]: item["campos_snapshot"] for item in alternativas}
        self.assertEqual(snapshots["Documento A"]["quantidade"], 10)
        self.assertEqual(snapshots["Documento A"]["valor_total"], 2000)
        self.assertEqual(snapshots["Documento B"]["quantidade"], 11)
        self.assertEqual(snapshots["Documento B"]["valor_total"], 2200)
        # Duas versões completas do mesmo documento com valores diferentes são
        # uma divergência explícita, não uma escolha automaticamente segura.
        self.assertEqual(plano["resultado"], "divergente")

    def test_sem_evidencia_e_falhas_de_cobertura_criam_pendencia_tipificada(self):
        vazio = self.plano(assunto=self.assunto_tipo("pesagem"))
        self.assertEqual(vazio["resultado"], "evidencia_insuficiente")
        self.assertFalse(vazio["registros"]["investigacao_alternativas"])
        self.assertEqual(
            {item["campo"] for item in vazio["registros"]["investigacao_pendencias"]},
            {"contexto", "data_folha", "peso_kg"},
        )
        esperados = {
            "indisponivel": "fonte_indisponivel",
            "erro_permanente": "fonte_indisponivel",
            "reautenticacao_necessaria": "reautenticacao",
            "cobertura_incompleta": "cobertura_incompleta",
        }
        for cobertura, tipo in esperados.items():
            plano = self.plano(fontes=[{
                "adaptador": "ima", "consulta": self.consulta,
                "linhagem": "ima-sem-resposta", "cobertura": cobertura,
                "candidatos": [],
            }])
            self.assertEqual(plano["resultado"], "cobertura_incompleta")
            self.assertIn(tipo, {item["tipo"] for item in plano["registros"]["investigacao_pendencias"]})

    def test_valor_nulo_nao_vira_alternativa_e_permanece_pendente(self):
        plano = self.plano(assunto=self.assunto_tipo("pesagem"), candidatos=[{
            "campo": "peso_kg", "valor": None, "linhagem": "gta",
            "tipo_correspondencia": "documento_referenciado",
        }])
        self.assertFalse(plano["registros"]["investigacao_alternativas"])
        self.assertIn(
            "peso_kg",
            {item["campo"] for item in plano["registros"]["investigacao_pendencias"]},
        )

    def test_allowlists_rejeitam_operacional_target_table_e_anexo_sem_rascunho(self):
        plano = self.plano()
        plano["tabelas_controle_planejadas"] = ["compras"]
        with self.assertRaises(ValueError):
            validar_plano(plano)

    def test_validador_rejeita_alternativa_parcial_sem_pendencia_explicita(self):
        plano = self.plano(assunto=self.assunto_tipo("pesagem"), candidatos=[{
            "campo": "data_folha", "valor": "2026-08-01", "linhagem": "nf-a",
            "tipo_correspondencia": "documento_referenciado",
        }])
        plano["registros"]["investigacao_pendencias"] = [
            item for item in plano["registros"]["investigacao_pendencias"]
            if item.get("campo") != "peso_kg"
        ]
        with self.assertRaisesRegex(
            ValueError, "alternativa_parcial_sem_pendencia_explicita"
        ):
            validar_plano(plano)
        plano = self.plano()
        plano["target_table"] = "compras"
        with self.assertRaises(ValueError):
            validar_plano(plano)
        plano = self.plano()
        plano["registros"]["operation_drafts"] = [{"id": "indevido"}]
        with self.assertRaises(ValueError):
            validar_plano(plano)

    def test_chave_natural_igual_com_fato_conflitante_e_rejeitada(self):
        with self.assertRaisesRegex(ValueError, "fatos_evidencia_conflitantes"):
            self.plano(candidatos=[
                {"campo": "peso", "valor": 1000, "linhagem": "gta", "chave_natural": "GTA-1", "tipo_correspondencia": "identificador_exato"},
                {"campo": "peso", "valor": 1100, "linhagem": "gta", "chave_natural": "GTA-1", "tipo_correspondencia": "identificador_exato"},
            ])

    def test_linhagem_declarada_no_fato_nao_substitui_proveniencia_selada(self):
        plano = self.plano(candidatos=[
            {"campo": "peso", "valor": 1000, "linhagem": "gta", "chave_natural": "DOC-1", "tipo_correspondencia": "documento_referenciado"},
            {"campo": "valor_total", "valor": 2000, "linhagem": "telegram", "chave_natural": "DOC-1", "tipo_correspondencia": "documento_referenciado"},
        ])
        evidencias = plano["registros"]["investigacao_evidencias"]
        self.assertEqual(len(evidencias), 1)
        self.assertEqual(len({item["linhagem"] for item in evidencias}), 1)

    def test_multifonte_cria_uma_tarefa_por_adaptador_e_preserva_sucesso_parcial(self):
        plano = self.plano(fontes=[
            {"adaptador": "ima", "cobertura": "completa", "candidatos": [
                {"campo": "gta", "valor": "GTA-1", "linhagem": "ima", "tipo_correspondencia": "identificador_exato"},
            ]},
            {"adaptador": "wey", "cobertura": "cobertura_incompleta", "candidatos": []},
        ])
        tarefas = plano["registros"]["investigacao_tarefas"]
        self.assertEqual({item["adaptador"] for item in tarefas}, {"ima", "wey", "sintese"})
        self.assertEqual(plano["resultado"], "cobertura_incompleta")
        self.assertEqual(len(plano["registros"]["investigacao_evidencias"]), 1)
        plano = self.plano()
        plano["registros"]["investigacao_evidencias"].append({
            "metadados": {"tabela_destino": "compras"},
        })
        with self.assertRaises(ValueError):
            validar_plano(plano)

    def test_tarefa_preserva_consulta_canonica_para_retomada_apos_crash(self):
        plano = self.plano(fontes=[{
            "adaptador": "ima",
            "consulta": {
                "tipo": "ficha_sanitaria",
                "pergunta": "localizar gta",
                "campos": ["gta", "data"],
                "janela_inicio": "2026-08-01",
                "janela_fim": "2026-08-29",
                "limite": 50,
            },
            "cobertura": "completa",
        }])
        tarefa = next(
            item for item in plano["registros"]["investigacao_tarefas"]
            if item["adaptador"] == "ima"
        )
        self.assertEqual(tarefa["consulta_schema_version"], "consulta-v1")
        self.assertTrue(tarefa["consulta_ref"].startswith("qref_"))
        self.assertEqual(tarefa["consulta_spec"]["limite"], 50)
        self.assertEqual(tarefa["consulta_spec"]["janela_inicio"], "2026-08-01")
        self.assertRegex(tarefa["consulta_hash"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            json.loads(tarefa["consulta_canonico"]), tarefa["consulta_spec"]
        )
        self.assertEqual(
            tarefa["consulta_ref"], f"qref_{tarefa['consulta_hash'][:32]}"
        )
        self.assertEqual(resolver_consulta_tarefa(tarefa), tarefa["consulta_spec"])
        repetida = self.plano(fontes=[{
            "adaptador": "ima",
            "consulta": dict(tarefa["consulta_spec"]),
            "cobertura": "completa",
        }])
        tarefa_repetida = next(
            item for item in repetida["registros"]["investigacao_tarefas"]
            if item["adaptador"] == "ima"
        )
        self.assertEqual(tarefa["consulta_ref"], tarefa_repetida["consulta_ref"])
        self.assertEqual(tarefa["consulta_hash"], tarefa_repetida["consulta_hash"])
        adulterada = dict(tarefa)
        adulterada["consulta_spec"] = dict(tarefa["consulta_spec"], limite=51)
        with self.assertRaisesRegex(ValueError, "consulta_canonico_divergente"):
            resolver_consulta_tarefa(adulterada)

    def test_sanitizacao_publica_protege_documento_numerico(self):
        plano = self.plano(candidatos=[{
            "campo": "referencia_documento",
            "valor": 12345678901,
            "linhagem": "ofx",
            "tipo_correspondencia": "valor",
        }])
        fatos = plano["registros"]["investigacao_evidencias"][0][
            "fatos_normalizados"
        ]
        self.assertEqual(fatos["referencia_documento"], "[DOCUMENTO PROTEGIDO]")
        linhagem = plano["registros"]["investigacao_evidencias"][0]["linhagem"]
        self.assertRegex(linhagem, r"^lin_[0-9a-f]{32}$")

    def test_grupo_externo_conflitante_preserva_todas_as_opcoes(self):
        plano = self.plano(candidatos=[
            {
                "campo": "quantidade", "valor": 10, "linhagem": "fonte-a",
                "grupo_correlacao": "grupo-g", "tipo_correspondencia": "valor",
            },
            {
                "campo": "quantidade", "valor": 11, "linhagem": "fonte-b",
                "grupo_correlacao": "grupo-g", "tipo_correspondencia": "valor",
            },
        ])
        alternativas = plano["registros"]["investigacao_alternativas"]
        self.assertEqual(
            {item["campos_snapshot"]["quantidade"] for item in alternativas},
            {10, 11},
        )
        self.assertEqual(plano["resultado"], "evidencia_insuficiente")
        self.assertTrue(all(item["classificacao"] in {"ambiguo", "possivel"} for item in alternativas))

    def test_fonte_completa_sem_resultado_vira_vazio_com_cobertura(self):
        plano = self.plano(candidatos=[])
        fonte = next(
            item for item in plano["registros"]["investigacao_tarefas"]
            if item["adaptador"] != "sintese"
        )
        sintese = next(
            item for item in plano["registros"]["investigacao_tarefas"]
            if item["adaptador"] == "sintese"
        )
        self.assertEqual(fonte["estado_cobertura"], "vazio_com_cobertura")
        self.assertEqual(sintese["estado_cobertura"], "vazio_com_cobertura")

    def test_sintese_recebe_alternativas_e_pendencias_e_fontes_ficam_com_evidencias(self):
        plano = self.plano(assunto=self.assunto_tipo("pesagem"), candidatos=[
            {
                "campo": "peso_kg", "valor": 1000, "linhagem": "ima",
                "tipo_correspondencia": "documento_referenciado",
            },
        ])
        tarefas = plano["registros"]["investigacao_tarefas"]
        sintese_id = next(item["id"] for item in tarefas if item["adaptador"] == "sintese")
        self.assertTrue(all(item["tarefa_id"] != sintese_id for item in plano["registros"]["investigacao_evidencias"]))
        self.assertTrue(all(item["tarefa_id"] == sintese_id for item in plano["registros"]["investigacao_alternativas"]))
        self.assertTrue(all(item["tarefa_id"] == sintese_id for item in plano["registros"]["investigacao_pendencias"]))

    def test_chave_da_tarefa_fonte_nao_muda_quando_cobertura_muda(self):
        base = self.plano(fontes=[{"adaptador": "ima", "consulta": "ficha", "linhagem": "fazenda-a", "cobertura": "completa"}])
        alterado = self.plano(fontes=[{"adaptador": "ima", "consulta": "ficha", "linhagem": "fazenda-a", "cobertura": "cobertura_incompleta"}])
        chave_base = next(item["chave_idempotencia"] for item in base["registros"]["investigacao_tarefas"] if item["adaptador"] == "ima")
        chave_alterada = next(item["chave_idempotencia"] for item in alterado["registros"]["investigacao_tarefas"] if item["adaptador"] == "ima")
        self.assertEqual(chave_base, chave_alterada)

    def test_candidato_herda_metadados_da_fonte_sem_rotulo_errado(self):
        plano = self.plano(fontes=[
            {"adaptador": "telegram", "linhagem": "grupo-a", "proveniencia": "versao-a", "consulta": "a", "candidatos": [{"campo": "peso", "valor": 1000, "tipo_correspondencia": "ocr"}]},
            {"adaptador": "telegram", "linhagem": "grupo-b", "proveniencia": "versao-b", "consulta": "b", "candidatos": [{"campo": "peso", "valor": 1100, "tipo_correspondencia": "ocr"}]},
        ])
        evidencias = plano["registros"]["investigacao_evidencias"]
        linhagens = {item["linhagem"] for item in evidencias}
        self.assertEqual(len(linhagens), 2)
        self.assertTrue(all(re.fullmatch(r"lin_[0-9a-f]{32}", item) for item in linhagens))
        self.assertFalse({"grupo-a", "grupo-b"} & linhagens)
        self.assertEqual({item["fonte_tipo"] for item in evidencias}, {"telegram"})
        self.assertEqual(len({item["tarefa_id"] for item in evidencias}), 2)

    def test_multiplas_fontes_do_mesmo_adaptador_usam_chave_de_fonte(self):
        plano = self.plano(fontes=[
            {"adaptador": "telegram", "linhagem": "grupo-a", "consulta": "compra A", "candidatos": [
                {"campo": "peso", "valor": 1000, "linhagem": "grupo-a", "tipo_correspondencia": "ocr"},
            ]},
            {"adaptador": "telegram", "linhagem": "grupo-b", "consulta": "compra B", "candidatos": [
                {"campo": "peso", "valor": 1100, "linhagem": "grupo-b", "tipo_correspondencia": "ocr"},
            ]},
        ])
        tarefas = plano["registros"]["investigacao_tarefas"]
        self.assertEqual(len(tarefas), 3)
        self.assertEqual({item["adaptador"] for item in tarefas}, {"telegram", "sintese"})
        self.assertEqual(
            {item["tarefa_id"] for item in plano["registros"]["investigacao_evidencias"]},
            {item["id"] for item in tarefas if item["adaptador"] != "sintese"},
        )

    def test_vinculos_de_origem_sao_exclusivos_e_grupo_exige_todos_os_timestamps(self):
        draft_id = "11111111-1111-4111-8111-111111111111"
        negocio_id = "22222222-2222-4222-8222-222222222222"
        outro_negocio_id = "33333333-3333-4333-8333-333333333333"
        plano_draft = self.plano(
            source_draft_id=draft_id,
            source_draft_atualizado_em="2026-08-29T11:00:00Z",
        )
        registro_draft = plano_draft["registros"]["investigacoes_revisao"][0]
        self.assertEqual(registro_draft["source_draft_id"], draft_id)
        self.assertEqual(
            registro_draft["source_draft_atualizado_em"],
            "2026-08-29T11:00:00Z",
        )

        plano = self.plano(
            negocio_candidato_ids=[outro_negocio_id, negocio_id],
            source_candidatos_atualizados_em={
                negocio_id: "2026-08-29T10:00:00Z",
                outro_negocio_id: "2026-08-29T10:05:00Z",
            },
        )
        registro = plano["registros"]["investigacoes_revisao"][0]
        self.assertIsNone(registro["source_draft_id"])
        self.assertEqual(registro["negocio_candidato_id"], negocio_id)
        self.assertEqual(registro["negocio_candidato_ids"], [negocio_id, outro_negocio_id])
        self.assertEqual(
            registro["source_candidatos_atualizados_em"], {
                negocio_id: "2026-08-29T10:00:00Z",
                outro_negocio_id: "2026-08-29T10:05:00Z",
            },
        )
        with self.assertRaisesRegex(ValueError, "source_draft_timestamp_obrigatorio"):
            self.plano(source_draft_id=draft_id)
        with self.assertRaisesRegex(ValueError, "negocio_candidato_id_invalido"):
            self.plano(negocio_candidato_id="nao-uuid")
        with self.assertRaisesRegex(ValueError, "source_candidatos_timestamps_obrigatorios"):
            self.plano(negocio_candidato_id=negocio_id)
        with self.assertRaisesRegex(ValueError, "origem_mista_draft_candidato_bloqueada"):
            self.plano(
                source_draft_id=draft_id,
                source_draft_atualizado_em="2026-08-29T11:00:00Z",
                negocio_candidato_id=negocio_id,
                source_candidato_atualizado_em="2026-08-29T10:00:00Z",
            )

    def test_identidade_muda_com_vinculo_e_preserva_equivalencias(self):
        draft_a = "11111111-1111-4111-8111-111111111111"
        draft_b = "22222222-2222-4222-8222-222222222222"
        def id_plano(**kwargs):
            return self.plano(**kwargs)["registros"]["investigacoes_revisao"][0]["id"]

        base_a = id_plano(
            source_draft_id=draft_a,
            source_draft_atualizado_em="2026-08-29T11:00:00Z",
        )
        self.assertNotEqual(base_a, id_plano(
            source_draft_id=draft_b,
            source_draft_atualizado_em="2026-08-29T11:00:00Z",
        ))
        self.assertNotEqual(base_a, id_plano(
            source_draft_id=draft_a,
            source_draft_atualizado_em="2026-08-29T11:00:01Z",
        ))

        candidato_a = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        candidato_b = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        timestamps = {
            candidato_a: "2026-08-29T10:00:00Z",
            candidato_b: "2026-08-29T10:05:00Z",
        }
        grupo = id_plano(
            negocio_candidato_ids=[candidato_b, candidato_a],
            source_candidatos_atualizados_em=dict(reversed(list(timestamps.items()))),
        )
        self.assertEqual(grupo, id_plano(
            negocio_candidato_ids=[candidato_a.upper(), candidato_b],
            source_candidatos_atualizados_em={
                candidato_b: "2026-08-29T10:05:00+00:00",
                candidato_a.upper(): "2026-08-29T10:00:00+00:00",
            },
        ))
        self.assertNotEqual(grupo, id_plano(
            negocio_candidato_ids=[candidato_a, candidato_b],
            source_candidatos_atualizados_em={
                candidato_a: "2026-08-29T10:00:01Z",
                candidato_b: "2026-08-29T10:05:00Z",
            },
        ))
        self.assertNotEqual(grupo, id_plano(
            negocio_candidato_ids=[candidato_a],
            source_candidatos_atualizados_em={
                candidato_a: "2026-08-29T10:00:00Z",
            },
        ))

    def test_correlacao_externa_nao_une_linhagens_sem_prova(self):
        sem_correlacao = self.plano(
            assunto=self.assunto_tipo("documento_fiscal"), candidatos=[
            {"campo": "numero_nf", "valor": "G1", "linhagem": "ima-1", "tipo_correspondencia": "documento_referenciado"},
            {"campo": "valor_total", "valor": 100, "linhagem": "ofx-1", "tipo_correspondencia": "valor_data"},
        ])
        self.assertEqual(sem_correlacao["resultado"], "evidencia_insuficiente")
        self.assertTrue(sem_correlacao["registros"]["investigacao_pendencias"])

        com_correlacao = self.plano(
            assunto=self.assunto_tipo("documento_fiscal"), candidatos=[
            {"grupo_correlacao": "negocio-a", "campo": "numero_nf", "valor": "G1", "linhagem": "ima-1", "tipo_correspondencia": "documento_referenciado"},
            {"grupo_correlacao": "negocio-a", "campo": "valor_total", "valor": 100, "linhagem": "ofx-1", "tipo_correspondencia": "valor_data"},
        ])
        self.assertEqual(com_correlacao["resultado"], "evidencia_insuficiente")
        alternativas = com_correlacao["registros"]["investigacao_alternativas"]
        self.assertEqual(len(alternativas), 2)
        self.assertEqual(
            {frozenset(item["campos_snapshot"]) for item in alternativas},
            {frozenset({"numero_nf"}), frozenset({"valor_total"})},
        )
        self.assertTrue(all(item["classificacao"] == "ambiguo" for item in alternativas))
        self.assertTrue(all(item["confianca_geral"] == 0 for item in alternativas))
        # Um rótulo externo não serve como prova de correlação. A selagem
        # mantém as duas pistas separadas; o rótulo não aumenta confiança.
        self.assertTrue(all(
            item["classificacao"] in {"inconclusivo", "possivel", "provavel"}
            for alternativa in alternativas
            for item in alternativa["confianca_campos"].values()
        ))

    def test_grupo_explicito_nao_descarta_pista_solta_nem_publica_versao_parcial(self):
        plano = self.plano(
            assunto=self.assunto_tipo("documento_fiscal"), candidatos=[
            {
                "grupo_correlacao": "negocio-a", "campo": "quantidade",
                "valor": 10, "linhagem": "gta-a",
                "tipo_correspondencia": "documento_referenciado",
            },
            {
                "campo": "valor_total", "valor": 100,
                "linhagem": "ofx-a", "tipo_correspondencia": "valor_data",
            },
        ])
        self.assertEqual(plano["resultado"], "evidencia_insuficiente")
        snapshots = [
            item["campos_snapshot"]
            for item in plano["registros"]["investigacao_alternativas"]
        ]
        self.assertIn({"quantidade": 10}, snapshots)
        self.assertIn({"valor_total": 100}, snapshots)
        campos_pendentes = {
            item["campo"] for item in plano["registros"]["investigacao_pendencias"]
            if item.get("campo")
        }
        self.assertTrue({"quantidade", "valor_total"}.issubset(campos_pendentes))

    def test_valor_vazio_em_grupo_explicito_vira_pendencia_sem_quebrar(self):
        plano = self.plano(
            assunto=self.assunto_tipo("documento_fiscal"), candidatos=[{
            "grupo_correlacao": "negocio-a", "campo": "quantidade",
            "valor": None, "linhagem": "gta-a",
            "tipo_correspondencia": "documento_referenciado",
        }])
        self.assertEqual(plano["resultado"], "evidencia_insuficiente")
        self.assertEqual(plano["registros"]["investigacao_alternativas"], [])
        self.assertTrue({
            "data_emissao", "numero_nf", "relacao_negocio", "valor_total",
        }.issubset({
            item["campo"] for item in plano["registros"]["investigacao_pendencias"]
        }))

    def test_pista_divergente_fora_do_grupo_permanece_como_alternativa(self):
        plano = self.plano(
            assunto=self.assunto_tipo("documento_fiscal"), candidatos=[
            {
                "grupo_correlacao": "negocio-a", "campo": "quantidade",
                "valor": 10, "linhagem": "gta-a",
                "tipo_correspondencia": "documento_referenciado",
            },
            {
                "campo": "quantidade", "valor": 11,
                "linhagem": "telegram-a", "tipo_correspondencia": "mensagem_contextual",
            },
        ])
        self.assertEqual(plano["resultado"], "evidencia_insuficiente")
        self.assertEqual(
            {item["campos_snapshot"]["quantidade"] for item in plano["registros"]["investigacao_alternativas"]},
            {10, 11},
        )
        self.assertTrue({
            "data_emissao", "numero_nf", "relacao_negocio", "valor_total",
        }.issubset({
            item["campo"] for item in plano["registros"]["investigacao_pendencias"]
        }))

    def test_retratos_iguais_nao_duplicam_alternativa_e_resultados_usam_ids_logicos(self):
        plano = self.plano(assunto=self.assunto_tipo("pesagem"), candidatos=[
            {"versao": "a", "campo": "peso_kg", "valor": 1000, "linhagem": "gta-a", "tipo_correspondencia": "documento_referenciado"},
            {"versao": "b", "campo": "peso_kg", "valor": 1000, "linhagem": "gta-b", "tipo_correspondencia": "documento_referenciado"},
        ])
        alternativas = plano["registros"]["investigacao_alternativas"]
        self.assertEqual(plano["resultado"], "evidencia_insuficiente")
        self.assertEqual(len(alternativas), 1)
        for tabela in ("investigacao_evidencias", "investigacao_alternativas", "investigacao_pendencias"):
            for item in plano["registros"][tabela]:
                self.assertIn("id_logico", item)
                self.assertNotIn("id", item)
        for ligacao in plano["registros"]["investigacao_alternativa_evidencias"]:
            self.assertIn("alternativa_id_logico", ligacao)
            self.assertIn("evidencia_id_logico", ligacao)
            self.assertIn("evidencia_tarefa_id", ligacao)

    def test_uma_divergencia_sem_contraponto_fica_incompleta(self):
        plano = self.plano(candidatos=[{
            "campo": "valor_total", "valor": 1, "linhagem": "ofx",
            "tipo_correspondencia": "valor", "divergencia_central": True,
        }])
        self.assertEqual(plano["resultado"], "evidencia_insuficiente")
        self.assertTrue(plano["registros"]["investigacao_pendencias"])

    def test_duas_falhas_do_mesmo_adaptador_tem_pendencias_distintas(self):
        plano = self.plano(fontes=[
            {"adaptador": "telegram", "linhagem": "grupo-a", "consulta": "a", "cobertura": "indisponivel"},
            {"adaptador": "telegram", "linhagem": "grupo-b", "consulta": "b", "cobertura": "indisponivel"},
        ])
        pendencias = plano["registros"]["investigacao_pendencias"]
        self.assertEqual(len({item["chave_idempotencia"] for item in pendencias}), len(pendencias))

    def test_fingerprint_atual_precisa_ser_hash_valido(self):
        with self.assertRaisesRegex(ValueError, "fingerprint_atual_invalido"):
            self.plano(fingerprint_atual="invalido")

    def test_campo_tecnico_nao_pode_virar_evidencia_publicavel(self):
        with self.assertRaisesRegex(ValueError, "campo_evidencia_proibido"):
            self.plano(candidatos=[{
                "campo": "origem_mensagem_id", "valor": "mensagem-privada",
                "linhagem": "telegram", "tipo_correspondencia": "identificador_exato",
            }])

    def test_sanitizacao_e_recursiva(self):
        bruto = {
            "ok": 1,
            "api_key": "segredo",
            "aninhado": {"authorization": "segredo", "valor": 2},
            "lista": [{"mensagem_bruta": "privada", "resumo": "permitido"}],
        }
        self.assertEqual(sanitizar_payload(bruto), {
            "ok": 1,
            "aninhado": {"valor": 2},
            "lista": [{"resumo": "permitido"}],
        })
        self.assertEqual(sanitizar_payload({"sênha": "segredo"}), {})
        self.assertEqual(
            sanitizar_payload({"documento": 12345678901}),
            {"documento": "[DOCUMENTO PROTEGIDO]"},
        )
        texto = sanitizar_payload({
            "resumo": "Bearer segredo-secreto contato@exemplo.com 12345678901",
        })["resumo"]
        self.assertNotIn("segredo-secreto", texto)
        self.assertNotIn("contato@exemplo.com", texto)
        self.assertNotIn("12345678901", texto)

    def test_sanitizacao_red_team_protege_documentos_contatos_ids_e_chaves(self):
        identificador = "123e4567-e89b-42d3-a456-426614174000"
        identificadores_atipicos = (
            "019535d9-3df7-7d2b-8c4d-ffeeddccbbaa",
            "00000000-0000-0000-0000-000000000000",
            "01234567-89ab-cdef-0123-456789abcdef",
        )
        bruto = {
            "x-api-key": "segredo",
            "aninhado": {"service-role-key": "segredo", "ok": "sim"},
            "operacao_id": identificador,
            "resumo": (
                "CPF 123.456.789-00 CNPJ 12.345.678/0001-90 "
                "fone (62) 99999-1234 x-api-key=nao-vazar "
                + " ".join((identificador, *identificadores_atipicos))
            ),
        }
        protegido = sanitizar_payload(bruto, proteger_identificadores=True)
        self.assertNotIn("x-api-key", protegido)
        self.assertNotIn("service-role-key", protegido["aninhado"])
        self.assertNotIn("operacao_id", protegido)
        texto = protegido["resumo"]
        for segredo in (
            "123.456.789-00", "12.345.678/0001-90", "(62) 99999-1234",
            "nao-vazar", identificador, *identificadores_atipicos,
        ):
            self.assertNotIn(segredo, texto)
        for embutido in (
            f"prefix_{identificador}_suffix",
            f"hexA{identificador}f",
            f"({identificador})",
        ):
            self.assertNotIn(
                identificador,
                sanitizar_payload(
                    embutido, proteger_identificadores=True,
                ),
            )
        with self.assertRaisesRegex(ValueError, "campo_evidencia_proibido"):
            self.plano(candidatos=[{
                "campo": "fornecedor_id", "valor": identificador,
                "linhagem": "nf", "tipo_correspondencia": "identificador_exato",
            }])

    def test_fonte_declarada_vazia_nao_pode_conter_resultado(self):
        with self.assertRaisesRegex(ValueError, "fonte_vazia_com_resultados_invalida"):
            self.plano(fontes=[{
                "adaptador": "ima", "cobertura": "vazio_com_cobertura",
                "consulta": "buscar gta", "candidatos": [{
                    "campo": "gta", "valor": "G1", "linhagem": "ima-a",
                    "tipo_correspondencia": "identificador_exato",
                }],
            }])

    def test_contraprova_nao_selada_nao_fabrica_divergencia_estruturada(self):
        confianca = confianca_explicavel([
            {
                "campo": "gta", "valor": "G1", "linhagem": "ima-a",
                "tipo_correspondencia": "identificador_exato",
                "_chave_fonte": "fonte-a", "_adaptador": "ima",
            },
            {
                "campo": "gta", "valor": "G2", "linhagem": "nfe-b",
                "tipo_correspondencia": "identificador_exato",
                "_chave_fonte": "fonte-b", "_adaptador": "agronotas",
            },
        ], cobertura_por_fonte={"fonte-a": "completa", "fonte-b": "completa"})
        alternativas = confianca["gta"]["alternativas"]
        # Rótulos de adaptador e cobertura injetados diretamente não são um
        # atestado: sem identidade selada, as opções ficam como pistas e não
        # podem produzir um selo de divergência estrutural.
        self.assertTrue(all(
            alternativa["classificacao"] == "possivel"
            and alternativa["confianca"] == 0.35
            and "divergencia_central" not in alternativa["penalidades"]
            for alternativa in alternativas
        ))

    def test_mesma_evidencia_em_tarefas_distintas_preserva_fencing_fisico(self):
        plano = self.plano(assunto=self.assunto_tipo("documento_fiscal"), fontes=[
            {
                "adaptador": "ima", "linhagem": "documento-a",
                "cobertura": "completa", "consulta": "buscar gta a",
                "candidatos": [{
                    "campo": "numero_nf", "valor": "G1",
                    "chave_natural": "documento-compartilhado",
                    "tipo_correspondencia": "documento_referenciado",
                }],
            },
            {
                "adaptador": "agronotas", "linhagem": "documento-a",
                "cobertura": "completa", "consulta": "buscar gta b",
                "candidatos": [{
                    "campo": "numero_nf", "valor": "G1",
                    "chave_natural": "documento-compartilhado",
                    "tipo_correspondencia": "documento_referenciado",
                }],
            },
        ])
        evidencias = plano["registros"]["investigacao_evidencias"]
        self.assertEqual(len(evidencias), 2)
        self.assertEqual(len({item["tarefa_id"] for item in evidencias}), 2)
        self.assertEqual(len({item["id_logico"] for item in evidencias}), 2)
        # Mesmo rótulo externo em tarefas distintas não prova que seja o mesmo
        # registro físico; cada envelope mantém sua âncora de origem própria.
        self.assertEqual(len({item["chave_natural_hash"] for item in evidencias}), 2)

    def test_ambiguidades_exatas_nao_podem_receber_selo_forte(self):
        confianca = confianca_explicavel([
            {
                "campo": "gta", "valor": "G1", "linhagem": "ima-a",
                "tipo_correspondencia": "identificador_exato",
                "_chave_fonte": "fonte-a",
            },
            {
                "campo": "gta", "valor": "G1", "linhagem": "nfe-a",
                "tipo_correspondencia": "identificador_exato",
                "_chave_fonte": "fonte-b",
            },
            {
                "campo": "gta", "valor": "G2", "linhagem": "ima-b",
                "tipo_correspondencia": "identificador_exato",
                "_chave_fonte": "fonte-c",
            },
            {
                "campo": "gta", "valor": "G2", "linhagem": "nfe-b",
                "tipo_correspondencia": "identificador_exato",
                "_chave_fonte": "fonte-d",
            },
        ], cobertura_por_fonte={
            "fonte-a": "completa", "fonte-b": "completa",
            "fonte-c": "completa", "fonte-d": "completa",
        })
        alternativas = confianca["gta"]["alternativas"]
        self.assertEqual({item["classificacao"] for item in alternativas}, {"possivel"})
        self.assertTrue(all("ambiguidade_no_campo" in item["caps"] for item in alternativas))

    def test_ordem_de_lista_faz_parte_da_identidade(self):
        self.assertNotEqual(
            chave_estavel("ordem", ["primeiro", "segundo"]),
            chave_estavel("ordem", ["segundo", "primeiro"]),
        )

    def test_selo_interno_do_correlator_nao_pode_vir_do_adaptador(self):
        with self.assertRaisesRegex(ValueError, "metadado_reservado_ao_mediador"):
            self.plano(candidatos=[{
                "campo": "peso", "valor": 1000,
                "_correlator_grupo_verificado": True,
            }])


if __name__ == "__main__":
    unittest.main()
