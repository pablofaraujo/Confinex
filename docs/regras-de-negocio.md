# Regras de negócio e fórmulas

Fonte canônica: `confinex-app.latest.js` (funções citadas pelos nomes reais). Validado também contra a skill `boi-balanca` (8 casos de teste, 100% de paridade com o JS).

## Constantes de domínio

- 1 arroba (@) = 15 kg de carcaça.
- 1 contrato BGI (B3) = 330 @.
- Rendimento de carcaça (RC) típico: entrada 50%, final 53%.
- Carreta: 65 bois (macho) / 70 (fêmea) — `boisPorCarretaPadrao`.
- Limite de capim padrão: 300 kg (abaixo disso não aplica desconto).
- Funrural: 0,2% default no Confinex; 1,5% default no simulador do Boi Balança.
- Finpec: campo separado no Confinex, com padrão de 0%. Informar 1% sobre o faturamento bruto total somente nos negócios em que houver a cobrança.

## Arrobas de compra e desconto de capim — `calcArrobas()` / `divisorCapim()`

Modos (`modoCapim`): `sem`, `10kg`, `700g`, `800g`, `1kg`.
- `sem` → `peso/2/15` (rendimento livre 50%).
- `10kg` (= "20 kg de balança") → `(peso × 0,5 − 10) / 15`, se `peso ≥ limCapim`.
- `700g/800g/1kg` → desconto por arroba de **carcaça** (não de balança): `divisorCapim(modo) = 15/(15−g)×30` → divisores **31,4685 / 31,6901 / 32,1429**.
- Desconto bezerro (fêmeas, opcional): −10/15 @ (−0,6667 @) se `peso ≥ limBezerro` (default 280 kg).

Exemplo de referência: 120 bois × 565 kg a R$ 245/@ com 800g → **2.139,47 @ = R$ 524.169,33**. (Com divisor de balança errado daria R$ 538.934 — R$ 14,7 mil de diferença no lote.)

Custo de compra: `custoCompra = arrobasCompra × precoCompra × N + baldeio`.

## Frete — `calcCenario`

- `qtdCarretas = ceil(N / boisPorCarreta)`.
- `fretePorCarretaBruto = km × 2 × precoPorKm + pedIda + pedVolta`.
- `respFrete`: `meu` (integral), `dividido` (metade), `confinamento` (zero).

## Quebra de transporte — `perdaKm(km)`

7% base + 0,5 p.p. por 100 km acima de 300 km (override manual por `perdaManual`).
`pesoChegada = pesoMedio × (1 − pctPerda)`; `pesoProc = pesoChegada + (pesoMedio − pesoChegada) × recuperacao%`.

### Duas referências para analisar as arrobas

As duas leituras são auxiliares e não mudam lucro, custos totais, caixa ou ranking:

- **Transporte na @ de chegada (padrão legado)**: @ base = peso processado × 50% ÷ 15; custo da @ posta = (compra + transporte) ÷ @ base; @ produzida = @ de saída − @ base; custo produzido = confinamento ÷ @ produzida.
- **Transporte na @ produzida**: @ base = peso de origem × 50% ÷ 15; custo da @ de origem = compra ÷ @ base; @ produzida = @ de saída − @ de origem; custo produzido = (confinamento + transporte) ÷ @ produzida.
- A opção **Comparar as duas** apresenta os dois referenciais sem somar transporte ou perda duas vezes.
- Perda bruta = origem − chegada; recuperação = processado − chegada; perda líquida = origem − processado. Denominador ausente ou não positivo aparece como “Não calculável”, nunca como zero.

## Engorda e abate

- `pesoAbate = pesoBase(refGanho) + gmd × diasCiclo` (refGanho: chegada/origem/proc).
- `carcacaKg = pesoAbate × rcFinal`; `arrobasAbate = carcacaKg / 15`; `arrobasEntrada = pesoRef × rcEntrada / 15`.

## Métricas legadas de custo por arroba

