# Confinex / CFAgro — Base de Conhecimento Completa

_Consolidado em 12/07/2026 a partir do repositório GitHub e das sessões do Cowork._

---

# CLAUDE.md — Ecossistema CFAgro / Confinex

Ecossistema de apps web para gestão de confinamento e giro de gado de Pablo Ferreira (marca **CFAgro**). Publicado via GitHub Pages em `https://pablofaraujo.github.io/Confinex/`.

## Documentação de contexto

Leia conforme a tarefa:

- `docs/arquitetura.md` — apps, backend, deploy, convenções e dívidas técnicas
- `docs/regras-de-negocio.md` — fórmulas e regras de cálculo (arrobas, capim, frete, GMD, Funrural, B3, VP)
- `docs/historico.md` — evolução do projeto (fases do git log)
- `docs/privado/contexto-negocio.md` — negócio, parcerias, pessoas, frigoríficos (NÃO commitar — ver abaixo)
- `docs/privado/infraestrutura.md` — VPS, OpenClaw, bot Juan, skills (NÃO commitar)
- `docs/privado/pendencias.md` — pendências abertas de negócio e infra (NÃO commitar)

⚠️ **Este repositório é PÚBLICO.** Tudo em `docs/privado/` contém dados financeiros, nomes de sócios e infraestrutura — mantenha no `.gitignore`.

## Visão geral dos apps (todos single-file)

| Arquivo | App |
|---|---|
| `index.html` | Central de Operações (hub, 5 cards + KPIs via Supabase) |
| `confinex.html` + `confinex-app.latest.js` | Confinex — simulador de compra/confinamento/revenda (React 19 via esm.sh; bundle esbuild commitado, sem `src/` no repo) |
| `bb.html` | Boi Balança — giro rápido balança→gancho |
| `bgi.html` | BGI — posições de hedge B3 (semi-órfão; o card BGI da Central aponta para o repo externo `boi-gordo-portfolio`) |
| `painel.html` | Painel Vivo — dashboard consolidado (Chart.js) |
| `ops.html` | Ops — heartbeats de agentes + fila de missões `vps_briefings` para o Claude Code da VPS |
| `central.html` | LEGADA (Central antiga com 3 cards; `bgi.html` ainda linka para ela) |

## Backend

- **Supabase** `fkmdzwjmjlmxqotznvgq.supabase.co` — usado por bb, bgi, painel, ops e index (auth email/senha, chave publicável hardcoded, RLS protege). Tabelas principais: `operacoes`, `compras`, `vendas`, `posicoes_hedge`, `alocacoes_hedge`, `cotacoes_bgi`, `pendencias_documentos`, `acertos`, `fluxo_caixa`, `promissorias`, `vps_briefings`, `ecossistema_inventario`, `ecossistema_status`; views `v_exposicao_hedge`, `v_estoque_atual`.
- **Confinex é a exceção**: localStorage + Google Sheets via Apps Script (JSONP para leitura, POST para escrita, debounce 10s, detecção de conflito multi-dispositivo por `clientUpdatedAt`). Chaves: `confinex:last-state:v3`, `confinex:named-versions:v1`, etc.

## Deploy

Push na `main` → workflow `deploy.yml` publica o repositório inteiro no GitHub Pages (sem build). Cuidado: tudo que estiver commitado fica público.

## Convenções

- Tudo em pt-BR: código, variáveis, UI, commits.
- Single-file por app: HTML + CSS inline + vanilla JS (exceto Confinex/React). Sem framework, sem package.json, sem testes.
- CSS/paleta e helpers (`fmtR$`, `fmtN`) duplicados entre páginas — mudança de tema exige tocar todas.
- Botão flutuante "⌂ Central" em toda página-satélite.
- Ações destrutivas com `confirm()`/`prompt()` nativos.
- Constantes de domínio: **1 @ = 15 kg**; **1 contrato BGI = 330 @**; RC padrão 50–53%; 65 bois/carreta (macho) / 70 (fêmea); limite de capim padrão 300 kg; Funrural 0,2% (Confinex) / 1,5% default no simulador do bb.

## Armadilhas conhecidas

