# Arquitetura do ecossistema CFAgro

Atualizado em 2026-07-23.

## Apps e navegação

- **index.html** — Visão Geral e página inicial. Mostra quatro KPIs na ordem: total de cabeças, confinadas, Fazenda Ametista e Parceria Ricardo. A seção "Áreas do ecossistema" detalha cabeças e arrobas descobertas por confinamento, sexo do rebanho da Ametista quando disponível e bois ativos de Ricardo.
- **confinex.html** — shell do Confinex: carrega o Design System e a navegação compartilhada, define `window.CONFINEX_SHEETS_API_URL` (Apps Script) e inicia `confinex-app.mobile.js`, pacote autocontido com React 19 e alvo Safari 14. O boot só é considerado concluído quando `#root` recebe a interface; Supabase e `cfagro-core.js` são carregados depois do evento `load`, sem bloquear a primeira pintura no Safari/iPhone. `confinex-app.latest.js` permanece como bundle legível de manutenção.
- **bb.html** — Boi Balança: simulador pré-compra + lotes + contas a pagar/receber + pendências GTA/NF.
- **abate.html** — Abate: cabeçalho por abate (`abates`: data, frigorífico, origem, romaneio, GTA) + romaneio animal a animal (`abate_animais`) com leitura OCR do romaneio do frigorífico (`aprovarFolha`). Não está no design system compartilhado (sem sidebar).
- **bgi.html** — BGI Posições: exposição por lote, travas, encerramento, rolagem, cotações, basis. Não é linkado por nenhuma página (o card BGI aponta para o repo externo `boi-gordo-portfolio`); acesso por URL direta. Ainda linka de volta para `central.html` (legada).
- **painel.html** — Painel Vivo: KPIs, exposição BGI, posições B3 (hedge × especulação), estoque, pendências, acertos, fluxo de caixa com Chart.js.
- **ops.html** — Ops: heartbeats (`ecossistema_status`, vivo se ≤10 min), inventário (`ecossistema_inventario`), "Ponte VPS" — fila `vps_briefings` (status pendente/em_andamento/concluido) consumida por um Claude Code rodando na VPS via cron (~5–10 min).
- **central.html** — versão legada da Central (3 cards). Ficou para trás no commit `fc9b66c`.
- **painel-boi-gordo.html** — Painel Boi Gordo: KPIs de mercado (arroba CEPEA/B3, bezerro, relação de troca, exportação), curva futura BGI com gráfico Chart.js, manchetes e contexto. Sem Supabase/login — dados vêm de um bloco JSON estático (`#painel-data`) embutido no HTML. Integrado ao DS/shell nesta sessão; antes vivia como artifact solto do Cowork.
- **financeiro.html** — visão autenticada e somente leitura de `fluxo_caixa`, `emprestimos`, `promissorias` e `transacoes_banco`. Separa previsto/realizado e a pagar/receber, calcula saldos parciais, reúne vencimentos, dívidas e renegociações já representáveis nas fontes e deriva lembretes para 30 dias. Não executa pagamentos, baixas, renegociações nem conciliação.
- **pendencias.html** — agregador autenticado e somente leitura de itens abertos em `operation_drafts`, `pending_actions` e `pendencias_documentos`; encaminha correções para Revisões.
- **eventos.html** — histórico autenticado e somente leitura de `eventos`, com filtros e projeção humana que omite JSON e identificadores técnicos.
- **Acompanhamento** — `confinamento.html` consolida em modo somente leitura entradas, cabeças, eventos recentes e custos por lote. O contrato puro de eventos (incluindo consumo, pesagem, morte, transferência, cobrança e fechamento) fica em `js/confinex-acompanhamento.mjs`; não há migração nem escrita operacional nessa camada.

Padrão: cabeçalho azul-marinho com marca Confinex e sidebar branca no desktop; no celular, a navegação vira uma faixa horizontal rolável. O botão legado "⌂ Central" fica oculto quando o shell está ativo. Favicon `confinex-logo.jpg`.

