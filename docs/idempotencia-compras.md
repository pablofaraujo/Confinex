# Idempotência de compras operacionais

Status: **preparada, não aplicada no Supabase**.

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

## Homologação antes de aplicar

1. Revisar o SQL e o contrato do cliente.
2. Confirmar o esquema real de `public.compras`.
3. Executar a migração em ambiente de homologação.
4. Conferir que as linhas anteriores permanecem com chave nula.
5. Simular duas requisições com a mesma chave e os mesmos dados.
6. Confirmar um registro e respostas `inserted`/`duplicate`.
7. Simular a mesma chave com dados diferentes e confirmar a rejeição.
8. Auditar RLS e permissões antes e depois.
9. Aplicar em produção somente com autorização explícita.

Enquanto a migração não for aplicada, o código que envia
`idempotency_key` não deve ser implantado no executor operacional.

## Testes locais

```bash
python3 -m unittest tools.test_compras_idempotencia
python3 -m unittest tools.test_promocao_operacional
python3 -m unittest tools.test_promocao_confirmacao_router
```

Os testes usam transporte em memória e não fazem chamadas ao Supabase.
