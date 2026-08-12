import tempfile
import unittest
from pathlib import Path

from tools.consolidar_historico_telegram import (
    agrupar_compras,
    atualizar_grupos_existentes,
    carregar_aliases,
    cruzar_gtas,
    extrair_compra,
    finalizar_plano,
    inferir_destino,
    inferir_sexo_categoria,
    gerar_plano,
    ler_exportacao,
    relatorio_markdown,
    validar_plano_documental,
)


def html_exportacao(mensagens):
    blocos = []
    for indice, item in enumerate(mensagens, 1):
        autor = f'<div class="from_name">{item.get("autor", "")}</div>' if "autor" in item else ""
        texto = item.get("texto", "").replace("\n", "<br>")
        anexo = ""
        if item.get("anexo"):
            anexo = (
                '<div class="media_wrap clearfix">'
                f'<a class="photo_wrap" href="{item["anexo"]}">arquivo</a></div>'
            )
        blocos.append(
            f'<div class="message default clearfix" id="message{indice}">'
            f'<div class="date details" title="0{indice}.08.2026 10:00:00 UTC-03:00"></div>'
            f'{autor}{anexo}<div class="text">{texto}</div></div>'
        )
    return '<div class="text bold">Grupo de Teste</div>' + "".join(blocos)


def mensagem(texto, ordem=1, mensagem_id="m1"):
    return {
        "contexto": "Grupo de Teste",
        "texto": texto,
        "autor": "Agente",
        "ordem": ordem,
        "mensagem_id": mensagem_id,
        "data": f"0{ordem}.08.2026 10:00:00 UTC-03:00",
        "texto_sha256": f"hash-{mensagem_id}",
        "gtas": [],
        "categorias": [],
    }


