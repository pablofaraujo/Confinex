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
| `index.html` | Visão Geral — Home/dashboard do ecossistema (total de cabeças, confinados, Fazenda Ametista, Parceria Ricardo e cobertura de arrobas por confinamento) |
| `confinex.html` + `confinex-app.latest.js` + `confinex-app.mobile.js` | Confinex — simulador de compra/confinamento/revenda. O arquivo `latest` preserva o bundle legível; `mobile` contém React 19 e o app empacotados em um único script compatível com Safari móvel, sem import map ou módulos externos |
| `fazenda-ametista.html` | Fazenda Ametista — ledger de entrada/saída de cabeças do rebanho próprio (gado que ainda não foi pra cocho/confinamento nem pra parceria). KPI de estoque atual = soma de entradas − saídas. Tabela `fazenda_ametista`. Existia só como arquivo solto no Drive desde 12/07/2026 (nunca commitado, tabela nunca criada) até ser trazido ao repo em 18/07/2026 — histórico retroativo de movimentação ainda não foi lançado |
| `confinamento.html` | Confinamento — visão operacional ao vivo por confinamento/parceiro: lotes, currais, entradas (GTA/NF/peso/perda de transporte), custos por categoria, fechamentos previsto×realizado. Lê `operacoes`/`confinamentos`/`entradas_confinamento`/`custos_operacao`/`eventos_operacao`/`fechamentos_operacao` (populadas em 07/07/2026 por outra sessão, sem UI até esta página — `eventos_operacao`/`fechamentos_operacao` ainda vazias) |
| `bb.html` | Boi Balança — giro rápido balança→gancho |
| `bgi.html` | BGI — posições de hedge B3 (módulo principal; o portfolio externo `boi-gordo-portfolio` virou link secundário na topbar) |
| `ocr-pesagem.html` | OCR Pesagem — leitura de tickets de balança (tema escuro próprio, ainda fora do DS) |
| `painel-boi-gordo.html` | Painel Boi Gordo — arroba CEPEA/B3, bezerro, relação de troca, curva futura BGI, manchetes e contexto de mercado (dados estáticos no DS, sem Supabase; atualizados por automação — ver Armadilhas) |
| `painel.html` | LEGADA — redirect para `index.html` (conteúdo migrou para a Home) |
| `ops.html` | Ops — heartbeats de agentes + fila de missões `vps_briefings` para o Claude Code da VPS |
| `central.html` | LEGADA — agora só redirect para `index.html` |
| `abate.html` | Abate — cabeçalho do abate + romaneio animal a animal (`abate_animais`), com leitura OCR da folha do frigorífico |

## Backend

- **Supabase** `fkmdzwjmjlmxqotznvgq.supabase.co` — usado por bb, bgi, painel, ops, abate, confinamento e index (auth email/senha, chave publicável hardcoded, RLS protege). Tabelas principais: `operacoes` (status inclui `liquidada` desde jul/2026 — compra paga + venda conciliada; `confinamento_id` linka a `confinamentos`), `compras`, `vendas`, `posicoes_hedge`, `alocacoes_hedge`, `cotacoes_bgi`, `pendencias_documentos`, `acertos`, `fluxo_caixa` (jul/2026: conciliação liga `operacao_id` ao lançamento real de entrada), `promissorias`, `vps_briefings`, `ecossistema_inventario`, `ecossistema_status`, `abates`, `abate_animais` (romaneio por animal); views `v_exposicao_hedge`, `v_estoque_atual`. **Grupo `confinamento` (criado 07/07/2026, sem UI até `confinamento.html`)**: `confinamentos` (cadastro dos parceiros/confinadores), `entradas_confinamento` (curral, peso embarque/chegada, perda de transporte, GTA/NF por lote — pode ter várias entradas por operação), `custos_operacao` (frete/trato/financeiro/baldeio/adiantamento_juros), `eventos_operacao` e `fechamentos_operacao` (ainda vazias). Também `transacoes_banco` e `emprestimos` (conciliação bancária Sicoob, jul/2026, sem UI ainda), `notas_fiscais_xml_raw` (staging bruto de NFe/NFSe, não curado), `fazendas` (registro de propriedades, ainda vazia) e `fazenda_ametista` (ledger entrada/saída do rebanho próprio, criada 18/07/2026 — ver `fazenda-ametista.html`).
- **Confinex está em transição para o Supabase**: localStorage + Google Sheets continuam como compatibilidade temporária; testes nomeados autenticados são gravados em `confinex_testes`. Agentes submetem negócios como `rascunho` pela RPC `submeter_negocio_confinex`; a fila em **Operações → Confinamento** permite aprovar (`aprovar_negocio_confinex`) ou recusar (`recusar_negocio_confinex`). A recusa preserva a avaliação como `cancelado` para auditoria, sem criar lote operacional. A estimativa original fica congelada em `confinex_estimativas`. Consolidação previsto × realizado usa `confinex_consolidacoes` + `confinex_desvios`. Migrações: `supabase/migrations/202607200001_confinex_avaliacoes.sql`, `202607200002_confinex_aprovacoes.sql` e `202607210001_confinex_recusas.sql`.

