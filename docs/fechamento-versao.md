# Fechamento da versão operacional

Atualizado em 2026-07-23. Esta página define quando a versão atual pode ser
considerada encerrada, sem depender de impressão subjetiva.

## Critério de pronto

A versão só fecha quando estes gates estiverem verdes:

1. `main` local limpo e publicado no GitHub.
2. GitHub Actions do commit publicado sem falha.
3. `python3 tools/test_ecossistema.py` aprovado localmente.
4. `python3 tools/sanear_fila_revisoes.py` executado em dry-run e sem escrita.
5. Validação completa VPS/Juan aprovada com PDF e foto reais.
6. Supabase assinado antes/depois sem alteração fora das ações autorizadas.
7. Nenhum rascunho, promoção ou memória operacional é criado sem confirmação
   explícita.

## Comando de fechamento

```bash
python3 tools/planejar_fechamento_versao.py
```

O comando é somente leitura. Ele informa se existem commits locais não
publicados, arquivos essenciais ausentes e quais gates ainda dependem de rodada
manual ou validação completa.

## Ordem dos ciclos restantes

1. **Publicação e CI:** manter `main` sincronizado e corrigir qualquer falha do
   GitHub Actions antes de novas features.
2. **Validação completa:** rodar `tools/test_ecossistema.py --completa` com os
   anexos reais no VPS.
3. **Saneamento da fila:** rodar `tools/sanear_fila_revisoes.py`; se houver
   vínculos fortes, aplicar em lote pequeno com confirmação forte.
4. **Fila operacional:** revisar os itens de prioridade alta, completar campos e
   preparar promoções apenas quando todos os dados estiverem conferidos.
5. **Memória e handoff:** decompor fatos operacionais para tabelas/eventos,
   manter somente regras duráveis em memória e encerrar handoff apenas após
   preservar os vínculos.

Com esses gates, esta versão deixa de ser uma sequência aberta de ajustes e
passa a ter uma linha clara de aceite.
