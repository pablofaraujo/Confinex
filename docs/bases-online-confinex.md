# Bases online do Confinex

## Problema resolvido

As bases reutilizáveis de confinamento ficavam dentro de
`confinex:last-state:v3` no navegador. Um computador novo começava sem essas
bases, mesmo quando já havia estudos salvos em outro aparelho.

O catálogo passa a ter duas camadas:

- Supabase `confinex_bases`: cópia pessoal compartilhada entre aparelhos;
- `localStorage`: cache e contingência quando não houver conexão.

Uma base contém somente premissas reutilizáveis do confinamento. Ela não cria
compra, venda, abate, pesagem, avaliação, rascunho ou promoção.

## Comportamento

1. Ao abrir o Confinex, depois que a sessão Supabase estiver disponível, as
   bases online são lidas e mescladas às locais.
2. O estudo corrente não é substituído.
3. Salvar, atualizar ou apagar uma base é uma ação explícita e replica a mesma
   mudança online quando houver sessão autenticada.
4. Bases antigas continuam locais até o clique em **Sincronizar bases** no
   aparelho que as possui.
5. Uma versão antiga enviada por outro aparelho não substitui uma versão
   online mais nova.
6. Sem login ou sem rede, o trabalho continua local e a tela informa o que
   ocorreu em linguagem simples.

## Migração

Arquivo: `supabase/migrations/202608120001_confinex_bases_online.sql`.

A migração é aditiva e cria somente:

- tabela `public.confinex_bases`;
- RLS por `auth.uid()`;
- função `public.salvar_base_confinex`.

O papel anônimo e `public` não possuem permissão sobre a tabela nem execução da
função; somente `authenticated` recebe as permissões necessárias, sempre sob
RLS.

Ela foi aplicada em 12/08/2026 depois de autorização explícita. A conferência
somente leitura comprovou tabela vazia, RLS ativa, uma política por usuário,
função `security invoker`, acesso completo para `authenticated`, ausência de
acesso para `anon` e assinaturas operacionais inalteradas:

```sql
select table_name
from information_schema.tables
where table_schema = 'public' and table_name = 'confinex_bases';

select policyname, roles, cmd
from pg_policies
where schemaname = 'public' and tablename = 'confinex_bases';
```

Não foi necessário criar registro de teste real para confirmar a estrutura.

## Reversão

O frontend tolera a ausência da tabela e mantém todas as bases locais. Portanto
a reversão imediata do comportamento é reverter o commit do frontend; não é
necessário apagar a tabela. Preservar a tabela permite recuperar o catálogo se
a funcionalidade for reativada.

## Testes permanentes

```bash
node tools/test_confinex_bases_online.mjs
python3 -m unittest tools.test_migracao_confinex_bases
python3 tools/test_ecossistema.py
```

Os testes usam cliente Supabase simulado e não fazem chamadas externas nem
escritas reais.
