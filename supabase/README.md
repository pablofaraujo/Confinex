# Migrações Supabase do Confinex

## Ordem de implantação

1. Aplicar `migrations/202607200001_confinex_avaliacoes.sql` em um projeto de testes.
2. Validar criação de teste nomeado e abertura de um negócio fictício.
3. Consolidar o negócio fictício pela RPC `consolidar_negocio_confinex` e conferir desvios/comentários em `confinamento.html`.
4. Somente depois promover a mesma migração ao projeto de produção.

O Google Sheets permanece como compatibilidade temporária. A estimativa original de um negócio iniciado é imutável; mudanças posteriores usam `revisar_estimativa_confinex` e exigem motivo.

Negócios cancelados continuam no banco para auditoria, mas as páginas operacionais os excluem das listas.