class ConsolidarHistoricoTelegramTest(unittest.TestCase):
    def test_inferir_sexo_categoria_e_destino_sem_inventar(self):
        self.assertEqual(inferir_sexo_categoria("14 garrotes"), ("macho", "garrote"))
        self.assertEqual(inferir_sexo_categoria("19 vacas"), ("fêmea", "vaca"))
        self.assertEqual(inferir_sexo_categoria("60 novilhas"), ("fêmea", "novilha"))
        self.assertEqual(inferir_sexo_categoria("20 cabeças"), ("não informado", "não informado"))
        self.assertEqual(inferir_destino("lote destinado ao confinamento"), "confinamento")
        self.assertEqual(inferir_destino("gado para o abate no boi balança"), "abate / boi balança")
        self.assertEqual(inferir_destino("garrotes para a fazenda"), "fazenda")
        self.assertEqual(inferir_destino("Compra da Fazenda Ametista"), "não informado")

    def test_extrai_valor_calculado_e_pagamento_em_bloco(self):
        compra = extrair_compra(mensagem(
            "Compra – Fornecedor\nQuantidade: 19 vacas\nPreço: R$ 300/@\n"
            "Valor total: 307,57 × 300 = R$ 92.270,00\n📅 Pagamento\n• À vista",
            1, "m1"), {})
        self.assertEqual(float(compra["valor_total"]), 92270)
        self.assertEqual(compra["pagamento"], "à vista")

    def test_peso_com_ponto_de_milhar_permanece_numero(self):
        compra = extrair_compra(mensagem(
            "Compra – Fornecedor\nQuantidade: 60 novilhas\n"
            "Peso bruto total: 22.297 kg\nPreço: R$ 300/@",
            1, "m1"), {})
        self.assertEqual(float(compra["peso_total_kg"]), 22297)

    def test_normaliza_formas_equivalentes_de_pagamento(self):
        por_prazo = extrair_compra(mensagem(
            "Compra – Fornecedor\nQuantidade: 10 garrotes\nNegociação: 01/08/2026\n"
            "Pagamento: 30 dias", 1, "m1"), {})
        por_data = extrair_compra(mensagem(
            "Compra – Fornecedor\nQuantidade: 10 garrotes\nNegociação: 01/08/2026\n"
            "Pagamento\n• Data: 31 de agosto de 2026", 2, "m2"), {})
        grupo = agrupar_compras([por_prazo, por_data])[0]
        self.assertEqual(por_prazo["pagamento"], "31/08/2026")
        self.assertEqual(por_data["pagamento"], "31/08/2026")
        self.assertEqual(grupo["versoes"], 1)

    def test_repeticoes_semanticas_iguais_viram_uma_alternativa(self):
        completa = extrair_compra(mensagem(
            "Compra – Fornecedor\nQuantidade: 10 garrotes\nPeso bruto total: 3.000 kg\n"
            "Preço: R$ 300/@\nValor total: R$ 60.000,00\nNegociação: 01/08/2026\n"
            "Pagamento: 31/08/2026",
            1, "m1"), {})
        parcial = extrair_compra(mensagem(
            "Compra – Fornecedor\nQuantidade informada: 10 garrotes\nNegociação: 01/08/2026",
            2, "m2"), {})
        repetida = extrair_compra(mensagem(
            "Compra – Fornecedor\nQuantidade: 10 garrotes\nPeso bruto total: 3.000 kg\n"
            "Preço: R$ 300,00/@\nValor total: R$ 60.000,00\nNegociação: 01/08/2026\n"
            "Pagamento: 31/08/2026",
            3, "m3"), {})
        grupo = agrupar_compras([completa, parcial, repetida])[0]
        self.assertEqual(grupo["versoes"], 1)
        self.assertEqual(grupo["evidencias"], 3)
        self.assertEqual(grupo["repeticoes_consolidadas"], 2)
        self.assertEqual(grupo["versoes_revisao"][0]["ocorrencias"], 3)
        self.assertEqual(grupo["versoes_revisao"][0]["mensagens"], ["m1", "m2", "m3"])
        self.assertFalse(grupo["requer_revisao"])

    def test_versao_unica_incompleta_permanece_na_conferencia(self):
        incompleta = extrair_compra(mensagem(
            "Compra – Fornecedor\nQuantidade: 10 garrotes\nNegociação: 01/08/2026",
            1, "m1"), {})
        grupo = agrupar_compras([incompleta])[0]
        self.assertEqual(grupo["classificacao"], "incompleto_campos_obrigatorios")
        self.assertEqual(grupo["situacao_revisao"], "completar dados do negócio")
        self.assertTrue(grupo["requer_revisao"])
        self.assertEqual(grupo["prioridade_revisao"], "alta")
        self.assertIn("valor total", grupo["campos_minimos_ausentes_humanos"])

    def test_codigos_anuais_sao_deterministicos_e_preservam_vinculo(self):
        config = {"regras_mensagens": {
            "m1": {"negocio_origem": "NEG-26-001", "destino": "confinamento"},
            "m2": {"negocio_origem": "NEG-26-001", "destino": "fazenda"},
        }}
        compras = [
            extrair_compra(mensagem(
                "Compra – Fornecedor\nQuantidade: 10 garrotes\nNegociação: 01/08/2026",
                1, "m1"), config),
            extrair_compra(mensagem(
                "Compra – Fornecedor\nQuantidade: 5 garrotes\nNegociação: 01/08/2026",
                2, "m2"), config),
        ]
        grupos = agrupar_compras(compras)
        self.assertEqual([item["codigo_negocio"] for item in grupos], [
            "NEG-26-001", "NEG-26-002",
        ])
        self.assertEqual({item["negocio_origem"] for item in grupos}, {"NEG-26-001"})

    def test_plano_antigo_e_reclassificado_sem_combinar_evidencias(self):
        grupo = {
            "negocio_origem": None,
            "data_base": "2026-08-01",
            "classificacao": "repeticao_deduplicavel",
            "campos_ausentes_em_todas": ["peso_total_kg", "valor_total"],
            "campos_divergentes": [],
            "requer_revisao": False,
            "mensagens": ["m1"],
        }
        atualizado = atualizar_grupos_existentes([grupo])[0]
        self.assertEqual(atualizado["codigo_negocio"], "NEG-26-001")
        self.assertEqual(atualizado["data_base"], "01/08/2026")
        self.assertEqual(atualizado["classificacao"], "incompleto_campos_obrigatorios")
        self.assertTrue(atualizado["requer_revisao"])
        self.assertEqual(atualizado["mensagens"], ["m1"])

    def test_vinculo_gta_nf_e_associado_ao_negocio_sem_confirmar(self):
        compra = extrair_compra(mensagem(
            "Compra – Fornecedor\nQuantidade: 10 garrotes\nNegociação: 01/08/2026",
            1, "m1"), {})
        plano = {
            "gerado_em": "2026-08-12T00:00:00-03:00",
            "modo": "dry_run_somente_leitura",
            "plano_gera_escrita": False,
            "escritas_executadas": 0,
            "tabelas_operacionais_alteradas": 0,
            "resumo": {"anexos_omitidos": 0},
            "cortes": {},
            "grupos_compras": agrupar_compras([compra]),
            "cruzamento_gta": {"vinculos_exatos": 2, "vinculos": [
                {
                    "gta": "000001", "nf": "100", "linha_documento": 9,
                    "referencias_telegram": [{"mensagem_id": "m1"}],
                },
                {
                    "gta": "000002", "nf": "101", "linha_documento": 10,
                    "referencias_telegram": [{"mensagem_id": "fora-do-negocio"}],
                },
            ]},
            "pendencias": [],
        }
        atualizado = finalizar_plano(plano)
        grupo = atualizado["grupos_compras"][0]
        self.assertTrue(grupo["tem_vinculo_gta_nf_candidato"])
        self.assertFalse(grupo["vinculos_documentais_candidatos"][0]["confirmado"])
        self.assertIn("GTA/NF candidata", grupo["acao_recomendada"])
        self.assertEqual(
            atualizado["resumo_revisao"]["negocios_com_vinculo_gta_nf_candidato"], 1
        )
        self.assertEqual(
            atualizado["resumo_revisao"]["vinculos_gta_nf_sem_negocio_identificado"], 1
        )
        self.assertIn("vinculos_gta_nf_sem_negocio_identificado", atualizado["pendencias"])
        self.assertEqual(atualizado["escritas_executadas"], 0)

    def test_separa_mesma_origem_por_sexo_categoria_e_destino(self):
        config = {"regras_mensagens": {
            "m1": {"negocio_origem": "NEG-26-001", "destino": "confinamento"},
            "m2": {"negocio_origem": "NEG-26-001", "destino": "abate / boi balança"},
            "m3": {"negocio_origem": "NEG-26-001", "destino": "confinamento"},
            "m4": {"negocio_origem": "NEG-26-001", "destino": "fazenda"},
        }}
        textos = (
            "Compra – Fornecedor\nQuantidade: 10 novilhas\nNegociação: 01/08/2026",
            "Compra – Fornecedor\nQuantidade: 10 vacas\nNegociação: 01/08/2026",
            "Compra – Fornecedor\nQuantidade: 10 garrotes\nNegociação: 01/08/2026",
            "Compra – Fornecedor\nQuantidade: 5 garrotes\nNegociação: 01/08/2026",
        )
        compras = [extrair_compra(mensagem(texto, indice, f"m{indice}"), config)
                   for indice, texto in enumerate(textos, 1)]
        grupos = agrupar_compras(compras)
        identidades = {(item["sexo"], item["categoria"], item["destino"]) for item in grupos}
        self.assertEqual(len(grupos), 4)
        self.assertEqual(identidades, {
            ("fêmea", "novilha", "confinamento"),
            ("fêmea", "vaca", "abate / boi balança"),
            ("macho", "garrote", "confinamento"),
            ("macho", "garrote", "fazenda"),
        })

    def test_resumo_agregado_e_preservado_sem_criar_negocio(self):
        compra = extrair_compra(mensagem(
            "Compra – Fornecedor\nQuantidade: 40 garrotes\nNegociação: 01/08/2026",
            1, "m1"), {"regras_mensagens": {"m1": {"tipo_evidencia": "resumo_agregado"}}})
        exportacao = {
            "contexto": "Grupo", "arquivo": "messages.html", "arquivo_sha256": "a" * 64,
            "mensagens": [mensagem(compra and "Compra – Fornecedor\nQuantidade: 40 garrotes", 1, "m1")],
            "anexos": [], "anexos_omitidos": 0, "primeira_data": None, "ultima_data": None,
        }
        plano = gerar_plano(
            [exportacao], {"regras_mensagens": {"m1": {"tipo_evidencia": "resumo_agregado"}}}
        )
        self.assertEqual(plano["resumo"]["resumos_agregados_preservados"], 1)
        self.assertEqual(plano["resumo"]["grupos_compras"], 0)
        self.assertEqual(plano["evidencias_agregadas"][0]["quantidade"], 40)

    def test_carrega_regras_privadas_sem_expor_na_saida_publica(self):
        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / "aliases.json"
            caminho.write_text('{"aliases":{"Apelido":"Nome"},"regras_mensagens":{"m1":{"destino":"fazenda"}}}')
            config = carregar_aliases(caminho)
        self.assertEqual(config["aliases"]["apelido"], "nome")
        self.assertEqual(config["regras_mensagens"]["m1"]["destino"], "fazenda")

    def test_le_exportacao_herda_autor_e_confere_anexo(self):
        with tempfile.TemporaryDirectory() as pasta:
            raiz = Path(pasta)
            (raiz / "photos").mkdir()
            (raiz / "photos" / "foto.jpg").write_bytes(b"foto")
            caminho = raiz / "messages.html"
            caminho.write_text(html_exportacao([
                {"autor": "Pessoa", "texto": "primeira"},
                {"texto": "segunda", "anexo": "photos/foto.jpg"},
            ]))
            exportacao = ler_exportacao(caminho)
        self.assertEqual(exportacao["contexto"], "Grupo de Teste")
        self.assertEqual(exportacao["mensagens"][1]["autor"], "Pessoa")
        self.assertEqual(len(exportacao["anexos"]), 1)
        self.assertTrue(exportacao["anexos"][0]["existe"])
        self.assertEqual(len(exportacao["anexos"][0]["sha256"]), 64)

    def test_correcao_explicita_posterior_e_preferida_sem_confirmar(self):
        inicial = extrair_compra(mensagem(
            "Compra – Fornecedor\nQuantidade: 10 cabeças\nPeso bruto total: 3.000 kg\nNegociação: 01/08/2026",
            1, "m1"), {})
        correcao = extrair_compra(mensagem(
            "Correção final da Compra – Fornecedor\nQuantidade: 11 cabeças\nPeso bruto total: 3.300 kg\nNegociação: 01/08/2026",
            2, "m2"), {})
        grupo = agrupar_compras([inicial, correcao])[0]
        self.assertEqual(grupo["classificacao"], "correcao_explicita_mais_recente")
        self.assertEqual(grupo["versao_preferida"]["quantidade"], 11)
        self.assertFalse(grupo["confirmado"])
        self.assertTrue(grupo["requer_revisao"])
        self.assertEqual(grupo["situacao_revisao"], "conferir correção explícita")
        self.assertEqual(grupo["prioridade_revisao"], "alta")
        self.assertIn("cabeças", grupo["campos_divergentes_humanos"])

    def test_mesmo_fornecedor_e_data_com_dados_distintos_fica_ambiguo(self):
        primeira = extrair_compra(mensagem(
            "Compra – Fornecedor\nQuantidade: 10 cabeças\nPeso bruto total: 3.000 kg\nNegociação: 01/08/2026",
            1, "m1"), {})
        segunda = extrair_compra(mensagem(
            "Compra – Fornecedor\nQuantidade: 5 cabeças\nPeso bruto total: 1.500 kg\nNegociação: 01/08/2026",
            2, "m2"), {})
        grupo = agrupar_compras([primeira, segunda])[0]
        self.assertEqual(grupo["classificacao"], "ambiguo_multiplas_versoes")
        self.assertIsNone(grupo["versao_preferida"])
        self.assertFalse(grupo["confirmado"])
        self.assertEqual(grupo["situacao_revisao"], "escolher a versão correta")
        self.assertIn("peso total", grupo["campos_divergentes_humanos"])
        self.assertIn("valor total", grupo["campos_ausentes_humanos"])
        self.assertEqual(len(grupo["versoes_revisao"]), 2)

    def test_gta_exata_e_candidata_forte_mas_nao_confirmada(self):
        mensagens = [{
            "contexto": "Grupo", "mensagem_id": "m1", "data": "01.08.2026",
            "gtas": ["654321"],
        }]
        documentos = {"vinculos_nf_gta": [
            {"nf": "100", "gta": "654321", "linha_agronotas": 9},
            {"nf": "101", "gta": "999999", "linha_agronotas": 10},
        ]}
        resultado = cruzar_gtas(mensagens, documentos)
        self.assertEqual(resultado["vinculos_exatos"], 1)
        self.assertEqual(resultado["somente_documentos"], 1)
        self.assertEqual(resultado["classificacao"], "candidato_forte_documental_nao_confirmado")
        self.assertFalse(resultado["confirmado"])

    def test_teste_e_exemplo_nao_entram_nos_negocios_reais(self):
        compra_teste = extrair_compra(mensagem(
            "Compra – Teste Final\nQuantidade: 1 cabeça\nNegociação: 01/08/2026",
            1, "m1"), {})
        self.assertTrue(compra_teste["eh_teste"])
        exportacao = {
            "contexto": "Grupo", "arquivo": "messages.html", "arquivo_sha256": "a" * 64,
            "mensagens": [mensagem(compra_teste and "Compra – Teste Final\nQuantidade: 1 cabeça", 1, "m1")],
            "anexos": [], "anexos_omitidos": 0, "primeira_data": None, "ultima_data": None,
        }
        plano = gerar_plano([exportacao], {}, None, None)
        self.assertEqual(plano["resumo"]["blocos_compra_reais_deduplicados"], 0)

    def test_texto_igual_em_contextos_diferentes_permanece_como_evidencia(self):
        base = mensagem("Compra – Fornecedor\nQuantidade: 10 cabeças", 1, "m1")
        base["contexto"] = "Grupo A"
        outro = dict(base, contexto="Grupo B", mensagem_id="m2")
        exportacoes = []
        for contexto, item in (("Grupo A", base), ("Grupo B", outro)):
            exportacoes.append({
                "contexto": contexto, "arquivo": f"{contexto}.html",
                "arquivo_sha256": contexto * 8, "mensagens": [item], "anexos": [],
                "anexos_omitidos": 0, "primeira_data": None, "ultima_data": None,
            })
        plano = gerar_plano(exportacoes, {})
        self.assertEqual(plano["resumo"]["blocos_compra_reais_deduplicados"], 2)
        self.assertEqual(plano["resumo"]["grupos_compras"], 2)

    def test_cortes_sao_os_das_fontes_e_variacao_ima_nao_e_inferida(self):
        exportacao = {
            "contexto": "Grupo", "arquivo": "messages.html", "arquivo_sha256": "a" * 64,
            "mensagens": [], "anexos": [], "anexos_omitidos": 0,
            "primeira_data": None, "ultima_data": None,
        }
        documentos = {
            "plano_gera_escrita": False,
            "escritas_executadas": 0,
            "tabelas_operacionais_alteradas": 0,
            "fontes": {
            "agronotas": {"data_final": "2026-06-12", "notas": 4, "com_gta": 3},
            "banco": {"data_final": "2026-07-24", "transacoes": 5},
            "ima": {"periodo_final": "2026-07-26", "saldo_rebanho": 253},
        }}
        plano = gerar_plano([exportacao], {}, documentos,
                            {"data": "2026-08-11", "saldo_rebanho": 263})
        self.assertEqual(plano["cortes"]["agronotas"], "2026-06-12")
        self.assertEqual(plano["cortes"]["banco"], "2026-07-24")
        self.assertEqual(plano["cortes"]["variacao_ima_sem_detalhamento"], 10)
        self.assertIn("saldo_ima_variou_sem_ficha_detalhada_correspondente", plano["pendencias"])
        self.assertEqual(plano["escritas_executadas"], 0)
        self.assertEqual(plano["tabelas_operacionais_alteradas"], 0)
        self.assertFalse(plano["plano_gera_escrita"])
        self.assertTrue(plano["validacao_documental"]["somente_leitura"])

    def test_rejeita_plano_documental_que_nao_comprova_zero_escrita(self):
        for documentos in (
            {"plano_gera_escrita": True, "escritas_executadas": 0,
             "tabelas_operacionais_alteradas": 0},
            {"plano_gera_escrita": False, "escritas_executadas": 1,
             "tabelas_operacionais_alteradas": 0},
            {"plano_gera_escrita": False, "escritas_executadas": 0,
             "tabelas_operacionais_alteradas": 1},
        ):
            with self.subTest(documentos=documentos):
                with self.assertRaisesRegex(ValueError, "zero escrita operacional"):
                    validar_plano_documental(documentos)

    def test_assina_e_resume_fontes_documentais_sem_dados_reais(self):
        documentos = {
            "plano_id": "plano-teste",
            "plano_gera_escrita": False,
            "escritas_executadas": 0,
            "tabelas_operacionais_alteradas": 0,
            "fontes": {
                "agronotas": {"notas": 10, "com_gta": 8},
                "banco": {"transacoes": 12},
                "ima": {"movimentos": 4},
                "negocios": {"registros": 3},
            },
            "vinculos_nf_gta": [{"gta": "000001"}],
            "candidatos_banco": [],
            "candidatos_negocio": [],
        }
        resultado = validar_plano_documental(documentos)
        self.assertTrue(resultado["somente_leitura"])
        self.assertEqual(resultado["vinculos_nf_gta"], 1)
        self.assertEqual(resultado["transacoes_banco"], 12)
        self.assertEqual(len(resultado["assinatura_sha256"]), 64)

    def test_plano_id_e_deterministico_e_nao_ha_opcao_executar(self):
        exportacao = {
            "contexto": "Grupo", "arquivo": "messages.html", "arquivo_sha256": "a" * 64,
            "mensagens": [], "anexos": [], "anexos_omitidos": 0,
            "primeira_data": None, "ultima_data": None,
        }
        primeiro = gerar_plano([exportacao], {})
        segundo = gerar_plano([exportacao], {})
        self.assertEqual(primeiro["plano_id"], segundo["plano_id"])
        fonte = Path(__file__).with_name("consolidar_historico_telegram.py").read_text()
        self.assertNotIn("--executar", fonte)
        self.assertNotIn("ConfinexClient", fonte)
        self.assertNotIn("requests.", fonte)

    def test_relatorio_traz_fila_humana_sem_combinar_versoes(self):
        primeira = extrair_compra(mensagem(
            "Compra – Fornecedor\nQuantidade: 10 cabeças\nNegociação: 01/08/2026",
            1, "m1"), {})
        segunda = extrair_compra(mensagem(
            "Compra – Fornecedor\nQuantidade: 12 cabeças\nNegociação: 01/08/2026",
            2, "m2"), {})
        exportacao = {
            "contexto": "Grupo de Teste", "arquivo": "messages.html",
            "arquivo_sha256": "a" * 64, "mensagens": [], "anexos": [],
            "anexos_omitidos": 0, "primeira_data": None, "ultima_data": None,
        }
        plano = gerar_plano([exportacao], {})
        plano["grupos_compras"] = agrupar_compras([primeira, segunda])
        plano["resumo"]["grupos_compras"] = 1
        plano["resumo"]["grupos_ambiguos"] = 1
        texto = relatorio_markdown(plano)
        self.assertIn("Fila privada de conferência por negócio", texto)
        self.assertIn("escolher a versão correta", texto)
        self.assertIn("não combina campos de versões diferentes", texto)
        self.assertNotIn("{\"", texto)


if __name__ == "__main__":
    unittest.main()
