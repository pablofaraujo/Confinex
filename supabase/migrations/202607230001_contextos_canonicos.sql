-- Contrato único de contexto por canal/conversa.
-- Esta migração cria estrutura; a correção dos dados é feita separadamente,
-- primeiro com tools/normalizar_contextos.py em modo dry-run.

CREATE TABLE IF NOT EXISTS public.contextos_canais (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  contexto_canonico text NOT NULL UNIQUE,
  contexto_nome text NOT NULL,
  origem_canal text NOT NULL,
  origem_conversa_id text NOT NULL,
  escopo text NOT NULL CHECK (escopo IN ('grupo', 'direto', 'sistema')),
  aliases text[] NOT NULL DEFAULT '{}',
  ativo boolean NOT NULL DEFAULT true,
  criado_em timestamptz NOT NULL DEFAULT now(),
  atualizado_em timestamptz NOT NULL DEFAULT now(),
  UNIQUE (origem_canal, origem_conversa_id)
);

ALTER TABLE public.operation_drafts
  ADD COLUMN IF NOT EXISTS contexto_canonico text,
  ADD COLUMN IF NOT EXISTS contexto_nome text,
  ADD COLUMN IF NOT EXISTS escopo text;

ALTER TABLE public.pending_actions
  ADD COLUMN IF NOT EXISTS contexto_canonico text,
  ADD COLUMN IF NOT EXISTS contexto_nome text,
  ADD COLUMN IF NOT EXISTS origem_canal text,
  ADD COLUMN IF NOT EXISTS origem_conversa_id text,
  ADD COLUMN IF NOT EXISTS origem_mensagem_id text,
  ADD COLUMN IF NOT EXISTS escopo text;

ALTER TABLE public.eventos
  ADD COLUMN IF NOT EXISTS contexto_canonico text,
  ADD COLUMN IF NOT EXISTS contexto_nome text,
  ADD COLUMN IF NOT EXISTS escopo text;

ALTER TABLE public.memorias_agentes
  ADD COLUMN IF NOT EXISTS contexto_canonico text,
  ADD COLUMN IF NOT EXISTS contexto_nome text,
  ADD COLUMN IF NOT EXISTS agente text;

CREATE INDEX IF NOT EXISTS operation_drafts_contexto_canonico_idx
  ON public.operation_drafts (contexto_canonico);
CREATE INDEX IF NOT EXISTS pending_actions_contexto_canonico_idx
  ON public.pending_actions (contexto_canonico);
CREATE INDEX IF NOT EXISTS eventos_contexto_canonico_idx
  ON public.eventos (contexto_canonico);
CREATE INDEX IF NOT EXISTS memorias_agentes_contexto_canonico_idx
  ON public.memorias_agentes (contexto_canonico);

ALTER TABLE public.contextos_canais ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS contextos_canais_authenticated_select ON public.contextos_canais;
CREATE POLICY contextos_canais_authenticated_select
ON public.contextos_canais FOR SELECT TO authenticated USING (ativo = true);

COMMENT ON COLUMN public.contextos_canais.contexto_nome IS
  'Nome humano exibido no frontend; IDs técnicos nunca devem ser mostrados.';

CREATE OR REPLACE FUNCTION public.preencher_contexto_canonico()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
  v_canal text;
  v_conversa text;
  v_mensagem text;
  v_nome text;
  v_canonico text;
  v_escopo text;
  v_registro public.contextos_canais%ROWTYPE;