- **Arrobas postas no confinamento**: `arrobasPostas = pesoProcessado × 50% ÷ 15 × N`.
- **Custo da arroba posta**: `(custoCompra + freteTotal) ÷ arrobasPostas`. Mede o custo do gado já transportado e processado, independentemente do prazo de permanência.
- **Carcaça líquida produzida**: `kgCarcacaProduzida = pesoAbate × rcFinal − pesoProcessado × 50%`.
- **Arrobas líquidas produzidas**: `kgCarcacaProduzida ÷ 15`.
- **Custo da arroba líquida produzida**: `custoConfinamentoTotal ÷ arrobasLiquidasProduzidasTotal`. Considera o ganho de peso e a evolução do RC de 50% no processamento para o RC esperado no abate.
- **Custo marginal da arroba de ganho**: `custoDiarioCab ÷ (GMD × rcFinal) × 15`. Mostra apenas o custo do ganho diário, sem atribuir ao GMD a evolução do RC sobre o peso já existente.
- **Frete diluído por arroba produzida**: `freteTotal ÷ arrobasLiquidasProduzidasTotal`; cai conforme mais arrobas são produzidas.
- **Produção + frete por arroba produzida**: `(custoConfinamentoTotal + freteTotal) ÷ arrobasLiquidasProduzidasTotal`.
- Origem e local do confinamento pertencem ao estudo. O local do confinamento também acompanha a base salva, permitindo reutilizá-lo em novos cenários sem publicar endereços no repositório.
- Quando não houver integração de distância homologada, a rota é aberta no Google Maps e a quilometragem permanece de preenchimento manual. Uma distância automática precisa guardar fonte e data; ao entrar no cálculo, fica congelada no estudo para que uma consulta posterior não altere silenciosamente o resultado histórico.

## Custo de confinamento por `modalidade`

- `arroba` → `custoArrobaProd × (arrobasAbate − arrobasEntrada)`.
- `ms` → `tonsMS × custoMS + custoAdm × dias + protocolo`, com `consumoDiarioKg = pesoMedioConf × consumoMS%`.
- `diaria` → `custoDiaria × dias`.
- `parceria` → custo 0, mas a receita incide só sobre `arrobasEntrada` (o produtor entrega as arrobas de entrada; o ganho fica com o confinamento).

## Preço de venda e resultado

- Preço bruto: balcão = `precoBalcao`; bolsa = `precoBolsa × (1−baseDesc%)`.
- `faturamentoBruto = arrobasRef × precoVendaBruto × N`.
- `valorFunrural = faturamentoBruto × pctFunrural`; `valorFinpec = faturamentoBruto × pctFinpec` (padrão 0%; normalmente 1% quando aplicável). Os encargos são calculados separadamente sobre a mesma base bruta, sem incidência em cascata.
- **Receita líquida**: `receita = faturamentoBruto − valorFunrural − valorFinpec`.
- **Custos operacionais**: `custosOperacionais = custoCompra + freteTotal + custoContTotal`.
- **Lucro bruto**: `lucroBruto = receita − custosOperacionais`.
- **Custo financeiro total**: custo do dinheiro da compra + frete + parcelas do confinamento + eventual operação financeira adicional.
- **Lucro líquido**: `lucroLiquido = lucroBruto − custoFinanceiroTotal`. Cada componente é descontado exatamente uma vez. Bruto e líquido só são iguais quando o custo financeiro total é zero.

## Capital, prazos e valor presente