- O fonte do Confinex não está no repo — só o bundle `confinex-app.latest.js` (legível, não minificado). Edite o bundle diretamente com cuidado ou reconstrua fora.
- Cotações B3: cascata frágil (API B3 → histórico → Yahoo `{contrato}.SA` → scrape CEPEA via allorigins.win) com heurística `extrairNumeroB3` que aceita qualquer número entre 100 e 800.
- `rolar()`/`encerrar()` no bgi.html fazem escritas sequenciais sem transação.
- `central.html` legada e `bgi.html` órfão podem divergir do resto.

---

# Arquitetura do ecossistema CFAgro

Analisado do repositório `pablofaraujo/Confinex` (HEAD `69dc3d5`, 2026-07-11).

## Apps e navegação

- **index.html** — Central de Operações (hub e página inicial). Grid de 5 cards: Confinex (`./confinex.html`), BGI (externo: `https://pablofaraujo.github.io/boi-gordo-portfolio/`), Boi Balança (`./bb.html`), Dashboard (`./painel.html`), Ops (`./ops.html`). Mostra 3 KPIs (Cabeças, Cts descobertos, Pendências) lendo do Supabase se houver sessão; senão pede "Faça login no Dashboard uma vez".
- **confinex.html** — shell mínimo do Confinex: importmap React 19.2.6 via esm.sh, define `window.CONFINEX_SHEETS_API_URL` (Apps Script) e importa `./confinex-app.latest.js?v=...`.
- **bb.html** — Boi Balança: simulador pré-compra + lotes + contas a pagar/receber + pendências GTA/NF.
- **bgi.html** — BGI Posições: exposição por lote, travas, encerramento, rolagem, cotações, basis. Não é linkado por nenhuma página (o card BGI aponta para o repo externo `boi-gordo-portfolio`); acesso por URL direta. Ainda linka de volta para `central.html` (legada).
- **painel.html** — Painel Vivo: KPIs, exposição BGI, posições B3 (hedge × especulação), estoque, pendências, acertos, fluxo de caixa com Chart.js.
- **ops.html** — Ops: heartbeats (`ecossistema_status`, vivo se ≤10 min), inventário (`ecossistema_inventario`), "Ponte VPS" — fila `vps_briefings` (status pendente/em_andamento/concluido) consumida por um Claude Code rodando na VPS via cron (~5–10 min).
- **central.html** — versão legada da Central (3 cards). Ficou para trás no commit `fc9b66c`.

Padrão: botão flutuante "⌂ Central" (`href="./"`) em toda página-satélite. Favicon `confinex-logo.jpg`.

## Backend Supabase

URL `https://fkmdzwjmjlmxqotznvgq.supabase.co`, chave publicável hardcoded em cada página (RLS protege; rotação exige editar N arquivos). Auth `signInWithPassword`, sessão persistida.

Tabelas/views por app:
- **bb**: `operacoes` (`tipo_negocio='boi_balanca'`), `compras` (`prazo_dias`, `data_pagamento`, `pago`), `vendas` (`prazo_recebimento` = **data prevista** de recebimento, `recebido`, `funrural`), `pendencias_documentos`, `contatos`. Pendências excluem status `validado`/`cancelado`.
- **bgi**: `v_exposicao_hedge`, `posicoes_hedge` (+ `alocacoes_hedge` com `resultado_creditado` rateado), `cotacoes_bgi` (contrato ou `FISICO`; basis = físico − futuro).
- **painel**: as anteriores + `v_estoque_atual`, `acertos`, `fluxo_caixa`; `posicoes_hedge.categoria` distingue hedge × `especulacao`; filtro `.or('status.in.(aberta,rolada),origem.eq.bgi-portfolio')` importa posições do app bgi-portfolio.
- **ops**: `ecossistema_inventario`, `ecossistema_status`, `vps_briefings`.
- **promissórias** (skill): tabela `promissorias` (numero pk `NNN/AAAA`, credor, cpf, valor, vencimento, praça, negocio_id, status aberta/quitada).

## Confinex (confinex-app.latest.js, ~110 KB / 2.120 linhas)

Bundle esbuild legível de `src/confinex-entry.jsx` + `confinex_work.jsx` (fonte NÃO está no repo). React com hooks, JSX pré-compilado, CSS injetado via template string (tema em `var T`). Raiz: componente `Confinex()`.

