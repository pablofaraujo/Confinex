const TIPOS = new Set(['diaria', 'materia_seca', 'entrada', 'saida', 'consumo', 'pesagem', 'morte', 'transferencia', 'cobranca', 'pagamento']);

export function normalizarEvento(evento) {
  if (!evento || !TIPOS.has(evento.tipo)) throw new Error('tipo de evento inválido');
  if (!String(evento.data || '').match(/^\d{4}-\d{2}-\d{2}$/)) throw new Error('data inválida');
  const quantidade = Number(evento.quantidade ?? evento.cabecas ?? 0);
  if (!Number.isFinite(quantidade) || quantidade < 0) throw new Error('quantidade inválida');
  return { ...evento, data: evento.data, tipo: evento.tipo, quantidade };
}

export function consolidarLote({ loteId, eventos = [] }) {
  if (!String(loteId || '').trim()) throw new Error('lote obrigatório');
  const validos = eventos.map(normalizarEvento).sort((a, b) => a.data.localeCompare(b.data));
  const total = tipo => validos.filter(e => e.tipo === tipo).reduce((s, e) => s + e.quantidade, 0);
  const entradas = total('entrada'), saidas = total('saida'), mortes = total('morte'), transferencias = total('transferencia');
  const pesagens = validos.filter(e => e.tipo === 'pesagem' && Number.isFinite(Number(e.pesoKg))).map(e => ({ data: e.data, pesoKg: Number(e.pesoKg) }));
  const consumoMsKg = validos.filter(e => e.tipo === 'materia_seca').reduce((s, e) => s + Number(e.materiaSecaKg || e.quantidade || 0), 0);
  const cobrancas = total('cobranca'), pagamentos = total('pagamento');
  return { loteId: String(loteId), eventos: validos, entradas, saidas, mortes, transferencias, cabecasAtuais: Math.max(entradas - saidas - mortes - transferencias, 0), pesagens, consumoMsKg, cobrancas, pagamentos, saldoCobrancas: cobrancas - pagamentos, fechado: false };
}

export function fecharLote(resumo, dataFechamento) {
  if (!resumo || !resumo.loteId) throw new Error('resumo obrigatório');
  if (resumo.fechado) return resumo;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(dataFechamento || ''))) throw new Error('data de fechamento inválida');
  return { ...resumo, fechado: true, dataFechamento };
}