**Shell compartilhado** (`js/cfagro-shell.js`): injeta cabeçalho e navegação com links absolutos (inclusive para o repo externo `boi-gordo-portfolio`) e é carregado com `design/tokens.css`/`design/components.css` por todos os módulos ativos. O Portfólio B3 navega na mesma janela e se identifica como item ativo no repositório externo; somente Datamars Livestock, AgroNota e Portal do Produtor IMA/SIDAGRO abrem nova aba, por serem ferramentas externas independentes. Em produção usa a base canônica do GitHub Pages; em localhost resolve links contra o servidor local, permitindo auditoria antes do deploy. `ops.html` mantém o client Supabase local, porém não redefine mais tokens nem componentes visuais.

## Backend Supabase

URL `https://fkmdzwjmjlmxqotznvgq.supabase.co`, chave publicável hardcoded em cada página (RLS protege; rotação exige editar N arquivos). Auth `signInWithPassword`, sessão persistida.

Tabelas/views por app:
- **bb**: `operacoes` (`tipo_negocio='boi_balanca'`), `compras` (`prazo_dias`, `data_pagamento`, `pago`), `vendas` (`prazo_recebimento` = **data prevista** de recebimento, `recebido`, `funrural`, + `outros_custos`/`custos_obs` desde jul/2026), `negocios_boi_balanca` (desde jul/2026: previsão pré-lote — `status` previsto/confirmado/cancelado, campos do simulador + `operacao_id`/`confirmado_em` quando confirmado), `pendencias_documentos`, `contatos`. Pendências excluem status `validado`/`cancelado`.
- **bgi**: `v_exposicao_hedge`, `posicoes_hedge` (+ `alocacoes_hedge` com `resultado_creditado` rateado), `cotacoes_bgi` (contrato ou `FISICO`; basis = físico − futuro).
- **painel**: as anteriores + `v_estoque_atual`, `acertos`, `fluxo_caixa`; `posicoes_hedge.categoria` distingue hedge × `especulacao`; filtro `.or('status.in.(aberta,rolada),origem.eq.bgi-portfolio')` importa posições do app bgi-portfolio.
- **ops**: `ecossistema_inventario`, `ecossistema_status`, `vps_briefings`.
- **revisões/promoções**: `operation_drafts` e `pending_actions` guardam rascunhos e ordens auditáveis; `revisoes.html` prepara a promoção e `tools/promocao_operacional.py` executa a gravação em `compras`, `vendas`, `pesagens_caderno` ou `abates` somente após confirmação `PROMOVER <id>`. Antes do insert, a pendência é assumida por compare-and-set (`em_execucao`), impedindo dois workers; falhas posteriores ao insert preservam o ID operacional em `erro_pos_gravacao` e exigem reconciliação, nunca repetição automática. `tools/reconciliar_compras_telegram.py` transforma auditorias do Juan em backlog de triagem separado pelo nome do grupo: elimina ocorrências repetidas por conversa, não leva trechos técnicos brutos para a interface, marca dados não confirmados como pendentes e usa UUID determinístico para impedir nova inserção do mesmo candidato. Sem `--executar --limite N`, apenas mostra o plano; com execução, a única tabela permitida é `operation_drafts`.
- **idempotência de compras aplicada e executor implantado**: `supabase/migrations/202607250001_compras_idempotencia.sql` adicionou `compras.idempotency_key` nula e índice único parcial. A conferência antes/depois preservou compras, RLS, políticas e permissões. O cliente e o executor foram implantados na VPS após backup e testes simulados; uma prévia real confirmou zero escrita e não expôs o registro comercial. O cliente reconcilia timeout pela chave, retorna `duplicate` para os mesmos dados, rejeita a mesma chave com dados diferentes e não repete envio incerto. O contrato e a homologação estão em [`docs/idempotencia-compras.md`](idempotencia-compras.md).
- **contratos em pré-análise, sem automação externa**: `tools/contratos_workflow.py`, instalado também como skill do Wey depois de backup e testes fictícios, calcula SHA-256, detecta repetição, compara campos do documento com o negócio e os termos aprovados, propõe organização privada no Drive e classifica riscos/Finpec. A saída é sempre dry-run; envio, assinatura, gov.br, garantia e qualquer escrita externa permanecem bloqueados. Consulte [`docs/contratos-automatizados.md`](contratos-automatizados.md).
- **Sheets formalmente legado**: o inventário privado do Drive confirmou planilhas de confinamento, mas nenhum arquivo foi aberto ou alterado. Sheets/Apps Script permanece como compatibilidade e histórico durante a transição; Supabase é a fonte dos dados operacionais confirmados. O gate de migração e reversão está em [`docs/google-sheets-legado.md`](google-sheets-legado.md).
- **contexto por grupo**: `contextos_canais` registra a chave canônica, o nome humano, o canal, o ID técnico e o escopo. `operation_drafts`, `pending_actions`, `eventos` e `memorias_agentes` repetem o vínculo necessário à auditoria. Nas memórias, `escopo` mantém o alcance funcional existente e `contexto_escopo` identifica grupo/conversa direta/sistema. O frontend usa somente `contexto_nome`; `origem_conversa_id` permanece oculto e nunca recebe um nome humano. A migração aditiva e o dry-run protegido estão em [`docs/contextos-por-grupo.md`](contextos-por-grupo.md).
- **abate**: `abates` (cabeçalho), `abate_animais` (romaneio por animal — schema alinhado jul/2026: `seq`, `descricao`, `classificacao`, `meia_esq`/`meia_dir`, `peso_kg` = carcaça, `vlr_kg`/`vlr_arroba`/`vlr_total`, `bonus`, `penalizacao`, `condenacao`; não guarda peso vivo/balança nem hora de passagem).
- **promissórias** (skill): tabela `promissorias` (numero pk `NNN/AAAA`, credor, cpf, valor, vencimento, praça, negocio_id, status aberta/quitada).
- **gestão somente leitura**: `financeiro.html` lê `fluxo_caixa`, `emprestimos`, `promissorias` e `transacoes_banco`; `pendencias.html` lê `operation_drafts`, `pending_actions` e `pendencias_documentos`; `eventos.html` lê `eventos`. `js/cfagro-gestao.js` recupera contexto humano também de estruturas legadas e aninhadas, descarta JSON/UUID/ID de grupo e associa cada linha a uma área operacional legível. A interface financeira tolera a ausência isolada da conciliação bancária; Pendências tolera a ausência isolada de uma de suas três fontes e mantém as demais disponíveis.
- **modelo financeiro preparado, não aplicado**: `supabase/migrations/202607240001_financeiro_compromissos.sql` propõe, de forma aditiva, `financeiro_compromissos`, `financeiro_parcelas`, `financeiro_pagamentos`, `financeiro_renegociacoes`, `financeiro_lembretes` e a view `v_financeiro_compromissos`. O arquivo não migra dados operacionais, habilita RLS e concede somente `select` a `authenticated`. Ele exige homologação e autorização explícita antes de qualquer aplicação.

