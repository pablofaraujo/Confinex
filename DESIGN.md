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

As ações de sessão **Atualizar** e **Sair** aparecem somente na Visão Geral. Os
módulos não repetem esses controles. O Portfolio B3 é um app CFAgro separado,
mas navega na mesma janela e mantém seu item ativo no shell.

O subtítulo do módulo descreve sua finalidade; não repete horário nem quantidade
que já aparece nos quadros. A Visão Geral pode informar a atualização do resumo,
e telas de mercado podem informar a data da fonte quando isso muda a decisão.

## Como montar uma página

```html
<link rel="stylesheet" href="./design/tokens.css">
<link rel="stylesheet" href="./design/components.css">
<script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.112.3/dist/umd/supabase.min.js"></script>
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
| Card | `.card` (+ `.scroll` p/ tabela larga) | container padrão; `.scroll` limita a largura ao conteúdo e mantém a rolagem dentro do card |
| KPI | `.kpis > .kpi > .l/.v/.d` | indicadores no topo |
| KPI monetário longo | `.kpis.kpis-dinheiro > .kpi` | valores em reais que precisam de cartões mais largos e quebra de segurança |
| Tabela | `table/th/td`, `.num`, `.pos/.neg`, `td.wrap` | dados operacionais |
| Badge | `.badge.b-green/.b-amber/.b-red/.b-blue` | status |
| Botão | `.btn` (+ `.sec .mini .warn`) | ações; funciona em `<a>` |
| Campo | `.fld > label + input/select/textarea` | formulários |
| Campo compacto | `.fld.compacto`, `.grid-campos-compactos` | formulários densos sem esconder rótulos ou orientações |
| Formulário com identificação lateral | `.form-com-aside > .form-principal + .form-aside` | campos operacionais à esquerda e metadados compactos à direita; empilha no celular |
| Métrica compacta | `.metrica-compacta` | resultado calculado curto e somente leitura |
| Painel retrátil | `.painel-retratil > .painel-retratil-resumo + .painel-retratil-corpo` | ações importantes, mas pouco frequentes, recolhidas por padrão |
| Feedback | `.err` (erro) `.msg` (sucesso inline) | abaixo de forms |
| Validação guiada | `.aviso-validacao`, `.fld.campo-incompleto`, `.btn:disabled` | aviso de campos faltantes, destaque no formulário e bloqueio pontual da ação dependente |
| Comparação de versões | `.comparacao-versoes`, `.grade-versoes`, `.versao-card`, `.versao-linha` | alternativas preservadas lado a lado, diferenças destacadas e escolha sem gravação automática |
| Login | `.login-box` | tela de entrada padrão |
| Topbar | `.topbar > .ident / .acoes`, `.logo-img` | cabeçalho de página |
| Grids | `.grid.g6`, `.grid2`, `.simgrid` | layouts de form/painel |
| Mini-resultado | `.res > .r > .l/.v` | saída de simuladores |
| Arquivo do estudo | `.sec` + `.g4` + `details/summary` | importar, baixar, versionar e consultar a cópia online sem expor configuração técnica |
| Relatório para PDF | `.report-print .report-page .report-grid .report-item .report-table` | relatório comparativo oculto na tela, isolado do restante da página e formatado em A4 paisagem sem folhas vazias |
| Hub | `.head .apps .app .ico .status .st .hub-sub` | cards da Central |
| Visão Geral | `.kpis-overview .ecossistema-card .eco-section .eco-row .eco-progress .eco-metricas` | resumo de rebanho e cobertura por área |
| Aviso | `.aviso` | banner de atenção |
| Rodapé | `.footer` (alias `.foot`) | assinatura da página |
| Detalhes | `details/summary/pre`, `.fluxo` | conteúdo expansível (Ops) |
| Voltar | `.voltar-central` | botão flutuante ⌂ (oculto quando o shell está ativo) |
| Shell | `.shell-top .shell-brand .shell-side .shell-sec .shell-sep .shell-link(.ativa) .shell-content` | cabeçalho azul-marinho + sidebar fixa; montados pelo cfagro-shell.js |

O módulo **CRM de Gado** reutiliza exclusivamente KPIs, cards, tabelas, badges,
campos, `simgrid` e `grid2`. Ofertas incompletas usam badge de atenção e mantêm
os campos ausentes escritos na tabela; o cadastro informa no rodapé que não
gera efeito operacional.

### Ícones do shell

Os itens do menu usam SVGs lineares locais, sem emojis, CDN ou fonte externa.
O contrato visual é 18 px, traço 1,75 px, `currentColor`, sem preenchimento ou
sombra. O estado normal usa cinza-azulado; hover usa fundo neutro; o item ativo
usa amarelo CFAgro suave e barra lateral amarela. O texto acessível permanece
sempre visível ao lado do ícone. A identidade CFAgro fica no brinco circular do
cabeçalho, não em variações dos ícones.

Fazenda usa uma porteira; confinamento usa um curral; parceiros usam uma pessoa
e o resumo de parcerias usa o aperto de mãos. As ações globais “Atualizar” e
“Sair” ficam no cabeçalho do shell e só aparecem após confirmação da sessão.

## Convenções de comportamento

### Cabeçalho das áreas

- A **Visão Geral** é a referência visual para todas as áreas: título à esquerda
  e subtítulo imediatamente abaixo, dentro de `.topbar > .ident`.
- O título usa 28 px no desktop e 24 px no celular, peso 700 e `--text`.
- O subtítulo usa `--fs-13`, peso 400 e `--muted`, sem caixa alta nem
  espaçamento decorativo entre letras.
- O brinco CFAgro aparece somente no cabeçalho global do shell. As áreas não
  repetem logo, nome do responsável ou marca dentro do próprio cabeçalho.
- Ações próprias da tela permanecem em `.topbar > .acoes`, alinhadas ao topo.

No celular, o grid do shell usa coluna `minmax(0, 1fr)` e todos os contêineres
de conteúdo têm `min-width: 0`. Tabelas largas devem permanecer dentro de
`.scroll`; a rolagem horizontal pertence ao card ou à faixa de navegação, nunca
ao documento inteiro.

- Título da aba: `CFAgro — {Módulo}`.
- Toda página-satélite tem `.voltar-central` apontando para `./`.
- Loading: texto no `.sub` ("Carregando..."). Empty state: `<td colspan=N>` com frase curta. (Skeletons e toasts entram na fase 5.)
- Datas: `fmtD` (dd/mm/aa) para datas de negócio; `fmtDT` para timestamps; "há X min" só para heartbeats.
- Moeda: `fmtR$` (inteiro) para totais; `fmtR$2` para valores unitários.

### Fila de revisões

- O painel usa KPIs e filtros rápidos com nomes operacionais: aguardando revisão, campos faltantes, aguardando confirmação, em andamento, concluídos, precisa conferir e rejeitados/cancelados.
- O contexto é apresentado pelo nome do grupo. IDs técnicos e JSON não aparecem na interface.
- Campo obrigatório ausente usa `.fld.campo-incompleto`, `aria-invalid="true"` e um aviso `.aviso-validacao` com atalhos que levam o foco ao campo. O aviso informa os nomes humanos dos campos.
- A validação bloqueia somente **Preparar promoção operacional**. **Salvar ajustes** continua disponível para permitir a correção progressiva.
- O histórico traduz estados para linguagem operacional e mostra data, responsável, destino, resultado e ID do registro criado quando existir.
- Quando houver mais de uma leitura plausível, a revisão compara as versões, destaca somente as diferenças e explica por que cada alternativa pode fazer sentido. **Usar esta versão** apenas preenche os campos editáveis; salvar, aprovar e promover continuam separados.
- Rejeitar exige motivo; devolver para confirmação preserva os dados; cada decisão cria um evento legível. Detalhes internos continuam no banco para auditoria, nunca como JSON bruto na tela.

## Roadmap do DS

~~Fases 3 e 4~~ feitas: shell de navegação, index como Home/dashboard e adoção do padrão por Confinex, OCR Pesagem e Ops. Fase 5: tabela rica (ordenar/filtrar/buscar/exportar), toasts/modais no lugar de alert/confirm/prompt. Ícones lineares locais do shell concluídos; ícones Lucide externos continuam fora do escopo. Fase 6: toggle dark mode. Detalhes: `docs/auditoria-ui-ux.md`.
