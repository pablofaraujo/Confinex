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
