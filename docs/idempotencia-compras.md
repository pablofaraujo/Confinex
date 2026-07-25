# Idempotência de compras operacionais

Status: **aplicada no Supabase em 25/07/2026; implantação do executor pendente**.

## Finalidade

A chave idempotente impede que a mesma compra confirmada seja criada novamente
quando uma resposta de rede se perde ou uma execução é retomada. Ela é técnica,
não contém dados comerciais e não substitui revisão, aprovação ou confirmação.

A proposta está em
`supabase/migrations/202607250001_compras_idempotencia.sql`.

## Estrutura proposta

- `compras.idempotency_key text null`;
- restrição que rejeita chave vazia ou maior que 200 caracteres;
- índice `unique` parcial somente quando a chave não é nula;
- comentários no catálogo descrevendo a finalidade.

Registros históricos continuam válidos com `idempotency_key = null`. O índice
parcial permite qualquer quantidade desses registros e não atualiza linhas
existentes.

## Compatibilidade com RLS

A migração não habilita, desabilita, cria nem remove políticas. Também não
concede ou revoga permissões. As políticas atuais de `compras` permanecem
inalteradas; a gravação controlada continua dependendo do executor autorizado.

A chave não deve conter token, pessoa, valor ou conteúdo do negócio. Na
promoção operacional ela é derivada somente do identificador da pendência:

`promocao_operacional:<pending_action_id>`

## Contrato do cliente

`ConfinexClient.insert_operational` aceita `idempotency_key` para compras e
retorna `OperationalInsertResult`:

- `inserted`: esta tentativa criou a compra;
- `duplicate`: a chave já existia com os mesmos dados, então nenhum segundo
  registro foi criado.

Se a chave existente estiver ligada a dados diferentes, o cliente retorna
`ConfinexIdempotencyConflict`. Ele nunca substitui o registro anterior.

Após timeout, o cliente consulta a chave:

1. se encontrar os mesmos dados, retorna `duplicate`;
2. se encontrar dados diferentes, rejeita;
3. se não encontrar registro, propaga a falha e não repete o `POST`
   automaticamente.

Essa consulta evita confundir resposta perdida com gravação perdida. A proteção
contra concorrência é fornecida pelo índice único, não apenas por memória do
processo.

## Aplicação e homologação

A migração foi aplicada somente depois do CI verde. A verificação sanitizada
antes/depois confirmou:

- mesma quantidade, mesmos IDs e mesmo conteúdo das compras;
- coluna nula e do tipo `text`;
- nenhuma compra antiga recebeu chave;
- índice único parcial e restrição validados;
- RLS, políticas e permissões inalterados;
- nenhum registro operacional criado.

Antes de implantar o cliente no executor:

1. conferir novamente o commit implantado;
2. validar o executor fora do sandbox;
3. simular o contrato com cliente local sem rede;
4. preparar um teste real controlado com marcador e IDs anotados;
5. confirmar `inserted`/`duplicate` e exatamente uma compra;
6. limpar o teste e comprovar a limpeza.

O cliente e o executor ainda não foram implantados no VPS.

## Testes locais

```bash
python3 -m unittest tools.test_compras_idempotencia
python3 -m unittest tools.test_promocao_operacional
python3 -m unittest tools.test_promocao_confirmacao_router
```

Os testes usam transporte em memória e não fazem chamadas ao Supabase.
