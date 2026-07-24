const LIMITE_KM = 5000;

export function normalizarDistancia({ origem, destino, km, fonte, calculadaEm, ajusteKm = 0 }) {
  const distancia = Number(km) + Number(ajusteKm || 0);
  if (!String(origem || '').trim() || !String(destino || '').trim()) throw new Error('origem e destino são obrigatórios');
  if (!Number.isFinite(distancia) || distancia <= 0 || distancia > LIMITE_KM) throw new Error('distância inválida');
  if (!String(fonte || '').trim() || !String(calculadaEm || '').trim()) throw new Error('fonte e data são obrigatórias');
  return { origem: String(origem).trim(), destino: String(destino).trim(), km: distancia, kmBase: Number(km), ajusteKm: Number(ajusteKm || 0), fonte: String(fonte).trim(), calculadaEm: String(calculadaEm), congeladaEm: null };
}

export function congelarDistancia(registro, estudoId, congeladaEm = new Date().toISOString()) {
  if (!registro || !String(estudoId || '').trim()) throw new Error('estudo obrigatório');
  if (registro.congeladaEm) return { ...registro };
  return { ...registro, estudoId: String(estudoId).trim(), congeladaEm };
}

export function calcularFrete({ distanciaKm, precoPorKm, pedagios = 0, carretas = 1, responsabilidade = 'meu' }) {
  const km = Number(distanciaKm), preco = Number(precoPorKm), pedagio = Number(pedagios), qtd = Number(carretas);
  if (![km, preco, pedagio, qtd].every(Number.isFinite) || km <= 0 || preco < 0 || pedagio < 0 || qtd <= 0) throw new Error('parâmetros de frete inválidos');
  const bruto = (km * 2 * preco + pedagio) * qtd;
  const total = responsabilidade === 'confinamento' ? 0 : responsabilidade === 'dividido' ? bruto / 2 : bruto;
  return { bruto, total, porCabeca: total / qtd };
}