## Fila de revisões e promoção operacional

O fluxo seguro é: Telegram/Juan → rascunho separado pelo nome do contexto → revisão visual → correção guiada → aprovação → preparação de uma pendência → confirmação em nova mensagem no mesmo contexto → promoção controlada → dado operacional → evento de auditoria → histórico consultável. Preparar ou aprovar não grava dado operacional.

Para foto ou PDF, a presença de `MediaPath` ou `MediaPaths` obriga Juan a chamar primeiro `arquivo_grupo_router.py`; as ferramentas internas de PDF, imagem e OCR de pesagem não podem anteceder o roteador. O roteador classifica a compra antes de aplicar o fluxo de pesagem, combina o texto da mensagem com os campos visuais e tenta montar o extrato completo. Ele apresenta o que leu, calcula apenas quando houver base suficiente e lista objetivamente os dados ausentes.

O processo do agente permanece em sandbox com escrita limitada à pasta de trabalho. Como esse ambiente não acessa o OCR externo, `compra_documento_ocr.py` entrega a leitura a um trabalhador local supervisionado, que usa OpenClaw/OpenAI fora do sandbox, processa páginas de PDF em paralelo e mantém cache identificado pelo conteúdo do arquivo. O retorno volta ao mesmo roteador; uma indisponibilidade ainda permite tentativa local com Tesseract. O runtime não pede autorização preliminar para esse comando local já confinado. Nenhuma leitura gera escrita automática; a criação de rascunho em `operation_drafts` é apenas uma opção oferecida ao final da conversa.