## Deploy

Push na `main` → workflow `deploy.yml` publica o repositório inteiro no GitHub Pages (sem build). Cuidado: tudo que estiver commitado fica público.

## Regra permanente — Design System

**Antes de criar qualquer tela, componente ou estilo novo: verificar se já existe equivalente no Design System (`DESIGN.md`, `design/`, `js/cfagro-*.js`). Se existir, reutilizar. Se não existir, criar no Design System — nunca na página — e documentar no `DESIGN.md`.** Nenhum layout novo nasce fora do DS. Plano e auditoria: `docs/auditoria-ui-ux.md`.

## Convenções

- Tudo em pt-BR: código, variáveis, UI, commits.
- Single-file por app na lógica, mas visual e infra compartilhados: páginas carregam `design/tokens.css` + `design/components.css` + `js/cfagro-shell.js` (cabeçalho azul-marinho, logo amarelo, sidebar e navegação móvel). As páginas Supabase também carregam `js/cfagro-core.js`, salvo `ops.html`, que mantém apenas seu client local por motivo legado, mas reutiliza integralmente os estilos do DS. `confinex.html` usa o shell compartilhado e preserva sua lógica React/Sheets própria.
- Fonte padrão: Inter (via tokens.css). Identidade visual global: cabeçalho azul-marinho, amarelo do logo como destaque, superfícies brancas e sem gradientes. Dark mode preparado em `[data-theme=dark]`, sem toggle ainda.
- Botão flutuante "⌂ Central" (`.voltar-central`) em toda página-satélite.
- Ações destrutivas com `confirm()`/`prompt()` nativos.
- Constantes de domínio: **1 @ = 15 kg**; **1 contrato BGI = 330 @**; RC padrão 50–53%; 65 bois/carreta (macho) / 70 (fêmea); limite de capim padrão 300 kg; Funrural 0,2% default (Confinex e simulador do bb — alinhados desde jul/2026).

## Armadilhas conhecidas

- O fonte do Confinex não está no repo — `confinex-app.latest.js` é o bundle legível usado como entrada e `confinex-app.mobile.js` é o pacote de execução autocontido. Ao editar o `latest`, regenere o `mobile` com React 19 e alvo Safari 14 antes do deploy.
- Cotações B3: cascata frágil (API B3 → histórico → Yahoo `{contrato}.SA` → scrape CEPEA via allorigins.win) com heurística `extrairNumeroB3` que aceita qualquer número entre 100 e 800.
- `rolar()`/`encerrar()`/`encerrarParcial()` no bgi.html fazem escritas sequenciais sem transação.
- `central.html` legada e `bgi.html` órfão podem divergir do resto.
- `painel-boi-gordo.html` nasceu como artifact do Cowork (a tarefa agendada `atualiza-painel-boi-gordo` ainda escreve nesse artifact separado, não neste arquivo do repo) — até a automação ser redirecionada para editar/commitar este arquivo, o conteúdo aqui é uma cópia estática que só atualiza manualmente.
