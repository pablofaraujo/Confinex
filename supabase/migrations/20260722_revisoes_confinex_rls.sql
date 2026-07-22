-- Permissoes para a tela Confinex Revisoes.
-- A interface logada pode revisar rascunhos e pendencias, mas nao promove
-- registros para tabelas operacionais. A promocao definitiva continua em fluxo separado.

ALTER TABLE public.operation_drafts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.pending_actions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.eventos ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS operation_drafts_authenticated_revisoes ON public.operation_drafts;
CREATE POLICY operation_drafts_authenticated_revisoes
ON public.operation_drafts
FOR ALL
TO authenticated
USING (true)
WITH CHECK (true);

DROP POLICY IF EXISTS pending_actions_authenticated_revisoes ON public.pending_actions;
CREATE POLICY pending_actions_authenticated_revisoes
ON public.pending_actions
FOR ALL
TO authenticated
USING (true)
WITH CHECK (true);

DROP POLICY IF EXISTS eventos_authenticated_revisoes_insert ON public.eventos;
CREATE POLICY eventos_authenticated_revisoes_insert
ON public.eventos
FOR INSERT
TO authenticated
WITH CHECK (origem = 'confinex_revisoes');

DROP POLICY IF EXISTS eventos_authenticated_revisoes_select ON public.eventos;
CREATE POLICY eventos_authenticated_revisoes_select
ON public.eventos
FOR SELECT
TO authenticated
USING (true);
