# Auditoria de UI/UX — Ecossistema CFAgro

**Data:** 12/07/2026 · **Escopo:** index, confinex (bundle), bb, bgi, painel, ops, central
**Objetivo:** base para a diretriz "um único sistema CFAgro" — sem redesign, com evolução incremental.

---

## 1. Sumário executivo

O ecossistema tem hoje **duas identidades visuais** e **cinco cópias do mesmo CSS**. A boa notícia: os 5 apps satélites (index, bb, bgi, painel, ops) já compartilham a mesma paleta via CSS variables com valores idênticos — a duplicação é literal, o que torna a extração para um arquivo compartilhado quase mecânica e de baixo risco. O Confinex é o ponto fora da curva: tema próprio (objeto `T` no bundle React), fontes próprias e zero reuso.

A recomendação técnica é **evolução vanilla, sem build**: tokens em CSS variables num `design/tokens.css`, componentes em `design/components.css`, helpers e shell de navegação em JS compartilhado carregado via `<link>`/`<script>`. Isso preserva o deploy atual (GitHub Pages sem build), permite migrar página por página e não quebra nada. O formato `tokens.ts` da diretriz original pressupõe bundler — fica como opção futura se o ecossistema um dia virar SPA.

---

## 2. Estado atual — mapa visual

| App | Fonte | Paleta | CSS | Navegação de volta |
|---|---|---|---|---|
| index.html | system (-apple-system) | tokens verde-escuro | inline, cópia parcial | é o hub |
| bb.html | system | tokens (13 vars) | inline, cópia completa | botão flutuante → `./` |
| bgi.html | system | tokens (13 vars) | inline, cópia completa | **botão topbar → central.html legada** |
| painel.html | system | tokens (13 vars) | inline, cópia completa | botão flutuante → `./` |
| ops.html | system | tokens (13 vars) | inline, cópia completa | botão flutuante → `./` |
| central.html (legada) | system | tokens (10 vars) | inline, cópia parcial | é hub antigo |
| **confinex** | **Plus Jakarta Sans + DM Mono** (e resíduos de Inter/Syne no bundle) | **tema `T` próprio** (#1A6B3C, cinzas Tailwind) | CSS-in-JS no bundle | link flutuante hardcoded |

Paleta satélites: `--bg:#f5f7f6 --text:#182b21 --muted:#6b7f74 --green:#059669 --border:#e3e9e5` + blue/amber/red com fundos.
Paleta Confinex: `bg:#F5F6F8 text:#1A1D23 muted:#9DA5B1 accent:#1A6B3C border:#E8EAED`. **Parecidas, mas nenhum valor é igual.**

---

## 3. Inconsistências de UI

1. **Duas identidades tipográficas.** Satélites usam fonte de sistema; Confinex importa Plus Jakarta Sans + DM Mono do Google Fonts (e o bundle ainda referencia Inter e Syne em trechos). Nada compartilhado.
2. **Tokens duplicados e divergentes em cobertura.** O `:root` é copiado em cada HTML; bb/bgi/painel/ops têm 13 variáveis, index e central têm menos (faltam `--blue/--bluebg/--greenbg`). Qualquer mudança de tema exige tocar 6+ arquivos.
3. **Escala de radius caótica.** Entre páginas aparecem 6, 8, 10, 12, 14, 16, 20, 999px e 50%. Confinex usa 14 (cards) e 8 (inputs). Não há escala definida.
4. **Cores hardcoded fora dos tokens.** Texto dos badges (`#065f46`, `#92400e`, `#991b1b`, `#1e40af`), aviso `#78350f`, fundos de ícone do hub (`#e8f5ee`, `#fef3c7`…), cores do Chart.js no painel, e todo o botão "⌂ Central" (que ignora as variáveis e repete `#fff/#e3e9e5/#182b21` inline em 4 páginas).
5. **Sombras diferentes por página.** `0 1px 2px rgba(16,42,30,.05)` (cards satélites), `0 1px 3px rgba(16,42,30,.06)` (cards do hub), `0 2px 10px rgba(15,23,42,.15)` (botão Central), `0 1px 4px rgba(0,0,0,.06)` (Confinex).
6. **Tipografia sem escala.** h1: 26px (index), 22px (bb/painel/ops), 21px (bgi). th: 11px vs 10.5px. KPI label: 11px vs 10px. Base: 12.5px (tabelas satélites) vs 13px (Confinex).
7. **Títulos de aba sem padrão.** "CFAgro — Central", "Boi Balança — Pablo Ferreira", "CFAgro — BGI · Posições", "CFAgro — Pablo Ferreira" (painel), "Ops — Ecossistema CFAgro", "Confinex - Pablo Ferreira".
8. **Nomenclatura de classes divergente.** `.foot` vs `.footer`; `.f` vs `.fld` (mesmo componente de campo); `.simgrid`/`.grid g6`/`.g2 g3 g4` (mesmo grid); `.res .r` vs `.kpi` (mesmo conceito de mini-KPI).
9. **Ícones = emojis.** 🧮📈⚖️📊⚙️ ↻ ⌂ ＋ 🛡 🎉 — renderização varia por SO, sem alinhamento óptico, sem estados. A diretriz pede Lucide.

## 4. Inconsistências de UX

1. **Login repetido e desconexo.** bb, bgi, painel e ops têm cada um sua tela de login própria (mesma sessão Supabase — logar num vale para todos, mas o usuário não sabe disso). Confinex não tem login algum (localStorage + Sheets). O hub mostra aviso "faça login no Dashboard" — gambiarra de sessão.
2. **Navegação hub-and-spoke com becos.** Tudo volta pela home via botão flutuante, exceto: bgi.html volta para a **central.html legada** (hub antigo com 3 cards), e o card BGI do hub aponta para **repo externo** (boi-gordo-portfolio), saindo do ecossistema. O usuário percebe claramente que mudou de app.
3. **Ações destrutivas com prompt()/confirm() nativos.** `rolar()` no bgi encadeia 3 prompts + 1 confirm; erros via `alert()`. Sem undo, sem validação, estética de 1999 — e o bgi ainda grava escritas sequenciais sem transação (dívida já conhecida).
4. **Feedback inconsistente.** Erro: `alert()` (bb, ops), `.msg` inline verde (bgi), texto no subtitle (painel). Sucesso: às vezes ✓ inline, às vezes alert, às vezes nada (só recarrega).
5. **Loading sem padrão.** "Carregando..." escrito no subtítulo; sem skeletons, sem spinners, sem estados de erro visuais.
6. **Empty states improvisados.** `<td colspan=N>` com texto, alguns com 🎉, um com instrução operacional ("registre pelo Juan no grupo").
7. **Tabelas cruas.** Nenhuma tabela tem ordenação, filtro, busca, seleção, paginação ou exportação — e são o coração do sistema.
8. **Atualização manual.** Botão "↻ Atualizar" em cada página; sem auto-refresh nem indicação de dado obsoleto.
9. **Formatos de data misturados.** `dd/mm/aa` (fmtD), `dd/mm/aaaa hh:mm` (fmtDT), "há X min" — sem regra de quando usar cada um. `fmtN` tem default 0 casas no bb/painel e 2 no bgi.
10. **Sem dark mode** em nenhum app.

## 5. Componentes e código duplicados

**CSS copiado 4–6×** (bb, bgi, painel, ops; parcial em index/central): reset `*`, `body`, `h1/h2`, `.card`, `.kpis/.kpi`, `table/th/td/.num/.pos/.neg`, `.badge/.b-*`, `.btn/.sec/.mini`, `input/select`, `.login-box`, `.topbar`, `.err`, `.footer`, `.scroll`.

**JS copiado:**

- `fmtR$`, `fmtN`, `fmtD`, `cls` — 3× (bb, bgi, painel); `fmtDT`, `esc` só no ops; `addDias` só no bb; Confinex tem `fmtData` próprio.
- Criação do client Supabase + URL + chave — 6 arquivos.
- Fluxo de auth (`entrar/sair/iniciar/getSession`) — 4×.
- Render de KPIs (`map` → div.kpi) — 4×, com pequenas variações.
- Botão "⌂ Central" inline — 4×.

## 6. Oportunidades de reutilização — arquitetura proposta

```
/design
  tokens.css        ← cores, espaçamento, radius, sombras, tipografia (CSS vars, com [data-theme=dark])
  components.css    ← card, kpi, badge, btn, input, tabela, topbar, login, toast, modal, empty, skeleton
DESIGN.md           ← princípios, escalas, catálogo de componentes (fonte única da verdade)
/js
  cfagro-core.js    ← client Supabase único, fmt*, esc, addDias, auth guard + tela de login única
  cfagro-shell.js   ← injeta sidebar fixa + header em qualquer página (lê um manifest de navegação)
  cfagro-ui.js      ← toast(), confirmar() (modal), renderKpis(), renderTabela() (ordenação/filtro/busca/export)
```

Cada página vira: `<link tokens+components>` + `<script core+shell>` + seu conteúdo. Continua single-file na lógica, mas com fundação comum. Nenhum build, nenhum package.json, deploy intocado.

Escalas a definir no tokens.css (proposta): radius 6/10/14/999; espaçamento 4/8/12/16/24/32; tipografia 11/12.5/14/17/22; sombra única `--shadow-1` e `--shadow-2`; fonte: **decidir entre manter system (velocidade, zero request) ou adotar a Plus Jakarta Sans do Confinex como identidade** — recomendo system para UI de dados e resolver o Confinex na fase 4.

## 7. Navegação proposta

Sidebar fixa injetada pelo `cfagro-shell.js` em todas as páginas (MPA com shell persistente — visualmente idêntico a um SPA, pois o layout não muda entre navegações):

```
CFAgro
  Visão Geral          → home (novo index = dashboard)
OPERAÇÕES
  Confinex             → confinex.html
  BGI                  → bgi.html (unificado — ver decisão abaixo)
  Boi Balança          → bb.html
  Financeiro           → seção da home ou página futura
GESTÃO
  Pendências           → hoje vive no painel; extrair p/ página própria depois
  Documentos / Eventos → futuro
SISTEMA
  Agentes / Ops        → ops.html
```

Decisões embutidas:

- **index.html deixa de ser menu de cards e vira a Home/dashboard** — absorve os KPIs e tabelas do painel.html (posição financeira, B3, exposição, estoque, eventos, pendências, caixa, alertas). painel.html passa a redirecionar para a home.
- **central.html aposentada**: vira redirect para `./`. Corrigir o link do bgi.html imediatamente (custo zero).
- **BGI e Portfolio B3 (decisão concluída):** `bgi.html` permanece como gestão operacional de hedge por lote; o Portfolio B3 permanece como app CFAgro separado. Os dois têm itens próprios no menu, navegam na mesma janela e não se repetem dentro das páginas.
- **Login único**: o guard do cfagro-core.js mostra uma única tela de login padronizada em qualquer página protegida; sessão já é compartilhada hoje.

## 8. Plano de migração (sem quebrar nada)

| Fase | Entrega | Risco | Critério de pronto |
|---|---|---|---|
| **0 — Correções imediatas** | bgi.html volta p/ `./`; central.html → redirect; padronizar `<title>` "CFAgro — {módulo}" | nulo | links certos, nada visual muda |
| **1 — Fundações** | `design/tokens.css`, `design/components.css`, `js/cfagro-core.js`, `DESIGN.md` | nulo (nada consome ainda) | arquivos publicados e documentados |
| **2 — Adoção nos satélites** | bb, bgi, painel, ops, index passam a consumir os arquivos; deletar CSS/helpers duplicados; 1 página por commit | baixo | diff visual ≈ zero; 5 `:root` viram 1 |
| **3 — Shell + Home** | `cfagro-shell.js` (sidebar + header) em todas as páginas; index vira dashboard (fusão do painel) | médio | navegar entre módulos sem "sensação de troca de app" |
| **4 — Confinex no DS** | Trocar objeto `T` e fontes do bundle pelos tokens (bundle é legível; já usa CSS vars em 5 pontos); embutir no shell | médio-alto (fonte não está no repo) | Confinex indistinguível dos demais |
| **5 — Componentes ricos** | `cfagro-ui.js`: tabela com ordenação/filtro/busca/export; toast/modal substituindo alert/confirm/prompt (começar pelo `rolar()` do bgi); skeletons; ícones Lucide no lugar de emojis | baixo, incremental | nenhuma ação destrutiva via prompt nativo |
| **6 — Dark mode** | `[data-theme=dark]` no tokens.css + toggle no header | baixo (se fases 2–4 feitas) | ambos os temas em todos os módulos |

Regras da migração: nunca misturar mudança visual com mudança de lógica no mesmo commit; uma página por vez; a página antiga só perde o CSS inline quando a versão com DS estiver conferida lado a lado.

## 9. Regra permanente

> **Antes de criar qualquer tela, componente ou estilo novo: verificar se já existe equivalente em `DESIGN.md` / `design/` / `js/cfagro-*.js`. Se existir, reutilizar. Se não existir, criar no Design System (não na página) e documentar no DESIGN.md.**

(Registrada também no CLAUDE.md do repo.)

## 10. Decisões em aberto para o Pablo

1. **Fonte única**: system (atual dos satélites) ou Plus Jakarta Sans (atual do Confinex)?
2. **BGI**: consolidar no repo ou manter o portfolio externo como módulo separado?
3. **Confinex sem fonte no repo**: vale recuperar/recriar o `src/` antes da fase 4, ou editamos o bundle diretamente?
4. **Pendências/Documentos/Eventos** como páginas próprias (sidebar GESTÃO) ou seções da Home?
