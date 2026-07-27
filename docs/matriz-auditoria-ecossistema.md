# Matriz permanente de auditoria do ecossistema

Atualizado em 2026-07-23. A fonte executável é
`tools/auditar_ecossistema.py`; os navegadores são dirigidos por
`tools/auditar_ecossistema_browser.js` (Chromium) e
`tools/auditar_ecossistema_webkit.js` (Safari/iPhone).

| Requisito | Cenário | Resultado esperado | Evidência |
|---|---|---|---|
| Inventário | repositório normal, vazio e referência inválida | páginas, scripts, testes, workflows, rotas, menu e dependências são listados; vazio e arquivo ausente falham | JSON e Markdown da auditoria |
| Menu | cada item interno | arquivo e âncora existem; não há redirecionamento inesperado | resultado estático por item |
| Janelas | Portfolio B3 | navega na mesma janela | `target`/política do manifesto |
| Janelas | Datamars, AgroNota e IMA/SIDAGRO | nova janela é aceita por serem ferramentas externas | política explícita por item |
| Navegação | clique, URL direta, recarga e voltar | destino permanece correto e volta à Visão Geral | execução Chromium |
| Navegação | Financeiro, Pendências e Eventos | cada módulo tem arquivo real; nenhuma âncora da Home é usada como substituta | regressão Python + Chromium |
| Conteúdo protegido | dados positivos, inválidos, vazios e falha da API | projeções legíveis, valores inválidos não contaminam totais, vazio é explícito e erro não vaza detalhe interno | `tools/test_gestao_frontend.js` |
| Privacidade | item sem contexto humano | a interface mostra “Contexto não informado”, nunca UUID ou ID de grupo | regressão JavaScript |
| Estado | página acessada | somente o item correspondente fica ativo | DOM após navegação |
| Runtime | página carregada | nenhum erro JavaScript, console, HTTP ou requisição | eventos do Chromium |
| Desktop | 1440 × 1000 | shell presente, sem estouro da página | PNG integral + medição |
| Celular | 390 × 844 | shell presente, sem estouro da página | PNG integral + medição |
| Safari/iPhone | WebKit real, pacote normal | interface monta uma vez, sem erro ou estouro horizontal | Playwright WebKit |
| Safari/iPhone | primeira carga lenta/interrompida | nova cópia é buscada automaticamente; não há montagem duplicada nem aviso terminal | Playwright WebKit com falha de rede controlada |
| CI | push, PR, agenda e execução manual | auditoria estática, Chromium e WebKit geram artefato | GitHub Actions |
| Pagamento do confinamento | adiantado, mensal e no final | fluxos vencem no dia 0, a cada 30 dias e no fim do ciclo; período parcial é proporcional | regressão JavaScript com resultados manuais |
| Pagamento do confinamento | vazio, modo legado e entrada inválida | vazio preserva `final`; modo desconhecido normaliza; número inválido/negativo falha explicitamente | `tools/test_confinex_pagamento_confinamento.mjs` |
| Custo do dinheiro | recebimento no fim e após o abate | cada parcela capitaliza somente do vencimento ao recebimento; não há custo duplicado | comparação independente a 2% a.m. |
| Valor presente | qualquer forma de pagamento | VP das parcelas permanece separado do lucro nominal e do valor futuro | regressão JavaScript + comparativo/PDF |
| Lucro bruto/líquido | custo financeiro positivo | bruto − líquido = custo financeiro total, sem desconto duplicado | regressão pura + Chromium desktop/celular |
| Lucro bruto/líquido | custo financeiro zero | bruto e líquido são iguais e o custo exibido é zero | regressão pura + Chromium desktop/celular |
| Contrato financeiro | vazio, negativo e entrada inválida | vazio produz zeros; valor negativo ou não numérico falha explicitamente | `tools/test_confinex_resultado_financeiro.mjs` |
| Consistência | cartões, comparativo, evolução, ranking e PDF | todos priorizam `lucroBruto`, `rentabilidadeTotalBruta` e `rentabilidadeMensalBruta`; métricas líquidas permanecem complementares | Chromium + evidência de impressão |
| Financeiro | dados positivos | KPIs, agenda, dívidas, parcelas, saldos, renegociação, lembretes e conciliação ficam legíveis | regressão JavaScript + Chromium desktop/celular |
| Financeiro | valor monetário multimilionário | o número cabe dentro do próprio cartão, sem corte visual | medição de `scrollWidth` × `clientWidth` + PNG desktop/celular |
| Financeiro | pagamentos parcial e total | original, pago e saldo não se confundem; realizado zera saldo | `tools/test_gestao_frontend.js` + Chromium |
| Financeiro | filtros e vínculo com origem | a lista é filtrada localmente e leva a uma área humana sem mostrar UUID | Chromium desktop/celular |
| Financeiro | fontes vazias | cada seção apresenta estado vazio claro e KPIs zerados | Chromium desktop/celular |
| Financeiro | falha somente de `transacoes_banco` | agenda e dívidas continuam disponíveis, com aviso específico | Chromium desktop/celular |
| Financeiro | falha das fontes principais | mensagem humana aparece sem detalhe interno da API | regressão JavaScript + Chromium |
| Financeiro | carga, atualização e filtros | nenhuma chamada de escrita é feita | regressão estática + cliente simulado com contador de mutações |
| Migração financeira | arquivo versionado, ainda não aplicado | modelo é aditivo, sem DML operacional, com RLS e políticas apenas de leitura | `tools/test_migracao_financeiro.py` |
| Pendências | rascunho, ação e documento | cada item apresenta resumo, contexto, situação e próxima etapa ligada à origem | regressão JavaScript + Chromium desktop/celular |
| Pendências | filtro por origem e busca sem resultado | a lista reduz localmente e o vazio filtrado é explícito | Chromium desktop/celular |
| Pendências | uma fonte indisponível | itens das outras fontes permanecem visíveis e há aviso humano | cliente simulado com falha parcial |
| Pendências | vazio e falha total | vazio é claro; falha não expõe detalhes internos | Chromium desktop/celular |
| Pendências | documento sem código operacional | mantém “Documento operacional” e não perde o contexto de toda a fonte | projetor puro + Chromium desktop/celular |
| Eventos | dados positivos legados e aninhados | descrição, contexto, responsável e origem são humanos e navegáveis | regressão JavaScript + Chromium desktop/celular |
| Eventos | situação, tipo, período e texto | filtros atuam localmente e combinam com estado vazio claro | Chromium desktop/celular |
| Eventos | vazio e falha | vazio é explícito; falha não expõe detalhes internos | Chromium desktop/celular |
| Pendências e Eventos | JSON, UUID, ID de grupo e referência Telegram | conteúdo técnico é descartado, nunca usado como contexto | projetor puro + inspeção do DOM |
| Pendências e Eventos | carga, atualização e filtros | nenhuma chamada de escrita é feita | regressão estática + cliente simulado com contador de mutações |
| OCR de anexos | JPG, PNG, PDF textual, PDF escaneado e PDF multipágina | primeira passagem usa `arquivo_grupo_router.py`, sem escrita automática | `tools/test_juan_vps.py` em `--completa` |
| OCR de PDF | duas ou mais páginas | cada página extraída informa origem, não se repete e respeita limite seguro de oito páginas | contrato `validar_contrato_paginas` + saída remota |
| OCR de PDF | documento maior que o limite ou origem ausente | falha explícita; nunca soma páginas sem proveniência nem rasteriza indefinidamente | regressão Python + log da VPS |
| Distância | origem/destino, ajuste manual e fonte | distância positiva, limitada, com fonte e data; ajuste fica separado da base | `tools/test_confinex_distancia.mjs` |
| Distância congelada | estudo calculado novamente | distância usada no estudo mantém `estudoId` e `congeladaEm`; alteração posterior não muda o estudo | contrato `congelarDistancia` |
| Frete | responsabilidade própria, dividida ou do confinamento | total, bruto e por cabeça são recalculados sem dados privados ou escrita operacional | regressão JavaScript |
| Acompanhamento | entrada, saída, consumo de matéria seca e diária | eventos são normalizados por lote e totalizam cabeças e consumo | `tools/test_confinex_acompanhamento.mjs` |
| Acompanhamento | pesagem, morte e transferência | saldo de cabeças e pesagens ficam rastreáveis, sem inventar eventos | regressão JavaScript |
| Acompanhamento financeiro | cobrança e pagamento parcial | saldo aberto é cobrança menos pagamentos; fechamento preserva o histórico | contrato de acompanhamento |
| Fechamento de lote | data válida e inválida | lote fecha uma vez com data ISO; entrada inválida falha sem escrita | regressão JavaScript |