- `diasTotal = diasCiclo + diasPagamento` (diasPagamento = prazo de RECEBIMENTO da venda); `diasCapitalCompra = max(diasTotal − prazoPagtoCompra, 0)`.
- Frete pago à vista integra o capital pelo prazo total e recebe custo do dinheiro. Frete marcado **pago no acerto final** continua como despesa operacional, mas não integra capital nem custo financeiro.
- O pagamento do confinamento tem três contratos: `adiantado` (uma parcela na entrada), `mensal` (parcelas no fim de cada período de 30 dias, com o último período parcial proporcional aos dias) e `final` (uma parcela no fim do ciclo). Cenários antigos sem o campo são normalizados para `final`.
- Cada parcela do confinamento corre custo do dinheiro somente de seu vencimento até o recebimento da venda: `valorNoRecebimento = parcela × (1 + i)^(diasExposição/30)`. Se a venda for recebida no fim do ciclo, a parcela final não tem exposição; com prazo pós-abate, corre custo somente nesse intervalo.
- O custo financeiro das parcelas do confinamento reduz o lucro líquido uma única vez. O valor presente segue uma trilha separada: `VPparcela = parcela ÷ (1 + i)^(diaParcela/30)`; ele não é somado nem subtraído novamente do lucro nominal.
- O tempo consolidado do capital é ponderado pelo valor e prazo de compra e frete.
- `rentTotal = lucroBruto/investInicial` e `rentMensal = (1+rentTotal)^(1/mesesCapital) − 1` são as rentabilidades brutas principal total e mensal; `rTliq = lucroLiquido/investInicial` e `rMliq = (1+rTliq)^(1/mesesCapital) − 1` representam, de forma complementar, o retorno depois do custo financeiro.
- O custo do dinheiro da compra e do frete é calculado separadamente conforme o prazo de cada desembolso e reduz `lucroLiquido`, `rTliq` e `rMliq`.
- A simulação financeira possui dois tipos. **Adiantamento de capital** representa dinheiro adicional colocado no negócio: `custoAdiantamento = valorAdiantamento × i × diasAdiantamento/30`; o custo reduz o resultado e o prazo original permanece. **Antecipação do recebimento** representa parte do valor final recebida antes: o valor antecipado entra na data escolhida, enquanto principal e custo são abatidos do saldo no acerto final.
- Compra, frete, `custoDinheiroConfinamento` e `custoAdiantamento`, quando houver, reduzem o resultado usado em `rTliq` e `rMliq`.
- Na antecipação, o valor máximo é `valorTerminalSemOperacao ÷ (1 + i × diasAdiantamento/30)`, evitando saldo final negativo. A rentabilidade mensal é a taxa interna de retorno dos fluxos `−capital` no início equivalente, `+valorAntecipado` na data escolhida e `+saldoFinal` no acerto. Assim, o lucro nominal diminui pelo custo, mas a rentabilidade mensal pode aumentar pela redução do tempo de capital exposto.
- O efeito da operação adicional é comparado na métrica líquida complementar: `rMliqSemAdiantamento` versus `rMliq`; `impactoAdiantamentoMensal = rMliq − rMliqSemAdiantamento`, em pontos percentuais ao mês.
- Ranking dos cenários: `rentMensal` decrescente; desempates por lucro bruto, rentabilidade total bruta e ordem original. Cartões, tabela, evolução e relatório usam a mesma sequência e destacam a rentabilidade mensal bruta. Lucro e rentabilidade líquidos permanecem complementares.
- Forma, parcelas, vencimentos, custo financeiro e valor presente do confinamento pertencem ao cenário e às bases salvas. Comparativo, evolução, ranking e relatório/PDF recalculam a mesma estrutura de fluxos.
- Valor presente é uma análise separada do lucro nominal. Receita e cada desembolso são trazidos ao dia zero por sua própria data: `VP = valor ÷ (1+i)^(dia/30)`. `resultadoVP = receitaVP − compraVP − freteVP − confinamentoVP`; **`precoCompraVpMax`** resolve o preço nominal de compra que zera esse resultado na data de pagamento da compra. `margemCompraVp = precoCompraVpMax − precoCompra`.
- Na interface, `precoCompraVpMax` recebe o nome humano **Preço máximo de compra para VP zero**. Ele aparece ao lado do preço atual, da diferença em R$/@ e da situação acima/abaixo do limite; não representa lucro adicional nem recomendação automática de compra.
- A referência **Preço mínimo de revenda para igualar o lucro líquido** é separada do VP. Ela calcula o preço bruto por arroba necessário em um cenário de revenda direta para alcançar o mesmo lucro líquido total de um confinamento: `(lucroLiquidoAlvo + custosOperacionaisRevenda + custoFinanceiroRevenda) ÷ (1 − tributosRevenda) ÷ arrobasVendidas`. As arrobas já refletem o desconto comercial de capim escolhido; custos e custo financeiro vêm do prazo da revenda. A tela também compara o preço disponível, a diferença, o lucro líquido estimado e a melhor alternativa. Sem cenário de revenda ou sem dados válidos, informa exatamente que a comparação não pode ser calculada.
- O próprio cenário de revenda desconta os tributos e encargos percentuais informados antes de calcular lucro líquido e rentabilidade. O preço digitado é bruto; `precoVendaLiq` é o valor líquido por arroba após esses descontos.
- Se o preço algébrico de empate for negativo porque o confinamento tem prejuízo maior que todos os custos da revenda, a interface não apresenta preço negativo como referência comercial: mostra que a revenda já supera o lucro-alvo mesmo com preço igual a zero.
- A antiga indicação isolada de “ponto ótimo” foi removida. Ela não respeitava o mínimo produtivo e mantinha a mesma cotação ao mudar o mês de saída.
- A evolução temporal compara 60 a 240 dias em intervalos de 15 dias, incluindo também o ciclo atual quando estiver dentro dessa faixa. Cada prazo recalcula a saída e usa a cotação do contrato BGI daquele mês; sem cotação, o ponto fica pendente e não gera resultado enganoso.
- Depois da aprovação, o prazo operacional pode ser ajustado repetidamente em **Operações → Confinamento**. A estimativa original permanece congelada; cada ajuste exige motivo e registra prazo anterior, novo prazo, saída anterior, nova saída, autor e horário. O prazo atual é sempre o último ajuste válido.

