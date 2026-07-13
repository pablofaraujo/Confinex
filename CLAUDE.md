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
| `index.html` | Visão Geral — Home/dashboard do ecossistema (KPIs, exposição, estoque, pendências, acertos, fluxo; absorveu o antigo painel) |
| `confinex.html` + `confinex-app.latest.js` | Confinex — simulador de compra/confinamento/revenda (React 19 via esm.sh; bundle esbuild commitado, sem `src/` no repo) |
| `bb.html` | Boi Balança — giro rápido balança→gancho |
| `bgi.html` | BGI — posições de hedge B3 (módulo principal; o portfolio externo `boi-gordo-portfolio` virou link secundário na topbar) |
| `ocr-pesagem.html` | OCR Pesagem — leitura de tickets de balança (tema escuro próprio, ainda fora do DS) |
| `painel.html` | LEGADA — redirect para `index.html` (conteúdo migrou para a Home) |
| `ops.html` | Ops — heartbeats de agentes + fila de missões `vps_briefings` para o Claude Code da VPS |
| `central.html` | LEGADA — agora só redirect para `index.html` |

## Backend

- **Supabase** `fkmdzwjmjlmxqotznvgq.supabase.co` — usado por bb, bgi, painel, ops e index (auth email/senha, chave publicável hardcoded, RLS protege). Tabelas principais: `operacoes`, `compras`, `vendas`, `posicoes_hedge`, `alocacoes_hedge`, `cotacoes_bgi`, `pendencias_documentos`, `acertos`, `fluxo_caixa`, `promissorias`, `vps_briefings`, `ecossistema_inventario`, `ecossistema_status`; views `v_exposicao_hedge`, `v_estoque_atual`.
- **Confinex é a exceção**: localStorage + Google Sheets via Apps Script (JSONP para leitura, POST para escrita, debounce 10s, detecção de conflito multi-dispositivo por `clientUpdatedAt`). Chaves: `confinex:last-state:v3`, `confinex:named-versions:v1`, etc.

## Deploy

Push na `main` → workflow `deploy.yml` publica o repositório inteiro no GitHub Pages (sem build). Cuidado: tudo que estiver commitado fica público.

## Regra permanente — Design System

**Antes de criar qualquer tela, componente ou estilo novo: verificar se já existe equivalente no Design System (`DESIGN.md`, `design/`, `js/cfagro-*.js`). Se existir, reutilizar. Se não existir, criar no Design System — nunca na página — e documentar no `DESIGN.md`.** Nenhum layout novo nasce fora do DS. Plano e auditoria: `docs/auditoria-ui-ux.md`.

## Convenções

- Tudo em pt-BR: código, variáveis, UI, commits.
- Single-file por app na lógica, mas visual e infra compartilhados: páginas carregam `design/tokens.css` + `design/components.css` + `js/cfagro-core.js` (client Supabase único, `fmtR$`/`fmtN`/`fmtD` etc. e `CFAgro.authInit`) + `js/cfagro-shell.js` (sidebar fixa de navegação, defer). Sem framework, sem package.json, sem testes. Exceções ainda fora do DS: `confinex.html` (bundle React, fase 4) e `ocr-pesagem.html` (tema escuro próprio, pendente).
- Fonte padrão: Inter (via tokens.css). Dark mode preparado em `[data-theme=dark]`, sem toggle ainda.
- Botão flutuante "⌂ Central" (`.voltar-central`) em toda página-satélite.
- Ações destrutivas com `confirm()`/`prompt()` nativos.
- Constantes de domínio: **1 @ = 15 kg**; **1 contrato BGI = 330 @**; RC padrão 50–53%; 65 bois/carreta (macho) / 70 (fêmea); limite de capim padrão 300 kg; Funrural 0,2% (Confinex) / 1,5% default no simulador do bb.

## Armadilhas conhecidas

- O fonte do Confinex não está no repo — só o bundle `confinex-app.latest.js` (legível, não minificado). Edite o bundle diretamente com cuidado ou reconstrua fora.
- Cotações B3: cascata frágil (API B3 → histórico → Yahoo `{contrato}.SA` → scrape CEPEA via allorigins.win) com heurística `extrairNumeroB3` que aceita qualquer número entre 100 e 800.
- `rolar()`/`encerrar()` no bgi.html fazem escritas sequenciais sem transação.
- `central.html` legada e `bgi.html` órfão podem divergir do resto.