### Persistência
- localStorage: `confinex:last-state:v3` (auto-save), `confinex:restore-before-reset:v1` (snapshot antes de reset), `confinex:named-versions:v1` (até 80 versões), `confinex:sheets-backend-url`, `confinex:device-id:v1`; legado migrado: `confinex:last-state:v2`.
- Google Sheets via Apps Script: leitura JSONP (`sheetsJsonp`, timeout 12s), escrita `sheetsPost` (ações `getState/saveState/getVersions/saveVersion/deleteVersion`) + `sheetsBeacon` no `pagehide`. Proteções: `cloudReadyRef` bloqueia auto-save até carregar a nuvem; debounce 10s; carimbo `clientUpdatedAt` → backend responde `error:"conflict"` se outro dispositivo salvou depois; merge por id em `carregarVersoesSheets`; "Salvar agora" força sobrescrita.

### Estado
`estadoAtual()` = `{lote, cenarios (até 5, o 5º nasce "Revenda"), confinamentos (modelos salvos), historico (testes de sensibilidade), scAtivo, resultados, data, versao:"1.2-sheets"}`. Defaults em `defaultLote` e `defaultSc(i)`.

### Integrações
- **Cotação B3**: `buscarPrecoB3PorContrato` — cascata API B3 (`InstrumentPriceFluctuation`) → `DailyFluctuationHistory` → Yahoo (`{contrato}.SA`) → CEPEA (scrape via proxy allorigins.win). Contrato sugerido por `contratoB3PorData` a partir da **data de saída** (`dataEntrada + diasCiclo`), códigos de mês F,G,H,J,K,M,N,Q,U,V,X,Z.
- **Distância de frete**: `calcularDistancia` usa `window.CONFINEX_GOOGLE_MAPS_KEY` ou `CONFINEX_DISTANCE_PROXY`; sem chave, abre Google Maps para copiar km manualmente.

## Deploy

`.github/workflows/deploy.yml`: push na `main` → checkout → configure-pages → upload-pages-artifact (`path: '.'`, repo inteiro, sem build) → deploy-pages. Publica em `https://pablofaraujo.github.io/Confinex/`.

## Dívidas técnicas

1. `central.html` legada coexiste com `index.html`; `bgi.html` linka para a legada.
2. Fonte do bundle React fora do repo.
3. CSS e helpers duplicados em 6 páginas.
4. Credenciais Supabase e URL do Apps Script hardcoded em todas as páginas.
5. Confinex usa persistência diferente (Sheets/localStorage) do resto (Supabase) — a "fonte única de verdade" prometida no footer da Central não vale para ele.
6. JSONP + scrape CEPEA + heurística `extrairNumeroB3` (aceita 100–800) são frágeis.
7. `rolar()`/`encerrar()` sem transação.
8. Deploy publica tudo, inclusive `promissoria-skill.zip`.
9. Sem testes, lint ou package.json.

---

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

- `diasTotal = diasCiclo + diasPagamento` (diasPagamento = prazo de RECEBIMENTO da venda); `diasCapital = max(diasTotal − prazoPagtoCompra, 0)`; `mesesCapital = diasCapital/30`.
- `rentTotal = lucro/investInicial`; `rentMensal = (1+rT)^(1/mesesCapital) − 1` (composto).
- Custo do dinheiro: `custoDinheiroTotal = investInicial × ((1+i)^mesesCapital − 1)`, i = `custoDinheiro` % a.m. → `lucroLiquido`, `rTliq`, `rMliq`.
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

---

# Histórico de evolução do projeto (git log, 31 commits)