## Agenda financeira e dívidas

- `financeiro.html` é uma projeção somente leitura. Atualizar a página ou usar filtros nunca cria baixa, parcela, renegociação, lembrete persistente ou conciliação.
- Em `fluxo_caixa`, entrada prevista com saldo representa conta a receber; saída prevista com saldo representa conta a pagar. Item realizado tem saldo zero. Pagamento parcial preserva `valor original`, `valor pago` e `saldo em aberto` separadamente.
- A situação é derivada nesta ordem: realizado, parcial, atrasado e previsto. Assim, uma obrigação com pagamento parcial continua identificada como parcial; a data vencida ainda produz lembrete atrasado.
- Dívida em aberto soma os saldos de empréstimos e promissórias. Quitado não deixa saldo; valor pago é a diferença entre original e saldo quando este estiver disponível.
- Lembretes da tela são derivados de compromissos em aberto vencidos ou com vencimento nos próximos 30 dias. Eles não provam que Telegram ou e-mail foi enviado.
- Vínculos mostram uma referência de negócio e uma origem humana, como Compra, Venda ou Confinamento. UUID, ID de grupo e JSON bruto não podem aparecer.
- `transacoes_banco` é uma fonte de conciliação para consulta. A indisponibilidade dessa tabela não derruba agenda e dívidas; a interface apresenta aviso próprio. Falha das fontes principais produz mensagem genérica sem expor detalhe interno.
- O rascunho `202607240001_financeiro_compromissos.sql` não foi aplicado. Até autorização e homologação, parcelas, pagamentos, renegociações e lembretes persistentes que não existirem nas fontes atuais permanecem apenas como capacidade projetada, nunca simulada como dado real.

## Pendências e eventos

- Pendências agrega `operation_drafts`, `pending_actions` e `pendencias_documentos` em modo somente leitura. Ela indica o que exige atenção e a próxima etapa, mas conferência e promoção operacional continuam exclusivamente em Revisões.
- Uma fonte de Pendências indisponível não oculta itens válidos das demais. Falha total produz mensagem genérica; detalhes internos da API nunca são apresentados.
- Eventos é histórico, não fila operacional. Os filtros por situação, tipo, período e texto atuam localmente e não alteram registros.
- Resumo e contexto priorizam campos humanos explícitos e, depois, dados legíveis de estruturas aninhadas ou códigos operacionais. JSON bruto, UUID, ID técnico de grupo e referência `telegram:<id>` são descartados, nunca usados como substituto.
- Documento sem referência humana específica conserva o contexto genérico “Documento operacional”; a ausência de código não deve transformar toda a fonte documental em “Contexto não informado”.
- Cada pendência e evento aponta para uma área operacional humana compatível com sua origem. Na falta de destino específico, a Visão Geral é usada com rótulo legível, sem expor identificador técnico.

## Análise de sensibilidade — `SensPanel` / `calcComOverride`

7 sliders: `precoCompra`, `prazoPagtoCompra`, `diasCiclo`, `gmd`, `rcFinal`, `perdaTransporte`, `precoVenda` (respeita `modoPreco` do cenário — fix `a7c9db8`). Testes nomeados salvos em `historico`.

## Contratos B3 / BGI

