# Referência sequencial dos negócios de bolsa

## Contrato

Cada posição de mesa recebe um código humano imutável `B3-AA-NNN`, por
exemplo `B3-26-001`. O código identifica o negócio de bolsa; o código
`CF-AA-NNN` continua identificando o lote físico ou o rateio atendido.

A referência deve acompanhar a conversa com a mesa, por exemplo:
`Fechamento da B3-26-001`. O conciliador privado do Wey reconhece também
variações com espaços ou separadores diferentes, normaliza o código e o usa
como evidência mais forte que o valor financeiro. A busca é somente leitura e
não envia mensagens nem altera o Supabase.

## Implantação segura

1. Publicar e revisar o código do Confinex e do Portfólio B3.
2. Capturar a contagem e a assinatura de `posicoes_hedge`.
3. Aplicar somente `202608210001_b3_referencias_sequenciais.sql` após
   autorização explícita.
4. Conferir que todas as posições receberam exatamente uma referência, sem
   mudança em contrato, direção, quantidade, preços, custos, status ou rateio.
5. Publicar o Portfólio B3 e conferir a coluna **Referência** e a data abaixo do
   preço de entrada em desktop e celular.

A migração é transacional. Ela cria a coluna, um índice único parcial, o
contador anual protegido por RLS e o gatilho atômico. O legado é numerado pela
data de entrada; quando a data não existe, usa a criação apenas para ordenar.
Não há gravação em compras, vendas, abates, pesagens ou fluxo financeiro.

## Situação desta entrega

A migração foi aplicada em **21/08/2026**. A validação confirmou referências
preenchidas, distintas e dentro do padrão, além do índice único, do gatilho e
do contador protegido por RLS. Compras, vendas, abates e pesagens permaneceram
inalterados. O preenchimento do legado acionou os gatilhos preexistentes de
`updated_at` e auditoria de `posicoes_hedge`, preservando o histórico da mudança.
