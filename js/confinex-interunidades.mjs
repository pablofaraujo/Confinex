const NUMEROS_POSITIVOS = ['quantidade', 'pesoTotalKg', 'precoArroba'];

function numeroPositivo(valor, campo) {
  const numero = Number(valor);
  if (!Number.isFinite(numero) || numero <= 0) {
    throw new Error(`${campo} deve ser maior que zero`);
  }
  return numero;
}

function quaseIgual(a, b, tolerancia = 0.01) {
  return Math.abs(Number(a) - Number(b)) <= tolerancia;
}

/** Calcula uma compra/venda a rendimento informado, sem arredondar arrobas. */
export function calcularNegocioPorPeso({
  quantidade,
  pesoTotalKg,
  precoArroba,
  rendimentoCarnePct = 50,
}) {
  const entrada = { quantidade, pesoTotalKg, precoArroba };
  for (const campo of NUMEROS_POSITIVOS) numeroPositivo(entrada[campo], campo);
  if (!Number.isInteger(Number(quantidade))) throw new Error('quantidade deve ser inteira');
  const rendimento = numeroPositivo(rendimentoCarnePct, 'rendimentoCarnePct');
  if (rendimento > 100) throw new Error('rendimentoCarnePct não pode exceder 100');

  const arrobas = Number(pesoTotalKg) * (rendimento / 100) / 15;
  return {
    quantidade: Number(quantidade),
    pesoTotalKg: Number(pesoTotalKg),
    pesoMedioKg: Number(pesoTotalKg) / Number(quantidade),
    rendimentoCarnePct: rendimento,
    arrobas,
    precoArroba: Number(precoArroba),
    valorTotal: arrobas * Number(precoArroba),
  };
}

/**
 * Consolida apenas compras-raiz. Componentes servem para rastreabilidade e
 * cobertura; somá-los novamente duplicaria cabeças, peso e valor.
 */
export function consolidarComprasOperacao(compras, componentes = []) {
  if (!Array.isArray(compras) || !Array.isArray(componentes)) {
    throw new Error('compras e componentes devem ser listas');
  }
  const ids = new Set(compras.map((compra) => String(compra.id)));
  const orfaos = componentes.filter(
    (componente) => !ids.has(String(componente.compraAgregadaId)),
  );
  if (orfaos.length) throw new Error('há componente sem compra agregada');

  const somar = (linhas, campo) => linhas.reduce(
    (total, linha) => total + Number(linha[campo] || 0),
    0,
  );
  return {
    compras: compras.length,
    componentes: componentes.length,
    quantidade: somar(compras, 'quantidade'),
    pesoTotalKg: somar(compras, 'pesoTotalKg'),
    valorTotal: somar(compras, 'valorTotal'),
    coberturaComponentes: {
      quantidade: somar(componentes, 'quantidade'),
      pesoTotalKg: somar(componentes, 'pesoTotalKg'),
      valorTotal: somar(componentes, 'valorTotal'),
    },
  };
}

/** Valida as três faces do mesmo movimento antes de qualquer persistência. */
export function validarMovimentacaoInterunidades({ venda, lancamento, compra, movimento }) {
  if (!venda || !lancamento || !compra || !movimento) {
    throw new Error('venda, lançamento, compra e movimento são obrigatórios');
  }
  const erros = [];
  if (venda.tipo !== 'venda' || venda.estado !== 'confirmado') {
    erros.push('negócio da fazenda não é uma venda confirmada');
  }
  if (lancamento.tipo !== 'saida' || lancamento.negocioFazendaId !== venda.id) {
    erros.push('saída física não está vinculada à venda');
  }
  for (const registro of [venda, compra, movimento]) {
    if (registro.operacaoId !== movimento.operacaoId) {
      erros.push('operação de destino divergente');
      break;
    }
  }
  if (![venda.quantidade, lancamento.quantidade, compra.quantidade]
    .every((valor) => Number(valor) === Number(movimento.quantidade))) {
    erros.push('quantidade divergente');
  }
  if (![venda.pesoTotalKg, compra.pesoTotalKg]
    .every((valor) => quaseIgual(valor, movimento.pesoTotalKg))) {
    erros.push('peso divergente');
  }
  if (![venda.precoArroba, compra.precoArroba]
    .every((valor) => quaseIgual(valor, movimento.precoArroba))) {
    erros.push('preço divergente');
  }
  if (![venda.valorTotal, compra.valorTotal]
    .every((valor) => quaseIgual(valor, movimento.valorTotal))) {
    erros.push('valor divergente');
  }
  return { ok: erros.length === 0, erros };
}

export function validarParticipacoes(participantes) {
  if (!Array.isArray(participantes)) throw new Error('participantes deve ser uma lista');
  const economicos = participantes.filter(({ papel }) => ['proprietario', 'parceiro'].includes(papel));
  const total = economicos.reduce((soma, item) => soma + numeroPositivo(item.participacaoPct, 'participacaoPct'), 0);
  return { ok: total <= 100, total };
}
