# Inventário de chaves do esquema

Complementa o [catálogo de fontes](catalogo-chaves-fontes.md). Mede estrutura sem
consultar linhas de negócio, normalizar valores, executar migração ou criar
funções no Supabase. Não possui opção `--executar`.

## Dois níveis de evidência

| Fonte | Comprova | Não comprova |
|---|---|---|
| OpenAPI: dois GETs em `/rest/v1/` | Objetos/campos expostos e presença de anotações PK/FK | Todas as tabelas físicas, chaves, índices, RLS ou dados íntegros |
| Exportação da consulta `pg_catalog` | Objetos de `public`, colunas, PK/FK/UNIQUE/exclusão, índices e flags RLS | Qualidade das linhas, políticas corretas ou identidades econômicas |

O modo parcial devolve `null` (não verificado), nunca zero, para contagens não
fornecidas por OpenAPI. Objetos expostos não equivalem a tabelas físicas.
Descrições são descartadas após extrair apenas presença de anotações; exemplos,
defaults, enum, URLs, credenciais e metadados RPC não entram no relatório.
Ver o contrato de [OpenAPI do PostgREST](https://docs.postgrest.org/en/stable/references/api/openapi.html).

## Coleta parcial pela API

No ambiente que **já possui** `SUPABASE_URL` e `SUPABASE_SERVICE_KEY` (ou
`SUPABASE_SERVICE_ROLE_KEY`):

```sh
python3 tools/inventariar_esquema_chaves.py --supabase \
  --saida docs/privado/esquema-chaves/coleta-nova
```

Não passar chaves em argumentos/chat. O cliente aceita apenas HTTPS no domínio
de projeto Supabase, recusa redirecionamentos, usa timeout de 15 segundos e
limite de 10 MB por resposta. Faz dois GETs sem retry, endpoint de tabela ou RPC.
Credenciais ficam nos cabeçalhos; mensagens de erro não reproduzem seus valores.
`--stdout` substitui `--saida` para transporte privado controlado; nunca usar em
logs públicos.

## Catálogo SQL — leitura, PostgreSQL 15+

`supabase/audits/catalogo_chaves_somente_leitura.sql` não é migração. Usa
transação `REPEATABLE READ READ ONLY`, limite de 15 segundos por instrução,
lock de até 2 segundos e termina com `ROLLBACK`. Consulta somente catálogos,
nunca linhas de usuário. Retorna uma coluna JSON `catalogo`; exportar somente
seu conteúdo em arquivo privado, não CSV disfarçado de JSON.

Com conexão de leitura **previamente configurada**:

```sh
psql -X -v ON_ERROR_STOP=1 -qAt -f supabase/audits/catalogo_chaves_somente_leitura.sql
```

Não colocar URL/senha de conexão no comando. O Python não descobre credenciais
SQL nem executa essa consulta automaticamente. Para processar a exportação:

```sh
python3 tools/inventariar_esquema_chaves.py \
  --arquivo docs/privado/esquema-chaves/catalogo-sql.json \
  --saida docs/privado/esquema-chaves/analise-nova
```

Preserva ordem de chaves/FKs compostas. `INCLUDE` não integra chave. Expressão
aparece como coluna `null`, sem seu SQL; índice parcial é sinalizado sem
predicado. Exclusão tem contagem própria, não vira UNIQUE. Índice de suporte de
PK/UNIQUE não é segunda chave autônoma. Índices inválidos/não prontos/removidos
não entram nos únicos ativos. Contagens são físicas por objeto, incluindo
partições, não identidades lógicas deduplicadas entre pai e filhos.
Referências: [pg_constraint](https://www.postgresql.org/docs/current/catalog-pg-constraint.html)
e [pg_index](https://www.postgresql.org/docs/current/catalog-pg-index.html).

## Assinaturas, privacidade e limites

- Duas leituras API devem produzir os mesmos metadados projetados; mudança
  interrompe a execução sem relatório de sucesso.
- No modo arquivo, verifica-se somente que o arquivo não mudou durante a
  leitura. Não são duas consultas ao banco. Estabilidade do esquema ao vivo
  exige duas exportações SQL independentes.
- `plano_id` identifica conteúdo estrutural sanitizado, separado do horário;
  não autoriza normalização nem substitui plano de escrita.
- Assinatura de metadados não prova estabilidade das linhas operacionais.
- Saída exige diretório novo privado/temporário, arquivos restritos e nenhuma
  sobrescrita. Defaults, comentários, expressões e políticas não são exportados.
- Flag RLS não é auditoria de políticas. FK não comprova associação econômica
  correta entre documento, pagamento e negócio.

## Testes

```sh
python3 -B -m unittest tools.test_inventariar_esquema_chaves tools.test_sql_catalogo_chaves
python3 tools/test_ecossistema.py
git diff --check
```

Os testes comuns usam metadados sintéticos/mocks sem rede. A consulta SQL deve
também passar em PostgreSQL descartável local com PK, UNIQUE composto, FK
ordenada, expressão, parcial, INCLUDE, exclusão e RLS antes de uso no banco real.
O teste PostgreSQL opcional é desativado por padrão; nunca apontá-lo para
Supabase nem banco de produção.