O handler de pesagem também aceita foto e PDF, usa OpenClaw/OpenAI primeiro e
Anthropic como fallback. Para impedir rejeição de anexos grandes, toda imagem
destinada ao fallback é normalizada para JPEG e reduzida antes do envio; a
bateria da VPS testa esse limite com arquivo sintético e remove o temporário.

`operation_drafts` contém o material revisável; `pending_actions` contém a ordem de promoção e seu estado; `eventos` preserva decisões e resultados legíveis. A promoção admite somente `compras`, `vendas`, `pesagens_caderno` e `abates`. O executor assume a pendência por comparação de estado antes da inserção. Uma falha antes da inserção termina em `erro`; uma falha depois dela termina em `erro_pos_gravacao`, conserva o ID operacional e nunca deve ser executada novamente sem reconciliação.

Para compras, a proteção persistente no banco já está ativa. A chave vem da
pendência confirmada, e timeout sem registro reconciliado nunca provoca novo
`POST` automático. O cliente e o executor já foram implantados na VPS após
backup, testes simulados e prévia real somente leitura, sem promoção nem
persistência da chave.

`memorias_agentes` e `contexto_handoff` dão continuidade ao contexto de Juan e à passagem entre agentes, mas não aprovam promoções nem substituem os registros de auditoria. O vínculo operacional é feito pelos IDs do rascunho, da pendência, do evento e do registro de destino; na interface, a referência humana é sempre o nome do grupo.

Memória permanente aceita somente decisão, preferência, regra, exceção ou
aprendizado reutilizável. Fatos numerados de compra, venda, pesagem e abate
ficam em rascunhos, eventos ou tabelas operacionais. O contrato Juan/Ceci e a
auditoria somente leitura estão em
[`docs/memoria-agentes.md`](memoria-agentes.md).

O contrato das tabelas, o significado dos estados, as ferramentas, os testes e o procedimento de reversão estão em [`docs/fila-revisoes.md`](fila-revisoes.md).

## Validação contínua

`tools/test_ecossistema.py` é a entrada única da bateria permanente. No modo
padrão, reúne testes Python, contratos de segurança, simulações da fila,
verificação sintática do JavaScript de `revisoes.html` e `git diff --check`.
Opcionalmente, compara contagem e assinatura dos IDs das tabelas auditadas no
Supabase antes e depois de uma leitura e executa na VPS o verificador efêmero
`tools/test_juan_vps.py`.

O GitHub Actions roda a parte local em push, pull request e semanalmente, sem
segredos. `python3 tools/test_ecossistema.py --completa` é o caminho único para
a prova local + VPS; ele exige o contexto privado por variáveis de ambiente e
ativa os arquivos reais e a trajetória do Juan. A prova completa não é
agendada no GitHub nem copiada para cron da VPS, pois o servidor não possui
clone canônico deste repositório.

A auditoria de navegação usa `tools/auditar_ecossistema.py` como orquestrador e
`tools/auditar_ecossistema_browser.js` para abrir todas as páginas em Chromium,
nas dimensões desktop e celular. Ela testa acesso direto, recarga, clique,
voltar, item ativo, shell, estouro horizontal, console, HTTP e falhas de
requisição. Financeiro, Pendências e Eventos também recebem cenários isolados
com cliente Supabase simulado. Eles cobrem positivo, vazio e falha; Financeiro
e Pendências incluem ainda falha parcial de uma fonte. Um contador reprova
qualquer tentativa de mutação, e a prova confere filtros, linguagem humana,
vínculos de origem e ausência de identificadores técnicos. As capturas e os
relatórios JSON/Markdown são publicados como artefato do workflow.

O verificador da VPS não é instalado no servidor e não cria rascunho nem
lançamento. Ele compila os handlers, roda seus testes, valida a configuração
OpenClaw e os serviços, processa foto e PDF reais em `--dry-run` e, quando
solicitado, inspeciona a trajetória do agente para provar que o primeiro acesso
ao anexo foi pelo roteador. A assinatura de nove tabelas — inclusive memória e
continuidade — deve permanecer idêntica. Comandos e cobertura detalhada estão
em [`docs/testes-ecossistema.md`](testes-ecossistema.md).

