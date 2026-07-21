# Migrações Supabase do Confinex

## Ordem de implantação

1. Aplicar `migrations/202607200001_confinex_avaliacoes.sql` em um projeto de testes.
2. Aplicar `migrations/202607200002_confinex_aprovacoes.sql` para habilitar a fila de aprovação.
3. Validar criação de teste nomeado e submissão de um negócio fictício pela RPC `submeter_negocio_confinex`.
4. Conferir o negócio com status `rascunho` na fila **Aprovações recebidas do Supabase** e aprová-lo pela RPC `aprovar_negocio_confinex`.
5. Consolidar o negócio fictício pela RPC `consolidar_negocio_confinex` e conferir desvios/comentários em `confinamento.html`.
6. Somente depois promover as mesmas migrações ao projeto de produção.

O Google Sheets permanece como compatibilidade temporária. A estimativa original submetida é imutável; mudanças posteriores a um negócio iniciado usam `revisar_estimativa_confinex` e exigem motivo. Agentes e integrações externas não devem chamar `iniciar_negocio_confinex`: devem submeter para aprovação.

Negócios cancelados continuam no banco para auditoria, mas as páginas operacionais os excluem das listas.
