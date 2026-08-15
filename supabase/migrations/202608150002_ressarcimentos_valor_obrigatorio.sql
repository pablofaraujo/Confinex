-- Ressarcimento operacional só existe quando o desembolso está quantificado.
-- A validação explícita impede esconder registros antigos como "A apurar".
DO $$
BEGIN
  IF EXISTS (
    SELECT 1
      FROM public.ressarcimentos_operacionais
     WHERE valor IS NULL
  ) THEN
    RAISE EXCEPTION 'Existem ressarcimentos operacionais sem valor conciliado';
  END IF;
END
$$;

ALTER TABLE public.ressarcimentos_operacionais
  ALTER COLUMN valor SET NOT NULL;