## Confinex (confinex-app.latest.js, ~110 KB / 2.120 linhas)

Bundle esbuild legível de `src/confinex-entry.jsx` + `confinex_work.jsx` (fonte NÃO está no repo). React com hooks, JSX pré-compilado, CSS injetado via template string (tema em `var T`). Raiz: componente `Confinex()`.

### Persistência
- localStorage: `confinex:last-state:v3` (auto-save), `confinex:restore-before-reset:v1` (snapshot antes de reset), `confinex:named-versions:v1` (até 80 versões), `confinex:sheets-backend-url`, `confinex:device-id:v1`; legado migrado: `confinex:last-state:v2`.
- Google Sheets via Apps Script: leitura JSONP (`sheetsJsonp`, timeout 12s), escrita `sheetsPost` (ações `getState/saveState/getVersions/saveVersion/deleteVersion`) + `sheetsBeacon` no `pagehide`. Proteções: `cloudReadyRef` bloqueia auto-save até carregar a nuvem; debounce 10s; carimbo `clientUpdatedAt` → backend responde `error:"conflict"` se outro dispositivo salvou depois; merge por id em `carregarVersoesSheets`; "Salvar agora" força sobrescrita.
- Supabase: `confinex_testes` recebe versões nomeadas quando há sessão autenticada. Integrações externas e agentes usam `submeter_negocio_confinex`, que cria uma avaliação `rascunho` e congela a estimativa original. O nome do grupo Telegram é a referência operacional exibida e informada pelo usuário; o ID é um vínculo técnico opcional, capturado pela integração a partir do contexto do chat e oculto nas telas. A fila em **Operações → Confinamento** (`confinamento.html`) mostra os indicadores e permite aprovar pela RPC `aprovar_negocio_confinex` (`iniciado`) ou recusar pela RPC `recusar_negocio_confinex` (`cancelado`, preservado para auditoria); o simulador não decide submissões externas. Negócios iniciados permitem ajustar o prazo operacional pela RPC `ajustar_prazo_confinex`; `confinex_ajustes_prazo` preserva cada prazo anterior, novo prazo, saídas previstas, motivo, autor e horário, sem alterar a estimativa congelada. A ação direta **Iniciar negócio** continua disponível no simulador para uma decisão já confirmada. `confinex_consolidacoes` e `confinex_desvios` guardam realizado, desvios e comentários. Sheets permanece somente durante a transição.

### Comparação de rentabilidade
- Cenários são classificados pela rentabilidade mensal líquida (`rMliq`), com lucro líquido e rentabilidade total como desempates.
- A rentabilidade mensal final é o número de maior destaque nos cartões. A rentabilidade total é complementar.
- Quando há operação financeira, a tela mostra o tipo, o período em dias, o custo e a mudança de `rMliqSemAdiantamento` para `rMliq`.
- `calcImpactoOperacaoFinanceira` separa adiantamento de capital de antecipação do recebimento. No segundo caso, `taxaMensalFluxos` calcula a rentabilidade mensal pelos fluxos datados do capital, do recebimento antecipado e do saldo final.
- `js/confinex-resultado-financeiro.mjs` define o contrato comum de receita, custos operacionais, lucro bruto, componentes financeiros e lucro líquido. `custoDinheiroOperacao` reúne compra, frete e parcelas do confinamento; todos reduzem o líquido uma vez. Adiantamento ou antecipação são componentes adicionais. A função separada de VP traz receita e desembolsos ao dia zero sem contaminar o lucro nominal.
- A comparação inclui custo da arroba posta, custo da arroba líquida produzida, custo marginal, frete diluído e produção + frete por arroba produzida.
- `calcEvolucaoTempo` avalia cada cenário entre 60 e 240 dias com a curva BGI correspondente ao mês de cada saída; substitui a antiga indicação de ponto ótimo.
- `RelatorioComparativo` mantém no DOM uma versão exclusiva para impressão/PDF com resumo ordenado, ficha de todos os cenários e respectivas evoluções, independentemente da aba ativa.