## Estados

- `aprovado`: o resultado observado corresponde ao esperado.
- `falhou`: houve divergência reproduzível.
- `não testado`: a camada não foi executada; no modo estrito bloqueia a
  conclusão.

## Linha de base anterior às correções

O modo `--modo-descoberta` preserva somente a prova histórica do Ciclo 1. Ele aprovava a própria
auditoria quando, e somente quando, detecta os quatro defeitos já relatados:
Portfolio B3 em outra janela e os destinos inexistentes de Financeiro,
Pendências e Eventos. Qualquer falha adicional ou a ausência de uma dessas
detecções reprovava a execução, salvo defeito adicional registrado explicitamente
pela primeira passagem exploratória. A auditoria inicial também detectou estouro
horizontal de 49 px no Painel Boi Gordo em desktop. O Ciclo 2 corrigiu os cinco
defeitos e promoveu o modo estrito a gate permanente; a configuração histórica
continua versionada para demonstrar o que a auditoria detectava antes da correção.

## Comandos

```bash
python3 tools/auditar_ecossistema.py --somente-estatico
npm ci
npx playwright install chromium webkit
python3 tools/auditar_ecossistema.py --navegador \
  --saida-json artifacts/auditoria-ecossistema/relatorio.json \
  --saida-md artifacts/auditoria-ecossistema/relatorio.md
```

`--somente-estatico` mantém a camada de navegador registrada como “não
testado”, mas permite que a bateria parcial valide arquivos e contratos. Ele
não é usado no gate de navegadores: a execução com `--navegador` continua reprovando
qualquer falha ou requisito sem teste.
