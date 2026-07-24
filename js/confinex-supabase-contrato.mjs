/** Contrato puro para preparar uma avaliação Confinex.
 * Não faz chamadas de rede nem gravações; a camada de UI decide quando usar a RPC.
 */
export function prepararAvaliacao({ codigo, nome, grupoOrigemNome, grupoOrigemId = null, premissas, resultado }) {
  const codigoLimpo = String(codigo ?? '').trim().toUpperCase();
  const nomeLimpo = String(nome ?? '').trim();
  const grupoLimpo = String(grupoOrigemNome ?? '').trim();
  if (!codigoLimpo) throw new Error('Código do negócio é obrigatório');
  if (!grupoLimpo) throw new Error('Grupo de origem é obrigatório');
  if (!premissas || typeof premissas !== 'object') throw new Error('Premissas são obrigatórias');
  if (!resultado || typeof resultado !== 'object') throw new Error('Resultado estimado é obrigatório');
  return { p_codigo: codigoLimpo, p_nome: nomeLimpo || codigoLimpo, p_grupo_origem_id: grupoOrigemId ? String(grupoOrigemId).trim() : null, p_grupo_origem_nome: grupoLimpo, p_premissas: structuredClone(premissas), p_resultado: structuredClone(resultado) };
}
export function prepararConsolidacao({ avaliacaoId, realizado, comentarioGeral = null }) {
  const id = String(avaliacaoId ?? '').trim();
  if (!id) throw new Error('Avaliação é obrigatória');
  if (!realizado || typeof realizado !== 'object') throw new Error('Resultado realizado é obrigatório');
  return { p_avaliacao_id: id, p_realizado: structuredClone(realizado), p_comentario_geral: comentarioGeral ? String(comentarioGeral).trim() : null };
}