### Estado
`estadoAtual()` = `{lote, cenarios (até 5, o 5º nasce "Revenda"), confinamentos (modelos salvos), historico (testes de sensibilidade), scAtivo, resultados, data, versao:"1.3-supabase"}`. Defaults em `defaultLote` e `defaultSc(i)`. Cada cenário/base salva persiste `pagamentoConfinamento` (`adiantado`, `mensal` ou `final`), `referenciaTransporte` (`transporte_na_entrada`, `transporte_na_producao` ou `comparar`) e, quando rota automática foi calculada, `distanciaFonte`, `distanciaCalculadaEm`, `distanciaEstudoId` e `distanciaCongeladaEm`; snapshots antigos assumem a referência legada de transporte na entrada e ausência de distância congelada. `lote.cotacoesB3` guarda a curva compartilhada do estudo, indexada por código de contrato; os cenários preservam uma cópia sincronizada do preço para compatibilidade com cálculos e snapshots antigos.

### Integrações
- **Cotação B3**: `buscarPrecoB3PorContrato` — cascata API B3 (`InstrumentPriceFluctuation`) → `DailyFluctuationHistory` → Yahoo (`{contrato}.SA`) → CEPEA (scrape via proxy allorigins.win). A seção geral Mercado BGI consulta simultaneamente todos os vencimentos usados no estudo e sincroniza cenários do mesmo contrato. `js/confinex-bgi.mjs` mantém a origem automática/manual, preserva valores manuais durante atualizações em lote e recusa ausência como zero. Em parceria, o contrato vem automaticamente da **data de saída** (`dataEntrada + diasCiclo`); em matéria seca e outras modalidades não-parceria, o vencimento pode ser escolhido. Códigos de mês F,G,H,J,K,M,N,Q,U,V,X,Z.
- **Distância de frete**: `calcularDistancia` usa `window.CONFINEX_GOOGLE_MAPS_KEY` ou `CONFINEX_DISTANCE_PROXY`; sem chave, abre Google Maps para copiar km manualmente.
- **Referências de transporte**: `js/confinex-referencias-transporte.mjs` calcula as leituras “transporte na @ de chegada” e “transporte na @ produzida”, além de perda bruta, recuperação e perda líquida. São métricas auxiliares; o motor financeiro e o ranking não são alterados.

## Deploy

`.github/workflows/deploy.yml`: push na `main` → checkout → configure-pages → upload-pages-artifact (`path: '.'`, repo inteiro, sem build) → deploy-pages. Publica em `https://pablofaraujo.github.io/Confinex/`.

## Dívidas técnicas

1. `central.html` legada coexiste com `index.html`; `bgi.html` linka para a legada.
2. Fonte JSX do bundle React fora do repo; o bundle legível e o pacote móvel gerado ficam versionados.
3. O shell e os componentes principais são compartilhados; o Confinex ainda injeta CSS próprio dentro do bundle React, embora seus tokens visuais estejam alinhados ao DS.
4. Credenciais Supabase e URL do Apps Script hardcoded em todas as páginas.
5. Confinex ainda mantém Sheets/localStorage como compatibilidade temporária; negócios iniciados e testes autenticados já possuem modelo Supabase, mas a migração operacional precisa ser homologada antes de remover o Apps Script.
6. JSONP + scrape CEPEA + heurística `extrairNumeroB3` (aceita 100–800) são frágeis.
7. `rolar()`/`encerrar()`/`encerrarParcial()` sem transação (o parcial faz 3+ escritas sequenciais: update da posição, insert da parte fechada, rateio das alocações).
8. Deploy publica tudo, inclusive `promissoria-skill.zip`.
9. Sem lint ou package.json; a bateria Python/Node cobre o fluxo crítico e
   roda pelo GitHub Actions, mas ainda não há análise estática geral dos apps.
10. `painel-boi-gordo.html` foi movido para o repo, mas a tarefa agendada `atualiza-painel-boi-gordo` (Cowork, seg-sex 6h32) ainda atualiza um artifact Cowork separado — o arquivo do repo não recebe as atualizações diárias até a automação ser redirecionada para editar este arquivo (e alguém commitar/push, já que a pasta do Drive não tem `.git`/push).