BEGIN
  IF TG_TABLE_NAME = 'pending_actions' THEN
    v_canal := coalesce(
      NEW.origem_canal, NEW.canal,
      NEW.payload #>> '{dados_revisados,origem_canal}',
      NEW.payload #>> '{dados_extraidos,origem_canal}'
    );
    v_conversa := coalesce(
      NEW.origem_conversa_id, NEW.conversa_id,
      NEW.payload #>> '{dados_revisados,origem_conversa_id}',
      NEW.payload #>> '{dados_extraidos,origem_conversa_id}'
    );
    v_mensagem := coalesce(
      NEW.origem_mensagem_id, NEW.mensagem_id,
      NEW.payload #>> '{dados_revisados,origem_mensagem_id}',
      NEW.payload #>> '{dados_extraidos,origem_mensagem_id}'
    );
    v_nome := coalesce(
      NEW.contexto_nome,
      NEW.payload #>> '{dados_revisados,contexto_nome}',
      NEW.payload #>> '{dados_extraidos,contexto_nome}'
    );
    v_canonico := coalesce(
      NEW.contexto_canonico,
      NEW.payload #>> '{dados_revisados,contexto_canonico}',
      NEW.payload #>> '{dados_extraidos,contexto_canonico}'
    );
    v_escopo := coalesce(
      NEW.escopo,
      NEW.payload #>> '{dados_revisados,escopo}',
      NEW.payload #>> '{dados_extraidos,escopo}'
    );
  ELSIF TG_TABLE_NAME = 'operation_drafts' THEN
    v_canal := coalesce(NEW.origem_canal, NEW.dados_extraidos ->> 'origem_canal');
    v_conversa := coalesce(NEW.origem_conversa_id, NEW.dados_extraidos ->> 'origem_conversa_id');
    v_mensagem := coalesce(NEW.origem_mensagem_id, NEW.dados_extraidos ->> 'origem_mensagem_id');
    v_nome := coalesce(NEW.contexto_nome, NEW.dados_extraidos ->> 'contexto_nome');
    v_canonico := coalesce(NEW.contexto_canonico, NEW.dados_extraidos ->> 'contexto_canonico');
    v_escopo := coalesce(NEW.escopo, NEW.dados_extraidos ->> 'escopo');
  ELSE
    v_canal := NEW.origem_canal;
    v_conversa := NEW.origem_conversa_id;
    v_mensagem := NEW.origem_mensagem_id;
    v_nome := NEW.contexto_nome;
    v_canonico := NEW.contexto_canonico;
    v_escopo := NEW.escopo;
  END IF;

  SELECT *
    INTO v_registro
  FROM public.contextos_canais
  WHERE ativo
    AND (
      contexto_canonico = v_canonico
      OR (origem_canal = v_canal AND origem_conversa_id = v_conversa)
      OR v_conversa = ANY (aliases)
      OR v_nome = ANY (aliases)
    )
  LIMIT 1;

  IF FOUND THEN
    v_canonico := v_registro.contexto_canonico;
    v_nome := v_registro.contexto_nome;
    v_escopo := v_registro.escopo;
    v_conversa := v_registro.origem_conversa_id;
  END IF;

  NEW.contexto_canonico := v_canonico;
  NEW.contexto_nome := v_nome;
  NEW.escopo := v_escopo;
  NEW.origem_canal := v_canal;
  NEW.origem_conversa_id := v_conversa;
  NEW.origem_mensagem_id := v_mensagem;

  IF TG_TABLE_NAME = 'pending_actions' THEN
    NEW.canal := v_canal;
    NEW.conversa_id := v_conversa;
    NEW.mensagem_id := v_mensagem;
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS operation_drafts_contexto_canonico ON public.operation_drafts;
CREATE TRIGGER operation_drafts_contexto_canonico
BEFORE INSERT OR UPDATE ON public.operation_drafts
FOR EACH ROW EXECUTE FUNCTION public.preencher_contexto_canonico();

DROP TRIGGER IF EXISTS pending_actions_contexto_canonico ON public.pending_actions;
CREATE TRIGGER pending_actions_contexto_canonico
BEFORE INSERT OR UPDATE ON public.pending_actions
FOR EACH ROW EXECUTE FUNCTION public.preencher_contexto_canonico();

DROP TRIGGER IF EXISTS eventos_contexto_canonico ON public.eventos;
CREATE TRIGGER eventos_contexto_canonico
BEFORE INSERT OR UPDATE ON public.eventos
FOR EACH ROW EXECUTE FUNCTION public.preencher_contexto_canonico();
