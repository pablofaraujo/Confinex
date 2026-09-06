#!/usr/bin/env python3
"""Testes offline do contrato de complemento de comissão do Juan."""

from __future__ import annotations

import copy
import ast
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import complemento_comissao_juan as modulo


SEGREDO = b"segredo-ficticio-com-mais-de-trinta-e-dois-bytes"
GRUPO = "-700001"
ID_DRAFT = "11111111-1111-4111-8111-111111111111"
ID_PENDING = "22222222-2222-4222-8222-222222222222"
ID_OUTRO = "33333333-3333-4333-8333-333333333333"


def identidade(**alteracoes):
    base = {
        "canal": "telegram", "agente": "juan", "grupo_id": GRUPO,
        "autor_id": "12345", "mensagem_id": "67890", "topico_id": None,
        "autor_bot": False, "encaminhada": False,
    }
    base.update(alteracoes)
    return base


def rascunho(**alteracoes):
    base = {
        "id": ID_DRAFT, "pending_action_id": ID_PENDING, "tipo_operacao": "compra",
        "status": "em_revisao", "atualizado_em": "2026-09-06T10:00:00Z",
        "origem_canal": "telegram", "origem_conversa_id": GRUPO,
        "contexto_canonico": f"telegram:grupo:{GRUPO}", "escopo": "grupo",
        "entidade_final_id": None, "revisao_tipo": "pre_revisao",
        "dados_extraidos": {"valor_total": "1000.00", "preco_arroba": "300.00", "status_confirmacao": "pendente"},
    }
    base.update(alteracoes)
    return base


def pendencia(**alteracoes):
    base = {
        "id": ID_PENDING, "status": "aguardando_confirmacao", "atualizado_em": "2026-09-06T10:00:00Z",
        "origem_canal": "telegram", "origem_conversa_id": GRUPO,
        "contexto_canonico": f"telegram:grupo:{GRUPO}", "escopo": "grupo",
        "entidade_tipo": "operation_draft", "entidade_id": ID_DRAFT,
        "acao_tipo": "revisar_compra", "payload": {"dados_extraidos": {"valor_total": "1000.00", "preco_arroba": "300.00", "status_confirmacao": "pendente"}},
        "resultado": {},
    }
    base.update(alteracoes)
    return base


def plano_valido(**kwargs):
    args = {"identidade": identidade(), "percentual": "2.5000", "beneficiario": "Corretor Fictício", "agora": 100}
    args.update(kwargs)
    return modulo.preparar_comissao(rascunho(), pendencia(), **args)