- O rateio aceita códigos com espaços acidentais e separadores humanos, como `CF-AA-NNN 5,2 cts`, `CF-AA-NNN: 5,2` e `CF-AA- NNN - 5,2`. A cobertura por lote usa as quantidades explicitamente informadas; quando há um único lote sem quantidade, usa todos os contratos da posição. Vários lotes sem quantidade não recebem rateio inventado.
- Na transição do Portfólio B3, um registro gerenciado (`termo` iniciado por `bgp:`) prevalece visualmente sobre seu par legado somente quando contrato, direção, quantidade, entrada e status coincidem. Posições gerenciadas distintas são preservadas.
- `tools/sanear_duplicidades_bgi.py` remove um legado apenas quando existe um único registro gerenciado economicamente equivalente, o legado não possui alocações nem informação exclusiva e diferenças de custo representam somente enriquecimento no canônico. O modo padrão é dry-run; a execução exige o `plano_id`, cria snapshot privado e comprova que alocações e tabelas operacionais permaneceram inalteradas.

- Código do contrato: `BGI` + letra do mês (F,G,H,J,K,M,N,Q,U,V,X,Z) + ano 2 dígitos, sugerido pela **data de saída** (`dataEntrada + diasCiclo`); alterar entrada ou permanência re-sugere o vencimento e carrega somente sua própria cotação. Sem cotação disponível, o preço fica vazio em vez de reutilizar o contrato anterior.
- A cotação é única por **contrato/vencimento dentro do estudo**, não única para todos os cenários: cenários que usam o mesmo código BGI compartilham obrigatoriamente o mesmo índice e a mesma fonte/data de consulta.
- A seção geral **Mercado BGI** reúne os vencimentos usados e atualiza a curva em um único lote de consulta. O diferencial de base continua individual por cenário.
- Cada cotação registra se veio de atualização automática ou de informação manual. Atualizar a curva nunca substitui um valor manual; a volta ao automático é uma decisão explícita por contrato.
- Cotação vazia ou inválida fica pendente e nunca é convertida em zero nem usada para calcular resultado.
- Os vencimentos da seção Mercado BGI são exibidos em ordem cronológica crescente de mês e ano, independentemente da letra usada no código do contrato.
- Na modalidade `parceria`, o contrato é definido automaticamente pelo mês da saída. Em `ms` e nas demais modalidades não-parceria, o usuário pode escolher outro vencimento; se ele ainda não tiver cotação no estudo, o preço fica vazio até atualização ou preenchimento na seção geral, evitando reaproveitar a cotação de outro mês.
- Encerramento (`encerrar` no bgi.html): `resultado = mult × (preco_entrada − preco_saida) × contratos_qtd × 330` (mult = +1 vendido, −1 comprado), rateado pró-rata nas `alocacoes_hedge`.
- Rolagem (`rolar`): encerra com resultado, cria nova posição (`obs:'rolagem de X'`, `rolada_para`), replica alocações.
- Basis = preço físico (`FISICO`) − futuro.

## Boi Balança — `simular()` (bb.html)

- Compra por R$/kg vivo ou R$/@ com rendimento combinado: `custoCab = peso × (rendC/100)/15 × precoC`.
- `arrCarc = peso × (rc/100)/15`; Funrural na venda (default 1,5%).
- **RC de equilíbrio**: `rcBE = custosCab/(peso/15 × precoV × (1−fun)) × 100`.
- **Descasamento de caixa**: `giroCaixa = prazoRecebimento − prazoPagamento`.
- `vendas.prazo_recebimento` é a DATA prevista de recebimento (não nº de dias); previsão = `prazo_recebimento || data_abate`; vencimento de compra = `data_pagamento || addDias(data, prazo_dias)`.
- Margem realizada por lote: `valor_bruto − funrural − valor_total`.

## Regras fiscais e custos observados nas apurações reais

- Funrural 0,2% sobre receita BRUTA (novilhas, porção dos bois e bônus de arrobas).
- GTA Abate R$ 4,63/cab; GTA Produtor R$ 2,89/cab; acompanhamento de abate R$ 11,00/cab; bônus R$ 5,00/@.
- Google Sheets em pt-BR usa vírgula decimal nas fórmulas (`*0,002`).

## Skill promissória (promissoria-skill.zip)

Comandos `emitir` / `quitar` / `listar`; numeração `NNN/AAAA`; fonte de verdade = tabela `promissorias` no Supabase (upsert); cópia local em `~/promissorias`. Regras: nunca emitir/quitar sem confirmação explícita; não inventar ID de negócio; promessa incondicional (sem cláusulas condicionais); assinatura digital gov.br (assinador.iti.br); quitação assinada pelo credor. Python: fpdf2 + num2words, `SUPABASE_SERVICE_KEY` no ambiente.
