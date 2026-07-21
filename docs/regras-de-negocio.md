# Regras de negócio e fórmulas

Fonte canônica: `confinex-app.latest.js` (funções citadas pelos nomes reais). Validado também contra a skill `boi-balanca` (8 casos de teste, 100% de paridade com o JS).

## Constantes de domínio

- 1 arroba (@) = 15 kg de carcaça.
- 1 contrato BGI (B3) = 330 @.
- Rendimento de carcaça (RC) típico: entrada 50%, final 53%.
- Carreta: 65 bois (macho) / 70 (fêmea) — `boisPorCarretaPadrao`.
- Limite de capim padrão: 300 kg (abaixo disso não aplica desconto).
- Funrural: 0,2% default no Confinex; 1,5% default no simulador do Boi Balança.

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

## Engorda e abate

- `pesoAbate = pesoBase(refGanho) + gmd × diasCiclo` (refGanho: chegada/origem/proc).
- `carcacaKg = pesoAbate × rcFinal`; `arrobasAbate = carcacaKg / 15`; `arrobasEntrada = pesoRef × rcEntrada / 15`.

## Custo de confinamento por `modalidade`

- `arroba` → `custoArrobaProd × (arrobasAbate − arrobasEntrada)`.
- `ms` → `tonsMS × custoMS + custoAdm × dias + protocolo`, com `consumoDiarioKg = pesoMedioConf × consumoMS%`.
- `diaria` → `custoDiaria × dias`.
- `parceria` → custo 0, mas a receita incide só sobre `arrobasEntrada` (o produtor entrega as arrobas de entrada; o ganho fica com o confinamento).

## Preço de venda e resultado

- Funrural: `fur = pctInput(sc.funrural, 2e-3)`. Balcão: `precoBalcao × (1−fur)`. Bolsa: `precoBolsa × (1−baseDesc%) × (1−fur)`.
- `receita = arrobasRef × precoVenda × N`; `custos = custoCompra + freteTotal + custoContTotal`; `lucro = receita − custos`.

## Capital, prazos e valor presente

- `diasTotal = diasCiclo + diasPagamento` (diasPagamento = prazo de RECEBIMENTO da venda); `diasCapitalCompra = max(diasTotal − prazoPagtoCompra, 0)`.
- Frete pago à vista integra o capital pelo prazo total e recebe custo do dinheiro. Frete marcado **pago no acerto final** continua como despesa operacional, mas não integra capital nem custo financeiro.
- O tempo consolidado do capital é ponderado pelo valor e prazo de compra e frete.
- `rentTotal = lucro/investInicial`; `rentMensal = (1+rT)^(1/mesesCapital) − 1` (composto).
- Custo do dinheiro da compra e do frete é calculado separadamente conforme o prazo de cada desembolso.
- Adiantamento opcional: `diasAdiantamento = recebimento − dataAdiantamento`; `custoAdiantamento = valorAdiantamento × i × diasAdiantamento/30` (juros simples pró-rata). O custo reduz o resultado; o principal adiantado não é contado novamente como despesa.
- Ranking dos cenários: `rTliq` decrescente; desempates por lucro líquido, rentabilidade mensal líquida e ordem original. Cartões e tabela usam a mesma sequência.
- Valor presente: `fatorVP = (1+i)^mesesCapital`; `vpArroba = precoVenda/fatorVP`; **`precoCompraVpMax`** = maior R$/@ de compra que empata em VP = `(receitaVP − freteTotal − custoContTotal − baldeio)/arrobasCompraTotal`; `margemCompraVp = precoCompraVpMax − precoCompra`.
- `calcPontoOtimoDias` varre diasCiclo ~30–240 e maximiza `rMliq`.

## Análise de sensibilidade — `SensPanel` / `calcComOverride`

7 sliders: `precoCompra`, `prazoPagtoCompra`, `diasCiclo`, `gmd`, `rcFinal`, `perdaTransporte`, `precoVenda` (respeita `modoPreco` do cenário — fix `a7c9db8`). Testes nomeados salvos em `historico`.

## Contratos B3 / BGI

- Código do contrato: `BGI` + letra do mês (F,G,H,J,K,M,N,Q,U,V,X,Z) + ano 2 dígitos, sugerido pela **data de saída** (`dataEntrada + diasCiclo`); re-sugerido quando a data muda (commit `39686ee`).
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