class ComplementoComissaoTestCase(unittest.TestCase):
    def test_modulo_nao_tem_imports_de_rede_banco_execucao_ou_arquivos(self):
        arvore = ast.parse(Path(modulo.__file__).read_text())
        imports = {n.module for n in ast.walk(arvore) if isinstance(n, ast.ImportFrom)}
        imports.update(a.name for n in ast.walk(arvore) if isinstance(n, ast.Import) for a in n.names)
        self.assertEqual(imports, {'__future__', 'copy', 'hashlib', 'hmac', 'json', 're', 'decimal'})
        chamadas = {n.func.id for n in ast.walk(arvore) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        self.assertFalse(chamadas & {'open', 'exec', 'eval', '__import__'})

    def test_previa_identifica_negocio_sem_expor_referencia_tecnica(self):
        d = rascunho(codigo_sugerido='NEG-26-999', contexto_nome='Grupo sintético')
        d['dados_extraidos'].update(fornecedor='Fornecedor fictício', data='2026-09-06', cabecas=3, categoria='Vacas')
        p = pendencia(payload={'dados_extraidos': copy.deepcopy(d['dados_extraidos'])})
        plano = modulo.preparar_comissao(d,p,identidade=identidade(),percentual='1',beneficiario='AB',agora=100)
        resumo = modulo.resumo_previa(plano)
        for valor in ('NEG-26-999', 'Grupo sintético', 'Fornecedor fictício', '06/09/2026', 'Cabeças: 3', 'Vacas'):
            self.assertIn(valor, resumo)
        for valor in (GRUPO, ID_DRAFT, ID_PENDING, 'telegram:'):
            self.assertNotIn(valor, resumo)

    def test_paridade_decimais_null_e_links_incompletos(self):
        dados = {'valor_total':'1000.000'}
        plano = modulo.preparar_comissao(rascunho(dados_extraidos=dados),pendencia(payload={'dados_extraidos':dados}),
            identidade=identidade(),percentual='1.00000',beneficiario='AB',agora=100)
        self.assertEqual(plano['comissao']['percentual'], '1.0000')
        self.assertEqual(plano['comissao']['valor'], '10.00')
        for dado in ({**dados,'comissao':None}, {'valor_total':'1E3'}):
            with self.subTest(dado=dado), self.assertRaises(modulo.ComplementoRecusado):
                modulo.preparar_comissao(rascunho(dados_extraidos=dado),pendencia(payload={'dados_extraidos':dado}),
                    identidade=identidade(),percentual='1',beneficiario='AB',agora=100)
        for chave in ('source_draft_id','operation_draft_id'):
            with self.subTest(chave=chave), self.assertRaises(modulo.ComplementoRecusado):
                modulo.preparar_comissao(rascunho(),pendencia(payload={'dados_extraidos':rascunho()['dados_extraidos'],chave:None}),
                    identidade=identidade(),percentual='1',beneficiario='AB',agora=100)
        with self.assertRaises(modulo.ComplementoRecusado):
            plano_valido(identidade=identidade(grupo_id=-700001))

    def test_assinador_nao_sela_plano_arbitrario_mesmo_com_chave_valida(self):
        for plano in (None, {}, {**plano_valido(),'expira_em_epoch':100000}, {**plano_valido(),'plano_id':'a'*64}):
            with self.subTest(plano_tipo=type(plano).__name__), self.assertRaises(modulo.ComplementoRecusado):
                modulo.assinar_previa(plano, SEGREDO)

    def test_seleciona_unico_e_exige_escolha_explicita_na_ambiguidade(self):
        unico = {"id": ID_DRAFT, "nome": "Fornecedor Fictício"}
        self.assertEqual(modulo.selecionar_candidato([unico]), unico)
        outro = {"id": ID_OUTRO, "nome": "Fornecedor Fictício"}
        with self.assertRaises(modulo.ComplementoRecusado):
            modulo.selecionar_candidato([unico, outro])
        self.assertEqual(modulo.selecionar_candidato([unico, outro], ID_OUTRO)["id"], ID_OUTRO)

    def test_mesmo_nome_de_outro_grupo_nao_substitui_escolha(self):
        candidatos = [{"id": ID_DRAFT, "grupo_id": GRUPO, "nome": "Fornecedor Fictício"},
                      {"id": ID_OUTRO, "grupo_id": "-700002", "nome": "Fornecedor Fictício"}]
        with self.assertRaises(modulo.ComplementoRecusado):
            modulo.selecionar_candidato(candidatos)
        self.assertEqual(modulo.selecionar_candidato(candidatos, ID_DRAFT)["grupo_id"], GRUPO)

    def test_prepara_previa_preserva_preco_original_e_calcula_comissao(self):
        original = rascunho()
        antes = copy.deepcopy(original)
        plano = plano_valido()
        self.assertEqual(plano["comissao"]["valor"], "25.00")
        self.assertEqual(plano["comissao"]["base_vendedor"], "1000.00")
        self.assertEqual(original, antes)
        self.assertEqual(plano["rascunho"]["dados_extraidos"]["preco_arroba"], "300.00")
        self.assertIn("O valor do vendedor não muda", modulo.resumo_previa(plano))

    def test_comissao_existente_e_substituida_sem_acumular(self):
        dados = {"valor_total": "1000.00", "preco_arroba": "300.00", "comissao": {"percentual": "1.00", "valor": "10.00"}}
        plano = plano_valido()
        plano = modulo.preparar_comissao({**rascunho(), "dados_extraidos": dados}, {**pendencia(), "payload": {"dados_extraidos": dados}}, identidade=identidade(), percentual="2.0000", beneficiario="Corretor Fictício", agora=100)
        self.assertEqual(plano["comissao"]["valor"], "20.00")
        self.assertIn("será substituída, não somada", modulo.resumo_previa(plano))
        self.assertIn("Total com comissão: R$ 1.020,00", modulo.resumo_previa(plano))

    def test_meio_centavo_arredonda_para_cima(self):
        dados = {"valor_total": "1.00"}
        p = modulo.preparar_comissao({**rascunho(), "dados_extraidos": dados}, {**pendencia(), "payload": {"dados_extraidos": dados}}, identidade=identidade(), percentual="0.5000", beneficiario="AB", agora=100)
        self.assertEqual(p["comissao"]["valor"], "0.01")

    def test_percentual_base_invalidos_nan_inf_bool_e_casas_excedentes(self):
        for percentual in (True, False, "NaN", "Infinity", "-1", "0", "100.0001", "1.23456"):
            with self.subTest(percentual=percentual), self.assertRaises(modulo.ComplementoRecusado):
                plano_valido(percentual=percentual)
        for base in (True, "NaN", "Infinity", "-1", "0", "1.001"):
            dados = {"valor_total": base}
            with self.subTest(base=base), self.assertRaises(modulo.ComplementoRecusado):
                modulo.preparar_comissao({**rascunho(), "dados_extraidos": dados}, {**pendencia(), "payload": {"dados_extraidos": dados}}, identidade=identidade(), percentual="1.0000", beneficiario="AB", agora=100)

    def test_vinculo_status_payload_e_identidade_contraditorios_sao_recusados(self):
        casos = [
            ({"pending_action_id": ID_OUTRO}, {}),
            ({"entidade_final_id": ID_OUTRO}, {}),
            ({"status": "realizado"}, {}),
            ({}, {"entidade_tipo": "compras"}),
            ({}, {"payload": {"dados_extraidos": rascunho()["dados_extraidos"], "target_table": "compras"}}),
        ]
        for draft_changes, pending_changes in casos:
            with self.subTest(draft_changes=draft_changes, pending_changes=pending_changes), self.assertRaises(modulo.ComplementoRecusado):
                modulo.preparar_comissao({**rascunho(), **draft_changes}, {**pendencia(), **pending_changes}, identidade=identidade(), percentual="1.0000", beneficiario="AB", agora=100)
        with self.assertRaises(modulo.ComplementoRecusado):
            modulo.preparar_comissao(rascunho(), pendencia(), identidade=identidade(grupo_id="-700002"), percentual="1.0000", beneficiario="AB", agora=100)

    def test_hmac_alteracao_outro_usuario_grupo_mensagem_antiga_expirada_e_frase_citada(self):
        plano = plano_valido()
        envelope = modulo.assinar_previa(plano, SEGREDO)
        alterado = copy.deepcopy(envelope)
        alterado["plano"]["comissao"]["valor"] = "999.99"
        with self.assertRaises(modulo.ComplementoRecusado):
            modulo.confirmar_previa(alterado, segredo=SEGREDO, identidade=identidade(mensagem_id="99999"), texto=modulo.frase_confirmacao(plano), agora=101)
        for ident in (identidade(autor_id="99999"), identidade(grupo_id="-700002"), identidade(agente="outro"), identidade(mensagem_id="67890")):
            with self.subTest(ident=ident), self.assertRaises(modulo.ComplementoRecusado):
                modulo.confirmar_previa(envelope, segredo=SEGREDO, identidade=ident, texto=modulo.frase_confirmacao(plano), agora=101)
        with self.assertRaises(modulo.ComplementoRecusado):
            modulo.confirmar_previa(envelope, segredo=SEGREDO, identidade=identidade(mensagem_id="99999"), texto=f"citado: {modulo.frase_confirmacao(plano)}", agora=101)
        with self.assertRaises(modulo.ComplementoRecusado):
            modulo.confirmar_previa(envelope, segredo=SEGREDO, identidade=identidade(mensagem_id="99999"), texto=modulo.frase_confirmacao(plano), agora=1001)

    def test_assinatura_e_seguro_curto_e_previa_confirmada_sem_escrita(self):
        plano = plano_valido()
        with self.assertRaises(modulo.ComplementoRecusado):
            modulo.assinar_previa(plano, b"curto")
        envelope = modulo.assinar_previa(plano, SEGREDO)
        confirmado = modulo.confirmar_previa(envelope, segredo=SEGREDO, identidade=identidade(mensagem_id="99999"), texto=modulo.frase_confirmacao(plano), agora=101)
        self.assertEqual(confirmado["p_plano"]["plano_id"], plano["plano_id"])
        self.assertEqual(confirmado["p_confirmacao"]["texto"], modulo.frase_confirmacao(plano))
        self.assertNotIn("salvar", modulo.resumo_previa(plano).lower())

    def test_copias_nao_mutam_entrada_e_identidades_fixas(self):
        d, p, i = rascunho(), pendencia(), identidade()
        snapshot = copy.deepcopy((d, p, i))
        plano = modulo.preparar_comissao(d, p, identidade=i, percentual="1.0000", beneficiario="AB", agora=100)
        modulo.assinar_previa(plano, SEGREDO)
        self.assertEqual((d, p, i), snapshot)
        for identidade_invalida in ({**i, "topico_id": "8"}, {**i, "autor_bot": True}, {**i, "encaminhada": True}, {**i, "canal": "whatsapp"}):
            with self.assertRaises(modulo.ComplementoRecusado):
                modulo.preparar_comissao(d, p, identidade=identidade_invalida, percentual="1.0000", beneficiario="AB", agora=100)


if __name__ == "__main__":
    unittest.main()
