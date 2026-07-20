# Arquitetura do ecossistema CFAgro

Atualizado em 2026-07-20.

## Apps e navegação

- **index.html** — Visão Geral e página inicial. Mostra quatro KPIs na ordem: total de cabeças, confinadas, Fazenda Ametista e Parceria Ricardo. A seção "Áreas do ecossistema" detalha cabeças e arrobas descobertas por confinamento, sexo do rebanho da Ametista quando disponível e bois ativos de Ricardo.
- **confinex.html** — shell do Confinex: carrega o Design System e a navegação compartilhada, define `window.CONFINEX_SHEETS_API_URL` (Apps Script) e inicia `confinex-app.mobile.js`, pacote autocontido com React 19 e alvo Safari 14. `confinex-app.latest.js` permanece como bundle legível de manutenção.
- **bb.html** — Boi Balança: simulador pré-compra + lotes + contas a pagar/receber + pendências GTA/NF.
- **abate.html** — Abate: cabeçalho por abate (`abates`: data, frigorífico, origem, romaneio, GTA) + romaneio animal a animal (`abate_animais`) com leitura OCR do romaneio do frigorífico (`aprovarFolha`). Não está no design system compartilhado (sem sidebar).
- **bgi.html** — BGI Posições: exposição por lote, travas, encerramento, rolagem, cotações, basis. Não é linkado por nenhuma página (o card BGI aponta para o repo externo `boi-gordo-portfolio`); acesso por URL direta. Ainda linka de volta para `central.html` (legada).
- **painel.html** — Painel Vivo: KPIs, exposição BGI, posições B3 (hedge × especulação), estoque, pendências, acertos, fluxo de caixa com Chart.js.
- **ops.html** — Ops: heartbeats (`ecossistema_status`, vivo se ≤10 min), inventário (`ecossistema_inventario`), "Ponte VPS" — fila `vps_briefings` (status pendente/em_andamento/concluido) consumida por um Claude Code rodando na VPS via cron (~5–10 min).
- **central.html** — versão legada da Central (3 cards). Ficou para trás no commit `fc9b66c`.
- **painel-boi-gordo.html** — Painel Boi Gordo: KPIs de mercado (arroba CEPEA/B3, bezerro, relação de troca, exportação), curva futura BGI com gráfico Chart.js, manchetes e contexto. Sem Supabase/login — dados vêm de um bloco JSON estático (`#painel-data`) embutido no HTML. Integrado ao DS/shell nesta sessão; antes vivia como artifact solto do Cowork.

Padrão: cabeçalho azul-marinho com marca Confinex e sidebar branca no desktop; no celular, a navegação vira uma faixa horizontal rolável. O botão legado "⌂ Central" fica oculto quando o shell está ativo. Favicon `confinex-logo.jpg`.

**Shell compartilhado** (`js/cfagro-shell.js`): injeta cabeçalho e navegação com links absolutos (inclusive para o repo externo `boi-gordo-portfolio`) e é carregado com `design/tokens.css`/`design/components.css` por todos os módulos ativos. Preserva a ordem histórica dos módulos e inclui Datamars Livestock, AgroNota e Portal do Produtor IMA/SIDAGRO em nova aba. `ops.html` mantém o client Supabase local, porém não redefine mais tokens nem componentes visuais.

## Backend Supabase

URL `https://fkmdzwjmjlmxqotznvgq.supabase.co`, chave publicável hardcoded em cada página (RLS protege; rotação exige editar N arquivos). Auth `signInWithPassword`, sessão persistida.

Tabelas/views por app:
- **bb**: `operacoes` (`tipo_negocio='boi_balanca'`), `compras` (`prazo_dias`, `data_pagamento`, `pago`), `vendas` (`prazo_recebimento` = **data prevista** de recebimento, `recebido`, `funrural`, + `outros_custos`/`custos_obs` desde jul/2026), `negocios_boi_balanca` (desde jul/2026: previsão pré-lote — `status` previsto/confirmado/cancelado, campos do simulador + `operacao_id`/`confirmado_em` quando confirmado), `pendencias_documentos`, `contatos`. Pendências excluem status `validado`/`cancelado`.
- **bgi**: `v_exposicao_hedge`, `posicoes_hedge` (+ `alocacoes_hedge` com `resultado_creditado` rateado), `cotacoes_bgi` (contrato ou `FISICO`; basis = físico − futuro).
- **painel**: as anteriores + `v_estoque_atual`, `acertos`, `fluxo_caixa`; `posicoes_hedge.categoria` distingue hedge × `especulacao`; filtro `.or('status.in.(aberta,rolada),origem.eq.bgi-portfolio')` importa posições do app bgi-portfolio.
- **ops**: `ecossistema_inventario`, `ecossistema_status`, `vps_briefings`.
- **abate**: `abates` (cabeçalho), `abate_animais` (romaneio por animal — schema alinhado jul/2026: `seq`, `descricao`, `classificacao`, `meia_esq`/`meia_dir`, `peso_kg` = carcaça, `vlr_kg`/`vlr_arroba`/`vlr_total`, `bonus`, `penalizacao`, `condenacao`; não guarda peso vivo/balança nem hora de passagem).
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
2. Fonte JSX do bundle React fora do repo; o bundle legível e o pacote móvel gerado ficam versionados.
3. O shell e os componentes principais são compartilhados; o Confinex ainda injeta CSS próprio dentro do bundle React, embora seus tokens visuais estejam alinhados ao DS.
4. Credenciais Supabase e URL do Apps Script hardcoded em todas as páginas.
5. Confinex usa persistência diferente (Sheets/localStorage) do resto (Supabase) — a "fonte única de verdade" prometida no footer da Central não vale para ele.
6. JSONP + scrape CEPEA + heurística `extrairNumeroB3` (aceita 100–800) são frágeis.
7. `rolar()`/`encerrar()`/`encerrarParcial()` sem transação (o parcial faz 3+ escritas sequenciais: update da posição, insert da parte fechada, rateio das alocações).
8. Deploy publica tudo, inclusive `promissoria-skill.zip`.
9. Sem testes, lint ou package.json.
10. `painel-boi-gordo.html` foi movido para o repo, mas a tarefa agendada `atualiza-painel-boi-gordo` (Cowork, seg-sex 6h32) ainda atualiza um artifact Cowork separado — o arquivo do repo não recebe as atualizações diárias até a automação ser redirecionada para editar este arquivo (e alguém commitar/push, já que a pasta do Drive não tem `.git`/push).