1. **Origem**: upload inicial do `index.html` (então o próprio simulador) + `deploy.yml`. Dois commits de teste "Hello→Goodbye" (`62e4dab`, `3b0d36e`).
2. **v2 do simulador**: `confinex_v2.html` vira `index.html` (PR #1); fixes de encoding; depois `923cc65` remove o v2.
3. **Correções de cálculo**: `cabPorCarreta` + frete por carreta; `precoVenda` na sensibilidade (`38be9d0`/`b05507c`); frete por quantidade de carretas (`de497ee`); capital e preço líquido (`8993ec2`); novos defaults (`2c72d2d`).
4. **Persistência**: auto-save de testes (`4f4caf4`); salvar último cálculo (`e560c0d`); recalcular ao abrir (`fbe9404`); alinhar sensibilidade + preservar modalidade de preço (`0479e8a`/`a7c9db8`).
5. **Ecossistema** (`dd01865`): "Fase 0" — nasce a arquitetura atual (bundle `confinex-app.latest.js` + páginas Supabase: Central, BGI, Painel Vivo). `fc9b66c`: Central vira página inicial, Confinex movido para `/confinex.html`, card BGI aponta para `boi-gordo-portfolio`.
6. **Expansões (jul/2026)**: `39686ee` hedge×especulação no Painel + contrato B3 acompanha data de saída; `3462bf1` Boi Balança (bb.html) + filtro de pendências canceladas; `b20df94` `prazo_recebimento` como data prevista; `9fb8c02` Ops dashboard; `69dc3d5` skill promissória (OpenClaw/Juan).

---

# Contexto de negócio (PRIVADO — não commitar em repo público)

Extraído das sessões do Cowork (jul/2026).

## O negócio

Pablo Ferreira Araujo opera confinamento e giro de gado sob a marca **CFAgro**: compra de gado (novilhas, vacas, bezerros, bois), engorda em cocho e venda a frigoríficos, com hedge em contratos BGI na B3.

## Pessoas e contrapartes

- **JP (João Paulo)** — sócio capitalista (parceria com apuração proporcional própria).
- **Xande (Alexandre Merlo)** — sócio 50/50 (Negócio Nº 3 documentado).
- **Wilson ("Wilson/Supremo")** — dono de 18 bois abatidos na Maxibeef; repassa valor a Pablo.
- **Amauri** — fornecedor de novilhas da parceria Xande.
- **Izabela Ferreira da Silva** — coproprietária que aparece nos demonstrativos de abate.
- Fornecedores de gado (jan-fev/26): Geverson Peron, Antonio Severino Bez, Junior Miura/Ubiracy, Nilton De Oliveira Acacio, Aquiles Ricardin, Marcão Zeny (parceria Xande), Valteir Barra, Laerson Oriente Lele.
- **Frigoríficos**: FRISA/FRISA AGRO (Frigorífico Rio Doce S.A. — principal destino das novilhas JP), Maxibeef (abates da parceria Xande), Minerva (contato "Minerva Marcia Mesa Operacao" no WhatsApp — fonte dos documentos CSAP).
- **Fazendas/locais**: Fazenda Santa Filomena (relatórios zootécnicos), CSAP/Altinópolis (confinamento "boitel" que recebe animais via contratos de parceria pecuária), Fazenda Ametista (origem default no Confinex).

## Parceria JP — apuração fechada

13 negócios de out/2024 a fev/2026 (quase todos "Novilhas Frisa" + 1 "Bois Balancão"). Números aceitos:
- Total aportado JP: R$ 208.574,33; lucro proporcional JP: R$ 127.061,00; capital final JP: R$ 335.635,33.
- Rentabilidade: 60,92% total; 40,01% a.a.; 2,84% a.m. Lucro de Pablo: R$ 444.412. Período 02/10/24 → 02/02/26.
- **Regra do capital idle**: deal 12 pagou R$ 563.604 em 02/02/26; JP reteve tudo até o acerto de 10/03/26 (36 dias). JP devolve `563.604 − (capital JP + lucro JP no deal 12)` ≈ R$ 345.209 + custo do dinheiro dos 36 dias. Cenário A (taxa do negócio ~40% a.a.): custo ~R$ 11.668 → Pablo recebe ~R$ 356.877. Cenário B (CDI 10,5% a.a.): ~R$ 3.416 → ~R$ 348.625. **Escolha da taxa ficou pendente.**
- Planilhas: `260124_Calculo Parceiria JP-Pablo.xlsx`, `260306-JP.xlsx` (abas Sheet1×Sheet2 divergem — ver pendencias.md).

## Parceria Xande — Negócio Nº 3

Planilha Google Sheets `consolidado_pablo_xande.xlsx` (docs.google.com/spreadsheets/d/14TOOJOYE023LRDzIwJfK4BwAoh1Aup4o, aba gid 22816765).
- PARTE 1: 13 novilhas 50/50 — receita bruta R$ 51.189,99 → líquida R$ 51.087,61 − compra Amauri R$ 44.968,67 − custos = lucro R$ 5.191,68 (R$ 2.595,84 cada).
- PARTE 2: acerto Wilson→Pablo dos 18 bois — total R$ 83.605,99 − GTAs − acompanhamento − lucro Xande − Funrural = **R$ 80.561,60 repassado a Pablo**. Abate 03/03/2026 na Maxibeef.

## Operação CSAP / Altinópolis

- 567 documentos do WhatsApp (Minerva) organizados em 13 categorias no Google Drive (Contratos ALT, NFs, Impostos/DAE ICMS SIARE, GTA, Laudos de morte, Informativos de Entrada, Cadastrais, Comprovantes, Planilhas, Mídias, Romaneios, Relatórios de Abate, Outros) + 12 Excels de controle (ex.: `Demonstrativo de Abate.xlsx` com 160 registros: contrato, curral, lote, cabeças, pesos, GPD, IMS %PV).
- Scheduled task **"classificar-documentos-csap"**: monitora inbox "14 - Novos Documentos" (Drive ID `1vPsFw-a20k7DetynJLfmA9Kn4exxsHnl`), classifica por palavras-chave, copia (não move) para a pasta certa e atualiza os Excels de contratos (`Contratos ALT - Assinados.xlsx` ID `1rhT4Ytz00J-F8_ERo_ZB95wyFLmF2aji`; `Contratos ALT - Minuta.xlsx` ID `1vLC9SBTDj9XnXEKEpfqQq2ZqIS_0zQ54`). Padrão de nome: `CSAP - ALTINÓPOLIS - NNN.AAAA - NOME - XXcab.pdf`.

## Pesagens

Balança Tru-Test/Datamars **XR5000** + brinco eletrônico → dados no site Datamars Livestock; em paralelo, caderno manual (nº do boi + peso). Workflow desejado (nunca executado): confrontar site × caderno e apontar faltas antes de aceitar pesagens. Skill `pesagem-ocr` (Gemini Vision) extrai dados de fotos recebidas no Telegram.

## Fluxo financeiro de compra

Pasta `05 FINANCEIRO / Compra Gado /` com `Compra_Gado_Fluxo.xlsx`: aba COMPRAS (código C-001…, data, vendedor, categoria, nº animais, valor) e aba PAGAMENTOS (compras + fretes + comissão + parceria + baldeio, status Pago/Pendente). 8 lotes jan-fev/26 (~R$ 620 mil).

---

# Infraestrutura (PRIVADO — não commitar em repo público)

## VPS

- **Hetzner**, IP público 5.161.79.153; IP Tailscale 100.83.231.75; alias SSH `openclaw-hetzner` (aponta para o IP Tailscale — SSH só via Tailscale, porta 22 pública fechada). Roda como root, Node v22.
- **Claude Code v2.1.141** instalado na VPS (plugins claude-mem e superpowers), executa missões da fila `vps_briefings` (Supabase) via cron ~5–10 min e tarefas de `vps_tarefas.md`.
- Container Docker `obsidian` (kasmweb) hospeda o vault Obsidian de memória.

## OpenClaw e agentes

- OpenClaw 2026.6.11 via npm, systemd user service `openclaw-gateway.service`, gateway `ws://127.0.0.1:18789`. Config `/root/.openclaw/openclaw.json`; workspace `/root/.openclaw/workspace`; skills em `/root/.openclaw/workspace/skills/`.
- Agentes: **Juan** (principal, ex-"main"), **Ceci** (conta Telegram própria), **Wey** (monitora grupo de preços de frigorífico no WhatsApp), **Zeus**. Agent-to-agent habilitado entre wey/ceci/juan.
- Canais: Telegram (2 contas: `ceci` e `default`; dm/groupPolicy allowlist; Telegram ID do Pablo 8552119610) e WhatsApp (allowlist com números do Pablo; groupPolicy corrigido de "open" para "allowlist"). Guard-rail: plugin `whatsapp-xande-readonly` ("WhatsApp Pablo Only") bloqueia mensagens WhatsApp de saída que não sejam para o próprio Pablo.
- Grupos Telegram: "Fazenda Operacional" (Juan ativo) e "Confinamento" (criado, mas group_id possivelmente ainda fora da allowlist — ver pendencias.md).

## Skills de negócio no Juan

- **`confinex-db`** — consulta/registra no Supabase do Confinex: lotes, cabeças, estoque, hedge/BGI, pendências GTA/NF, acertos, caixa. Dispara quando mensagem no grupo Confinamento descreve compra, pesagem, custo ou venda.
- **`boi-balanca`** — registra negócio, calcula previsão (fórmula `calcArrobas` do Confinex), lê romaneio de frigorífico em PDF e grava o realizado na tabela `negocios_boi_balanca`. Testada: 100% de acerto com a skill vs 36,7% sem.
- **`agronota`** — API AgroNota: NFs emitidas/recebidas, CT-es, totalizadores, clientes e propriedades de "Pablo Ferreira (CFAgro)".
- **`pesagem-ocr`** — OCR (Gemini Vision) de imagens de pesagem recebidas no Telegram.
- **`ofx`** — extratos bancários (Sicoob/OFX) → SQLite.
- **`obsidian-memoria-carregar/gravar`** — memória de operações no vault Obsidian.
- **`promissoria`** — emissão/quitação de notas promissórias (ver regras-de-negocio.md); commitada no repo como `promissoria-skill.zip`.
- Utilitárias: gog, tavily, whisper, browser-automation etc.

## Automações Cowork agendadas

- CSAP→Drive (classificar documentos) diariamente 01:04.
- Agenda GEV→GoodNotes 22:08.
- Consolidação de memória GSI segundas 08:05.

## Decisões registradas

- **Não ativar sandbox** nos agentes OpenClaw — quebraria skills que usam sqlite/Obsidian/CLIs no host.
- Supabase compartilhado por todo o ecossistema: `fkmdzwjmjlmxqotznvgq.supabase.co` (chave publicável no front; `SUPABASE_SERVICE_KEY` só no ambiente da VPS/skills).

---

# Pendências abertas (PRIVADO — não commitar em repo público)

Atualizado em 12/07/2026.

## Negócio

1. **Acerto final JP**: escolher taxa do custo do capital idle (36 dias) — negócio ~40% a.a. (~R$ 11.668) vs CDI 10,5% a.a. (~R$ 3.416).
2. **Conciliar `260306-JP.xlsx`**: Sheet1 (74 lançamentos) × Sheet2 (68) divergem — Sheet2 não tem os aportes de out/nov 2024 (R$ 71.976, R$ 720, R$ 71.347, GTAs 65092/64317/111568, transferência JP→Pablo dez/24 de −R$ 57.685) e há valores diferentes (ex.: saída Pablo fev/25 R$ 280.260,97 vs R$ 280.822,61). Existe `260306-JP_comparado.xlsx` com 780 células divergentes marcadas.
3. **Reconciliação de pesagens** XR5000/Datamars × caderno — nunca executada.
4. **GTA e NF pendentes** nos 8 lotes de compra jan-fev/26 (`Compra_Gado_Fluxo.xlsx`).
5. Confirmar limite de capim padrão (300 kg) na skill boi-balanca.
6. (Opcional) Renomear aba da planilha Xande para incluir "Nº 3".

## Infraestrutura

7. **Container Obsidian exposto publicamente** em https://5.161.79.153 (0.0.0.0:443) — rebind para IP Tailscale + rotacionar VNC_PW (vazou em chat).
8. Migrar secrets para 1Password (vault "VPS-OpenClaw" + Service Account + `op` CLI): token do gateway (rotacionar), botTokens Telegram, .env, API keys.
9. Desabilitar 5 provider plugins restantes (fireworks, moonshot, tencent, venice, zai); rodar `openclaw doctor --fix`; limpar ~40 backups do config.
10. Verificar se o Wey continua coletando preços do grupo de frigorífico após aperto da allowlist (pode precisar de exceção por grupo).
11. Adicionar group_id do grupo Telegram "Confinamento" na allowlist do OpenClaw (sessão terminou sem confirmação).

## Repositório

12. Aposentar `central.html` legada e corrigir link de volta do `bgi.html`.
13. Decidir destino do `bgi.html` (órfão — card BGI aponta para repo externo `boi-gordo-portfolio`).
14. Trazer o fonte do bundle React (`src/confinex-entry.jsx`, `confinex_work.jsx`) para o repo ou documentar onde vive.
15. Havia 1 issue aberta no GitHub em 12/07/2026 — verificar (`gh issue list`).

