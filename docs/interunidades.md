# Negócios da Fazenda e movimentos interunidades

## Objetivo

Representar separadamente três fatos que podem ocorrer no mesmo trânsito de animais:

1. a Fazenda vende o gado e apura seu próprio resultado;
2. o ledger da Fazenda registra a saída física das cabeças;
3. o Confinamento compra o mesmo gado e apura a operação de destino.

O vínculo auditável entre as três faces fica em `movimentacoes_interunidades`. Uma transferência física não é tratada como movimentação sem valor e uma compra do Confinamento não apaga o resultado da Fazenda.

## Entidades

- `negocios_fazenda`: compras e vendas econômicas da Fazenda, com quantidade, peso, rendimento, preço, valor, contraparte e chave idempotente.
- `fazenda_ametista`: continua sendo o ledger físico; recebe vínculo opcional para `negocios_fazenda` e chave idempotente.
- `movimentacoes_interunidades`: reconcilia a venda, a saída física e a compra do Confinamento. Ao confirmar, um gatilho exige mesma operação, quantidade, peso e valor.
- `compras_componentes`: explica os vendedores/corretores que formam uma compra agregada. É informativa e nunca entra novamente no total da operação.
- `operacao_participantes`: declara papéis por operação. Propriedade e parceria exigem percentual; corretor e gestor não recebem percentual econômico.

As telas Fazenda Ametista e Confinamento consultam `v_movimentacoes_interunidades` somente para leitura. Enquanto a migração não estiver aplicada, a seção informa que a estrutura está pendente sem impedir o restante da tela.

## Regras de cálculo e consolidação

- Arrobas a rendimento informado: `peso_total_kg × (rendimento_carne_pct ÷ 100) ÷ 15`.
- Valor: `arrobas × preco_arroba`.
- Em operações com compra agregada, os totais vêm exclusivamente de `compras`. Os componentes medem cobertura e rastreabilidade, não um segundo custo.
- Uma venda só pode ser confirmada quando seu valor coincidir, com tolerância de um centavo, com peso × rendimento × preço. Um movimento só pode ser confirmado quando essa venda já estiver confirmada e o lançamento físico for uma saída vinculada.
- A soma das participações econômicas (`proprietario` e `parceiro`) não pode exceder 100%.
- Nenhum participante, vendedor ou proprietário é inferido a partir de mensagens, valores ou negócios anteriores.

## Segurança e promoção

A migração `202608140001_interunidades_e_componentes.sql` é somente estrutural. Ela não contém `INSERT`, `UPDATE`, `DELETE` ou dados comerciais. Usuários autenticados recebem apenas `SELECT`; gravações são reservadas ao `service_role`. A aplicação da migração e qualquer carga operacional exigem gates separados.

Uma futura carga deve ser idempotente e ocorrer nesta ordem:

1. validar contatos e operação de destino;
2. criar/conciliar compras-raiz e seus componentes;
3. criar a venda confirmada da Fazenda;
4. criar a saída física vinculada;
5. criar a compra do Confinamento;
6. confirmar o vínculo interunidades somente depois das três conferências;
7. comparar assinaturas e totais antes/depois.

## Reversão da estrutura

Antes de reverter, exportar esquema, políticas e qualquer dado novo. Com as telas já tolerantes à ausência das views, a ordem de rollback é:

```sql
BEGIN;
DROP VIEW IF EXISTS public.v_movimentacoes_interunidades;
DROP VIEW IF EXISTS public.v_compras_componentes_resumo;
DROP TABLE IF EXISTS public.movimentacoes_interunidades;
DROP TABLE IF EXISTS public.operacao_participantes;
DROP FUNCTION IF EXISTS public.validar_movimentacao_interunidades();
DROP FUNCTION IF EXISTS public.validar_participacao_operacao();
ALTER TABLE public.fazenda_ametista DROP COLUMN IF EXISTS negocio_fazenda_id;
ALTER TABLE public.fazenda_ametista DROP COLUMN IF EXISTS idempotency_key;
DROP TABLE IF EXISTS public.negocios_fazenda;
DROP TABLE IF EXISTS public.compras_componentes;
DROP FUNCTION IF EXISTS public.validar_valor_negocio_fazenda();
DROP FUNCTION IF EXISTS public.atualizar_timestamp_interunidades();
COMMIT;
```

Esse script é destrutivo se houver registros e não deve ser executado automaticamente.
