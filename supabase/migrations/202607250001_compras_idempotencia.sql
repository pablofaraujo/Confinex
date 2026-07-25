-- Suporte aditivo a idempotência nas compras operacionais.
-- Esta migração não preenche nem altera registros existentes.

begin;

alter table public.compras
  add column if not exists idempotency_key text;

comment on column public.compras.idempotency_key is
  'Chave técnica estável da origem confirmada. Impede nova compra quando uma tentativa é repetida após falha de transporte.';

do $$
begin
  if not exists (
    select 1
      from pg_constraint
     where conrelid = 'public.compras'::regclass
       and conname = 'compras_idempotency_key_nao_vazia'
  ) then
    alter table public.compras
      add constraint compras_idempotency_key_nao_vazia
      check (
        idempotency_key is null
        or (
          btrim(idempotency_key) <> ''
          and length(idempotency_key) <= 200
        )
      );
  end if;
end
$$;

create unique index if not exists compras_idempotency_key_unique
  on public.compras (idempotency_key)
  where idempotency_key is not null;

comment on index public.compras_idempotency_key_unique is
  'Garante uma única compra por chave idempotente sem restringir registros históricos com chave nula.';

commit;
