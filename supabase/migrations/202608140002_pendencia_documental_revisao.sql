-- Distingue documento ausente de documento recebido cujo vínculo exige revisão.
ALTER TABLE public.pendencias_documentos
  DROP CONSTRAINT pendencias_documentos_status_check;

ALTER TABLE public.pendencias_documentos
  ADD CONSTRAINT pendencias_documentos_status_check
  CHECK (status = ANY (ARRAY[
    'aguardando_vendedor'::text,
    'revisao_necessaria'::text,
    'recebido'::text,
    'validado'::text,
    'dispensado'::text
  ]));

COMMENT ON COLUMN public.pendencias_documentos.status IS
  'aguardando_vendedor = documento ausente; revisao_necessaria = documento recebido com vínculo ou conteúdo a conferir; recebido/validado/dispensado = encerrado.';
