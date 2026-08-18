# Inventários físicos da Fazenda

## Objetivo

`inventarios_fazenda` guarda fotografias físicas por data, local e categoria.
Ela não substitui `fazenda_ametista`, que continua sendo o ledger de entradas e
saídas, e não cria compra, venda, negócio ou transferência interunidades.

Cada linha registra cabeças e peso médio vivo. O peso total é calculado pelo
banco como `cabeças × peso médio`, evitando divergência entre os três números.
Grupos mistos são divididos por categoria e podem manter o mesmo local.

## Segurança e idempotência

- `authenticated` possui somente leitura;
- `service_role` pode ler, inserir e atualizar, mas não apagar ou truncar;
- `anon` não possui acesso;
- `idempotency_key` impede repetição do mesmo item;
- local, categoria e sexo também são únicos dentro da mesma unidade e data;
- bezerros sem sexo confirmado permanecem com sexo não informado.

## Registro controlado

O arquivo de entrada permanece privado e fora do Git. A ferramenta é dry-run
por padrão:

```bash
python3 tools/registrar_inventario_fazenda.py \
  --arquivo docs/privado/inventario-fazenda.json
```

A saída informa o `plano_id` e a frase exata. A execução exige ambos:

```bash
python3 tools/registrar_inventario_fazenda.py \
  --arquivo docs/privado/inventario-fazenda.json \
  --executar \
  --confirmacao "REGISTRAR INVENTARIO FAZENDA <plano_id>"
```

A ferramenta consulta todas as chaves antes de escrever. Se o inventário já
existir com o mesmo conteúdo, retorna `duplicate` sem novo POST. Existência
parcial ou conteúdo diferente bloqueia a execução. A inserção dos itens usa uma
única requisição; falha de rede é reconciliada por leitura e nunca dispara uma
segunda escrita automática.

## Tela

Fazenda Ametista mostra separadamente:

- estoque e peso do último inventário físico;
- saldo calculado pelo histórico de entradas e saídas;
- itens do inventário por local e categoria.

Essa separação torna divergências visíveis sem alterar o ledger para forçar uma
conciliação artificial.

## Verificação e reversão

Antes e depois do registro, comparar contagem, IDs e soma de cabeças/peso de
`inventarios_fazenda`, além das assinaturas de `fazenda_ametista`,
`negocios_fazenda`, `movimentacoes_interunidades`, `compras`, `vendas` e
`operacoes`. Somente a nova tabela pode mudar.

Para reverter código, use `git revert`. A remoção de um inventário real não é
automática: exige decisão explícita, identificação exata das chaves e registro
de auditoria. Nunca limpar por data ou texto parcial.
