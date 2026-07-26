function dividir(total, quantidade) {
  return Number.isFinite(total) && quantidade > 0 ? total / quantidade : null;
}

export function calcularReferenciasTransporte({
  cabecas,
  pesoOrigem,
  pesoChegada,
  pesoProcessado,
  carcacaSaidaKg,
  custoCompra,
  custoFrete,
  custoConfinamento,
}) {
  const n = Number(cabecas) || 0;
  const origem = Number(pesoOrigem) || 0;
  const chegada = Number(pesoChegada) || 0;
  const processado = Number(pesoProcessado) || 0;
  const carcacaSaida = Number(carcacaSaidaKg) || 0;

  const arrobasSaidaCab = carcacaSaida / 15;
  const arrobasProcessadasCab = processado * 0.5 / 15;
  const arrobasOrigemCab = origem * 0.5 / 15;
  const produzidasDesdeProcessamentoCab = Math.max(arrobasSaidaCab - arrobasProcessadasCab, 0);
  const produzidasDesdeOrigemCab = Math.max(arrobasSaidaCab - arrobasOrigemCab, 0);

  return {
    transporteNaEntrada: {
      arrobasBaseCab: arrobasProcessadasCab,
      arrobasProduzidasCab: produzidasDesdeProcessamentoCab,
      custoArrobaBase: dividir(Number(custoCompra || 0) + Number(custoFrete || 0), arrobasProcessadasCab * n),
      custoArrobaProduzida: dividir(Number(custoConfinamento || 0), produzidasDesdeProcessamentoCab * n),
    },
    transporteNaProducao: {
      arrobasBaseCab: arrobasOrigemCab,
      arrobasProduzidasCab: produzidasDesdeOrigemCab,
      custoArrobaBase: dividir(Number(custoCompra || 0), arrobasOrigemCab * n),
      custoArrobaProduzida: dividir(Number(custoConfinamento || 0) + Number(custoFrete || 0), produzidasDesdeOrigemCab * n),
    },
    perdaPeso: {
      brutaKgCab: Math.max(origem - chegada, 0),
      recuperadaKgCab: Math.max(processado - chegada, 0),
      liquidaKgCab: Math.max(origem - processado, 0),
    },
  };
}
