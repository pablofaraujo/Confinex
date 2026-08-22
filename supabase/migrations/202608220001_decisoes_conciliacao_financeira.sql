-- Decisao auditavel de propostas de conciliacao bancaria.
-- Confirmar uma proposta aceita somente o vinculo no staging: esta funcao
-- nunca cria, altera ou quita lancamentos operacionais ou financeiros.

BEGIN;

CREATE OR REPLACE FUNCTION public.decidir_conciliacao_candidata(
  p_conciliacao_id uuid,
  p_decisao text,
  p_motivo text
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_registro public.conciliacoes_candidatas%ROWTYPE;
  v_decisao text := lower(btrim(coalesce(p_decisao, '')));
  v_motivo text := btrim(coalesce(p_motivo, ''));
  v_estado_novo text;
  v_ator text;
BEGIN
  IF auth.uid() IS NULL THEN
    RAISE EXCEPTION 'Entre no Confinex para registrar a decisão';
  END IF;

  IF v_decisao NOT IN ('confirmar', 'rejeitar') THEN
    RAISE EXCEPTION 'Escolha confirmar ou rejeitar a sugestão';
  END IF;

  IF v_motivo = '' THEN
    RAISE EXCEPTION 'Informe o motivo da decisão';
  END IF;

  IF length(v_motivo) > 500 THEN
    RAISE EXCEPTION 'Resuma o motivo em até 500 caracteres';
  END IF;

  v_estado_novo := CASE v_decisao
    WHEN 'confirmar' THEN 'confirmada'
    ELSE 'rejeitada'
  END;
  v_ator := 'usuario_confinex';

  SELECT *
    INTO v_registro
    FROM public.conciliacoes_candidatas
   WHERE id = p_conciliacao_id
   FOR UPDATE;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'Sugestão de conciliação não encontrada';
  END IF;

  -- Uma repeticao da mesma decisao e segura e nao duplica historico/evento.
  IF v_registro.estado = v_estado_novo THEN
    RETURN jsonb_build_object(
      'estado', v_registro.estado,
      'alterado', false,
      'mensagem', 'Esta decisão já estava registrada'
    );
  END IF;

  IF v_registro.estado <> 'pendente' THEN
    RAISE EXCEPTION 'A sugestão já recebeu outra decisão';
  END IF;

  UPDATE public.conciliacoes_candidatas
     SET estado = v_estado_novo,
         confirmado_por = CASE WHEN v_estado_novo = 'confirmada' THEN v_ator ELSE NULL END,
         confirmado_em = CASE WHEN v_estado_novo = 'confirmada' THEN now() ELSE NULL END
   WHERE id = v_registro.id;

  INSERT INTO public.decisoes_consolidacao (
    entidade_tipo,
    entidade_id,
    estado_anterior,
    estado_novo,
    decisao,
    decidido_por,
    motivo
  ) VALUES (
    'conciliacao_candidata',
    v_registro.id,
    v_registro.estado,
    v_estado_novo,
    v_decisao,
    v_ator,
    v_motivo
  );

  INSERT INTO public.eventos (
    tipo,
    agente,
    usuario,
    entidade_tipo,
    entidade_id,
    origem,
    status,
    dados,
    observacao
  ) VALUES (
    CASE WHEN v_estado_novo = 'confirmada'
      THEN 'conciliacao_bancaria_confirmada'
      ELSE 'conciliacao_bancaria_rejeitada'
    END,
    'confinex',
    'usuario_confinex',
    'conciliacao_candidata',
    v_registro.id,
    'confinex_financeiro',
    'registrado',
    jsonb_build_object(
      'acao', v_decisao,
      'estado_anterior', v_registro.estado,
      'estado_novo', v_estado_novo,
      'promovido_para_operacional', false
    ),
    CASE WHEN v_estado_novo = 'confirmada'
      THEN 'Relação bancária conferida no Financeiro. Motivo: ' || v_motivo
      ELSE 'Sugestão bancária rejeitada no Financeiro. Motivo: ' || v_motivo
    END
  );

  RETURN jsonb_build_object(
    'estado', v_estado_novo,
    'alterado', true,
    'mensagem', CASE WHEN v_estado_novo = 'confirmada'
      THEN 'Relação conferida; nenhum lançamento foi criado ou quitado'
      ELSE 'Sugestão rejeitada; nenhum lançamento foi alterado'
    END
  );
END;
$$;

REVOKE ALL ON FUNCTION public.decidir_conciliacao_candidata(uuid, text, text)
  FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.decidir_conciliacao_candidata(uuid, text, text)
  TO authenticated;

COMMENT ON FUNCTION public.decidir_conciliacao_candidata(uuid, text, text) IS
  'Confirma ou rejeita uma proposta no staging, exige motivo e registra decisão e evento; não altera fluxo_caixa, transacoes_banco ou tabelas operacionais.';

COMMIT;
