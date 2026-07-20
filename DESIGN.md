# DESIGN.md — Design System CFAgro

**Fonte única da verdade visual do ecossistema.** Identidade aprovada: azul-marinho, amarelo do brinco CFAgro, superfícies brancas e cinzas neutros; densidade de dados alta, sombras sutis, zero gradiente e zero animação decorativa.

> **Regra permanente:** antes de criar qualquer tela, componente ou estilo, verificar se já existe equivalente aqui. Se existir, reutilizar. Se não existir, criar AQUI (nunca na página) e documentar neste arquivo.

## Arquivos

| Arquivo | Conteúdo |
|---|---|
| `design/tokens.css` | Cores, tipografia, radius, espaçamento, sombras (CSS variables). Dark mode preparado via `[data-theme=dark]`. |
| `design/components.css` | Todos os componentes visuais. Requer tokens.css antes. |
| `js/cfagro-core.js` | Client Supabase único, formatadores (`fmtR$`, `fmtN`, `fmtD`…) e auth compartilhado (`CFAgro.authInit`). |
| `js/cfagro-shell.js` | Sidebar fixa de navegação (mesma em todos os módulos). Carregar com `defer`; o manifest de navegação vive dentro dele. |

## Como montar uma página

```html
<link rel="stylesheet" href="./design/tokens.css">
<link rel="stylesheet" href="./design/components.css">
<script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2/dist/umd/supabase.min.js"></script>
<script src="./js/cfagro-core.js"></script>
<script src="./js/cfagro-shell.js" defer></script>
```

Página protegida: markup de login com ids `#login #app #email #senha #loginErr` e, no fim do script, `CFAgro.authInit(carregar)` — isso cria `entrar()`/`sair()` globais e mostra `#app` se já houver sessão.

Larguras: padrão 1280px; `<body data-w="md">` = 1100px; `<body data-w="sm">` = 640px (hub).

## Princípios

1. Rapidez e clareza acima de estética — a UI é usada horas por dia.
2. Nenhuma cor, radius ou sombra hardcoded: sempre `var(--token)`.
3. Nada de gradientes, animações decorativas ou efeitos.
4. Desktop primeiro; mobile só para consulta.
5. Tabelas são o coração do sistema — legibilidade numérica (`.num` usa tabular-nums).
6. pt-BR em tudo.

## Tokens (resumo — valores em tokens.css)

- **Cores base:** `--bg --card --field --border --border-soft --text --muted`
- **Marca:** `--navy --navy-2 --yellow --yellow-soft --brand --brand-hover --brand-text`
- **Semânticas:** `--green/--greenbg/--green-text` (positivo/ação), `--red/...` (negativo/perigo), `--amber/...` (atenção), `--blue/...` (informativo)
- **Fonte:** `--font` = Inter, fallback system
- **Tamanhos:** `--fs-11` (labels/th/badges) · `--fs-12` (tabelas) · `--fs-13` (botões/corpo) · `--fs-14` (inputs) · `--fs-17` (sub do h1) · `--fs-20` (KPI) · `--fs-22` (h1)
- **Radius:** `--r-sm` 8 (botões/inputs) · `--r-md` 12 (cards) · `--r-lg` 16 (destaque) · `--r-pill`
- **Sombras:** `--shadow-1` (cards) · `--shadow-2` (flutuantes)

## Catálogo de componentes (components.css)

| Componente | Classes | Uso |
|---|---|---|
| Card | `.card` (+ `.scroll` p/ tabela larga) | container padrão |
| KPI | `.kpis > .kpi > .l/.v/.d` | indicadores no topo |
| Tabela | `table/th/td`, `.num`, `.pos/.neg`, `td.wrap` | dados operacionais |
| Badge | `.badge.b-green/.b-amber/.b-red/.b-blue` | status |
| Botão | `.btn` (+ `.sec .mini .warn`) | ações; funciona em `<a>` |
| Campo | `.fld > label + input/select/textarea` | formulários |
| Feedback | `.err` (erro) `.msg` (sucesso inline) | abaixo de forms |
| Login | `.login-box` | tela de entrada padrão |
| Topbar | `.topbar > .ident / .acoes`, `.logo-img` | cabeçalho de página |
| Grids | `.grid.g6`, `.grid2`, `.simgrid` | layouts de form/painel |
| Mini-resultado | `.res > .r > .l/.v` | saída de simuladores |
| Hub | `.head .apps .app .ico .status .st .hub-sub` | cards da Central |
| Visão Geral | `.kpis-overview .ecossistema-card .eco-section .eco-row .eco-progress .eco-metricas` | resumo de rebanho e cobertura por área |
| Aviso | `.aviso` | banner de atenção |
| Rodapé | `.footer` (alias `.foot`) | assinatura da página |
| Detalhes | `details/summary/pre`, `.fluxo` | conteúdo expansível (Ops) |
| Voltar | `.voltar-central` | botão flutuante ⌂ (oculto quando o shell está ativo) |
| Shell | `.shell-top .shell-brand .shell-side .shell-sec .shell-sep .shell-link(.ativa) .shell-content` | cabeçalho azul-marinho + sidebar fixa; montados pelo cfagro-shell.js |

## Convenções de comportamento

- Título da aba: `CFAgro — {Módulo}`.
- Toda página-satélite tem `.voltar-central` apontando para `./`.
- Loading: texto no `.sub` ("Carregando..."). Empty state: `<td colspan=N>` com frase curta. (Skeletons e toasts entram na fase 5.)
- Datas: `fmtD` (dd/mm/aa) para datas de negócio; `fmtDT` para timestamps; "há X min" só para heartbeats.
- Moeda: `fmtR$` (inteiro) para totais; `fmtR$2` para valores unitários.

## Roadmap do DS

~~Fases 3 e 4~~ feitas: shell de navegação, index como Home/dashboard e adoção do padrão por Confinex, OCR Pesagem e Ops. Fase 5: tabela rica (ordenar/filtrar/buscar/exportar), toasts/modais no lugar de alert/confirm/prompt, ícones Lucide. Fase 6: toggle dark mode. Detalhes: `docs/auditoria-ui-ux.md`.
