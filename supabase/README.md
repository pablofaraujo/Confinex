# Migrações Supabase do Confinex

## Ordem de implantação

1. Aplicar `migrations/202607200001_confinex_avaliacoes.sql` em um projeto de testes.
2. Aplicar `migrations/202607200002_confinex_aprovacoes.sql` para habilitar a fila de aprovação.
3. Aplicar `migrations/202607210001_confinex_recusas.sql` para habilitar a recusa auditável.
4. Aplicar `migrations/202607210002_confinex_ajustes_prazo.sql` para habilitar ajustes recorrentes e auditáveis do prazo operacional.
5. Validar criação de teste nomeado e submissão de um negócio fictício pela RPC `submeter_negocio_confinex`.
6. Conferir o negócio com status `rascunho` na fila em **Operações → Confinamento** e aprová-lo pela RPC `aprovar_negocio_confinex` ou recusá-lo pela RPC `recusar_negocio_confinex`.
7. Para um negócio iniciado, ajustar o prazo pela RPC `ajustar_prazo_confinex` e conferir prazo atual, saída prevista e histórico em `confinamento.html`.
8. Se aprovado, consolidar o negócio fictício pela RPC `consolidar_negocio_confinex` e conferir desvios/comentários em `confinamento.html`.
9. Somente depois promover as mesmas migrações ao projeto de produção.

## Financeiro — migração preparada, não aplicada

`migrations/202607240001_financeiro_compromissos.sql` é um rascunho
aditivo para homologação. Ele modela compromissos, parcelas, pagamentos
parciais, renegociações e lembretes sem importar ou alterar dados de
`fluxo_caixa`, `promissorias`, `emprestimos` ou `transacoes_banco`.

Não aplicar em produção sem autorização explícita. Primeiro executar em um
projeto de testes, validar RLS, conciliar amostras sem dados reais e definir as
RPCs de escrita. O rascunho concede apenas leitura a usuários autenticados; não
há políticas de `insert`, `update` ou `delete`.

O Google Sheets permanece como compatibilidade temporária. A estimativa original submetida é imutável; mudanças completas de premissas e resultado usam `revisar_estimativa_confinex`, enquanto mudanças rotineiras do prazo operacional usam `ajustar_prazo_confinex`. Ambas exigem motivo. Agentes e integrações externas não devem chamar `iniciar_negocio_confinex`: devem submeter para aprovação.

## Consolidação privada — staging antes da promoção

`migrations/202608130001_staging_consolidacao_privada.sql` cria somente a
estrutura de entrada e revisão das planilhas, OFX, Telegram, IMA, AgroNota e
Wey. A importação é idempotente por hash da fonte, chave de rastreio e
`conta + FITID`. Candidatos confirmados continuam sem efeito operacional:
qualquer promoção para compras, vendas, banco ou fluxo de caixa exige um fluxo
separado, confirmação explícita e reconciliação antes/depois.

Negócios cancelados continuam no banco para auditoria, mas as páginas operacionais os excluem das listas.

## Bases do simulador — migração aplicada

`migrations/202608120001_confinex_bases_online.sql` cria somente o catálogo
pessoal `confinex_bases` e a função de salvamento com proteção contra uma cópia
antiga sobrescrever a versão online mais nova. A RLS isola cada usuário e não
há leitura ou escrita em compras, vendas, abates, pesagens ou avaliações.

Aplicada em 12/08/2026 depois de autorização explícita, sem criar bases nem
alterar tabelas operacionais. RLS, política, função `security invoker`, acesso
de `authenticated` e bloqueio de `anon` foram conferidos em modo leitura.

No aparelho que contém as bases antigas, entrar no ecossistema e usar
**Sincronizar bases** uma vez; em computadores novos, o catálogo será carregado
automaticamente após o login.
