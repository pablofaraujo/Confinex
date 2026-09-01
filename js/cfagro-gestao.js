/* ============================================================
   CFAgro Gestão — projeções somente leitura para Financeiro,
   Pendências e Eventos. Nunca devolve IDs técnicos nem JSON cru.
   ============================================================ */
(function(raiz, fabrica){
'use strict';
var api = fabrica();
if(typeof module === 'object' && module.exports) module.exports = api;
if(raiz) raiz.CFAgroGestao = api;
})(typeof window !== 'undefined' ? window : globalThis, function(){
'use strict';

var STATUS = {
  aguardando_confirmacao:'Aguardando confirmação',
  aguardando_vendedor:'Aguardando vendedor',
  aprovado_confinex:'Aprovado no Confinex',
  cancelado:'Cancelado',
  confirmado_telegram:'Confirmado na origem',
  corrigido:'Corrigido',
  em_aberto:'Em aberto',
  em_execucao:'Em andamento',
  em_revisao:'Em revisão',
  erro:'Falha ao processar',
  erro_pos_gravacao:'Precisa de conferência',
  executado:'Concluído',
  parcial:'Parcial',
  pendente:'Pendente',
  previsto:'Previsto',
  quitada:'Quitada',
  quitado:'Quitado',
  renegociado:'Renegociado',
  revisao_necessaria:'Revisão necessária',
  realizado:'Realizado',
  recebido:'Recebido',
  registrado:'Registrado',
  rejeitado:'Rejeitado',
  validado:'Validado'
};

function texto(valor){
  return String(valor == null ? '' : valor).trim();
}

function numero(valor){
  var n = Number(valor);
  return Number.isFinite(n) ? n : 0;
}

function primeiroNumero(){
  for(var i=0; i<arguments.length; i+=1){
    if(arguments[i] !== null && arguments[i] !== undefined && arguments[i] !== ''){
      var n = Number(arguments[i]);
      if(Number.isFinite(n)) return n;
    }
  }
  return 0;
}

function pareceIdTecnico(valor){
  return /^[0-9a-f]{8}-[0-9a-f-]{27,}$/i.test(texto(valor));
}

function objeto(valor){
  return valor && typeof valor === 'object' && !Array.isArray(valor) ? valor : {};
}

function limparTextoTecnico(valor){
  if(valor && typeof valor === 'object') return '';
  var limpo = texto(valor);
  if(!limpo || /^[{[]/.test(limpo) || limpo === '[object Object]') return '';
  limpo = limpo
    .replace(/[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}/ig, '')
    .replace(/\btelegram:-?\d{6,}\b/ig, '')
    .replace(/\bgrupo[_ ]?id\s*[:=]\s*-?\d+\b/ig, '')
    .replace(/\.html\b/ig, '')
    .replace(/_/g, ' ')
    .replace(/\bjuan promover pending action\b/ig, 'Juan · ação pendente de promoção')
    .replace(/\bcompras missing fields\b/ig, 'Compras com campos faltantes')
    .replace(/\bpending action\b/ig, 'ação pendente')
    .replace(/\bmissing fields\b/ig, 'campos faltantes')
    .replace(/\brevisao\b/ig, 'revisão')
    .replace(/\brevisoes\b/ig, 'revisões')
    .replace(/\breconciliacao\b/ig, 'reconciliação')
    .replace(/\bpromocao\b/ig, 'promoção')
    .replace(/\bconfirmacao\b/ig, 'confirmação')
    .replace(/\boperacao\b/ig, 'operação')
    .replace(/\bgravacao\b/ig, 'gravação')
    .replace(/\btecnicos?\b/ig, function(palavra){ return palavra.toLowerCase().endsWith('s') ? 'técnicos' : 'técnico'; })
    .replace(/\bselecao\b/ig, 'seleção')
    .replace(/\bvalidacao\b/ig, 'validação')
    .replace(/\s{2,}/g, ' ')
    .replace(/^[\s·,;:/-]+|[\s·,;:/-]+$/g, '');
  if(/^[a-záàâãéêíóôõúç][a-záàâãéêíóôõúç0-9 .·/-]*$/.test(limpo)){
    limpo = limpo.charAt(0).toUpperCase()+limpo.slice(1);
  }
  return limpo;
}

function dadosHumanos(item){
  var payload = objeto(item && item.payload);
  var dados = objeto(item && item.dados);
  return Object.assign(
    {},
    objeto(payload.dados_extraidos),
    objeto(item && item.dados_extraidos),
    dados
  );
}

function referenciaHumana(){
  for(var i=0; i<arguments.length; i+=1){
    var candidato = limparTextoTecnico(arguments[i]);
    if(candidato && !pareceIdTecnico(candidato)) return candidato;
  }
  return 'Não informada';
}

function diasEntre(hoje, data){
  if(!data) return null;
  var inicio = new Date(String(hoje || new Date().toISOString().slice(0,10)).slice(0,10)+'T12:00:00');
  var fim = new Date(String(data).slice(0,10)+'T12:00:00');
  if(Number.isNaN(inicio.getTime()) || Number.isNaN(fim.getTime())) return null;
  return Math.round((fim-inicio)/86400000);
}

function statusHumano(valor){
  var chave = texto(valor).toLowerCase();
  if(!chave) return 'Não informado';
  return STATUS[chave] || limparTextoTecnico(chave);
}

function contextoHumano(item){
  var dados = dadosHumanos(item);
  var operacao = objeto(item && item.operacoes);
  var candidatos = [
    item && item.contexto_nome,
    dados.contexto_nome,
    dados.grupo_telegram,
    dados.contexto_operacional,
    item && item.entidade_codigo,
    item && item.codigo_sugerido,
    item && item.operacao_codigo,
    operacao.codigo
  ];
  for(var i=0; i<candidatos.length; i+=1){
    var candidato = limparTextoTecnico(candidatos[i]);
    if(candidato && !pareceIdTecnico(candidato)) return candidato;
  }
  return 'Contexto não informado';
}

function dataItem(item){
  return item && (item.atualizado_em || item.criado_em || item.created_at || item.data || item.vencimento) || null;
}

function resumoItem(item, tipo){
  var dados = dadosHumanos(item);
  if(tipo === 'rascunho') return referenciaHumana(
    item.resumo, dados.resumo, dados.descricao, dados.situacao,
    item.tipo_operacao && statusHumano(item.tipo_operacao),
    'Rascunho aguardando conferência'
  );
  if(tipo === 'ação') return referenciaHumana(
    item.resumo, dados.resumo, dados.descricao,
    item.acao_tipo && statusHumano(item.acao_tipo),
    'Ação aguardando conferência'
  );
  if(tipo === 'documento') return (texto(item.status).toLowerCase() === 'revisao_necessaria' ? 'Revisar documento: ' : 'Documento: ') +
    (item.tipo ? statusHumano(item.tipo) : 'tipo não informado');
  return referenciaHumana(
    item.observacao, item.resumo, item.descricao, dados.observacao,
    dados.resumo, dados.descricao, item.tipo && statusHumano(item.tipo),
    'Evento sem descrição'
  );
}

function destinoOperacional(item, categoria){
  var dados = dadosHumanos(item);
  var pista = [
    categoria,
    item && item.tipo,
    item && item.tipo_operacao,
    item && item.acao_tipo,
    item && item.entidade_tipo,
    item && item.origem,
    item && item.entidade_codigo,
    dados.tipo_negocio,
    dados.contexto_operacional,
    dados.target_table
  ].join(' ').toLowerCase();
  if(categoria === 'rascunho' || categoria === 'ação' ||
      /revis|rascunh|pending.?action|operation.?draft|promoc/.test(pista)){
    return {rotulo:'Revisões', href:'./revisoes.html'};
  }
  if(/confin/.test(pista)) return {rotulo:'Confinamento', href:'./confinamento.html'};
  if(/abate/.test(pista)) return {rotulo:'Abate', href:'./abate.html'};
  if(/pesag|caderno|ocr/.test(pista)) return {rotulo:'OCR Pesagem', href:'./ocr-pesagem.html'};
  if(/bgi|hedge|bolsa/.test(pista)) return {rotulo:'BGI', href:'./bgi.html'};
  if(categoria === 'documento' || /compra|venda|boi.?balan/.test(pista)){
    return {rotulo:'Boi Balança', href:'./bb.html'};
  }
  return {rotulo:'Visão Geral', href:'./index.html'};
}

// Propostas do cérebro de rentabilidade (pending_actions com
// acao_tipo='proposta_rentabilidade', executavel=false) são informativas:
// a aplicação passa pelas ferramentas dedicadas após o gate humano, nunca
// pelo promotor legado. Aqui elas ganham linha própria, legível, apontando
// para o comparativo em Operações → Confinamento.
function linhaPropostaCerebro(item){
  var payload = objeto(item && item.payload);
  var resumoNumeros = objeto(payload.resumo);
  var resumo = texto(item && item.resumo).replace(/^Cérebro rentabilidade — /,'');
  if(!resumo){
    var decisoes = (Array.isArray(payload.decisoes) ? payload.decisoes : [])
      .map(function(d){ return texto(d && d.decisao); }).filter(Boolean);
    resumo = decisoes.length ?
      'Proposta do cérebro: ' + decisoes.join(' + ') :
      'Proposta de rentabilidade aguardando avaliação';
    if(Number.isFinite(Number(resumoNumeros.desvio_total))){
      resumo += ' (desvio R$ ' + Number(resumoNumeros.desvio_total)
        .toLocaleString('pt-BR', {minimumFractionDigits:2, maximumFractionDigits:2}) + ')';
    }
  }
  return {
    origem:'Cérebro',
    resumo:resumo,
    contexto:referenciaHumana(item && item.entidade_codigo, 'Operação'),
    status:statusHumano(item && item.status),
    data:dataItem(item),
    destino:{rotulo:'Confinamento', href:'./confinamento.html'},
    acao:'Avaliar'
  };
}

function pendenciasLegiveis(rascunhos, acoes, documentos){
  var linhas = [];
  (rascunhos || []).forEach(function(item){
    linhas.push({origem:'Revisões', resumo:resumoItem(item,'rascunho'), contexto:contextoHumano(item), status:statusHumano(item.status), data:dataItem(item), destino:destinoOperacional(item,'rascunho'), acao:'Revisar'});
  });
  (acoes || []).forEach(function(item){
    if(texto(item && item.acao_tipo).toLowerCase() === 'proposta_rentabilidade'){
      linhas.push(linhaPropostaCerebro(item));
      return;
    }
    linhas.push({origem:'Ações', resumo:resumoItem(item,'ação'), contexto:contextoHumano(item), status:statusHumano(item.status), data:dataItem(item), destino:destinoOperacional(item,'ação'), acao:'Conferir'});
  });
  (documentos || []).forEach(function(item){
    var contexto = contextoHumano(item);
    if(contexto === 'Contexto não informado') contexto = 'Documento operacional';
    linhas.push({origem:'Documentos', resumo:resumoItem(item,'documento'), contexto:contexto, status:statusHumano(item.status), data:dataItem(item), destino:destinoOperacional(item,'documento'), acao:'Abrir origem'});
  });
  return linhas.sort(function(a,b){ return String(b.data || '').localeCompare(String(a.data || '')); });
}

function codigoNormalizado(valor){
  return texto(valor).toUpperCase();
}

function estimativaConfinexValida(estimativa){
  if(!estimativa || typeof estimativa !== 'object' || Array.isArray(estimativa)) return false;
  var identificada = texto(estimativa.id) || numero(estimativa.versao) > 0;
  return Boolean(
    identificada &&
    Object.keys(objeto(estimativa.premissas)).length &&
    Object.keys(objeto(estimativa.resultado)).length
  );
}

function planejamentosRentabilidadePendentes(operacoes, avaliacoes){
  var avaliacoesAtivas = (avaliacoes || []).filter(function(avaliacao){
    return texto(avaliacao && avaliacao.status).toLowerCase() !== 'cancelado';
  });
  return (operacoes || []).filter(function(operacao){
    var status = texto(operacao && operacao.status).toLowerCase();
    var tipo = texto(operacao && operacao.tipo_negocio).toLowerCase();
    return tipo === 'confinamento' && !['cancelada','cancelado'].includes(status);
  }).filter(function(operacao){
    var codigo = codigoNormalizado(operacao.codigo);
    var planejamentoValido = avaliacoesAtivas.some(function(item){
      var corresponde = Boolean(
        (texto(item.operacao_id) && texto(item.operacao_id) === texto(operacao.id)) ||
        (codigo && codigoNormalizado(item.codigo) === codigo)
      );
      return corresponde && (item.confinex_estimativas || []).some(estimativaConfinexValida);
    });
    return !planejamentoValido;
  }).map(function(operacao){
    return {
      origem:'Planejamento',
      resumo:'Planejamento de rentabilidade não registrado',
      contexto:referenciaHumana(operacao.codigo, 'Negócio de confinamento'),
      status:'Pendente',
      data:dataItem(operacao),
      destino:{rotulo:'Confinex', href:'./confinex.html'},
      acao:'Planejar'
    };
  }).sort(function(a,b){
    return String(a.contexto || '').localeCompare(String(b.contexto || ''), 'pt-BR');
  });
}

function eventosLegiveis(eventos){
  return (eventos || []).map(function(item){
    return {
      tipo:statusHumano(item.tipo),
      resumo:resumoItem(item,'evento'),
      contexto:contextoHumano(item),
      status:statusHumano(item.status),
      responsavel:referenciaHumana(item.usuario, item.agente, 'Não informado'),
      data:dataItem(item),
      origem:destinoOperacional(item,'evento')
    };
  });
}

function sinalFluxo(item){
  return texto(item && item.tipo).toLowerCase() === 'saida' ? -1 : 1;
}

function resumoFinanceiro(fluxo){
  var r = {previsto:0, realizado:0, aReceber:0, aPagar:0, quantidade:0};
  (fluxo || []).forEach(function(item){
    var valor = numero(item.valor);
    var sinal = sinalFluxo(item);
    r.previsto += sinal * valor;
    r.quantidade += 1;
    if(item.realizado === true) r.realizado += sinal * valor;
    else if(sinal > 0) r.aReceber += valor;
    else r.aPagar += valor;
  });
  return r;
}

function origemFinanceira(item, fonte){
  var pista = [
    fonte,
    item && item.origem_tipo,
    item && item.categoria,
    item && item.negocio,
    item && item.lote_ref
  ].join(' ').toLowerCase();
  if(/confin|\bcf-\d/.test(pista)) return {rotulo:'Confinamento', href:'./confinamento.html'};
  if(/compra|venda|boi.?balan/.test(pista)) return {rotulo:'Boi Balança', href:'./bb.html'};
  if(/promiss/.test(pista)) return {rotulo:'Promissória', href:'./financeiro.html'};
  if(/empr[eé]st|cprf|d[ií]vida/.test(pista)) return {rotulo:'Empréstimo', href:'./financeiro.html'};
  return {rotulo:'Financeiro', href:'./financeiro.html'};
}

function obrigacoesFinanceiras(fluxo, hoje){
  return (fluxo || []).map(function(item){
    var natureza = texto(item.tipo).toLowerCase() === 'saida' ? 'pagar' : 'receber';
    var valorOriginal = Math.max(0, primeiroNumero(item.valor_original, item.valor));
    var realizado = item.realizado === true || /realizad|quitad|recebid/.test(texto(item.status).toLowerCase());
    var valorPago = Math.max(0, Math.min(valorOriginal, realizado ? valorOriginal : primeiroNumero(item.valor_pago, item.valor_realizado, item.total_pago)));
    var temSaldo = item.saldo_aberto !== null && item.saldo_aberto !== undefined && item.saldo_aberto !== '' ||
      item.saldo !== null && item.saldo !== undefined && item.saldo !== '';
    var saldo = Math.max(0, temSaldo ? primeiroNumero(item.saldo_aberto, item.saldo) : valorOriginal-valorPago);
    if(realizado) saldo = 0;
    var vencimento = item.vencimento || item.data_prevista || item.data || null;
    var dias = diasEntre(hoje, vencimento);
    var status = realizado ? 'Realizado' : valorPago > 0 ? 'Parcial' : dias !== null && dias < 0 ? 'Atrasado' : 'Previsto';
    return {
      natureza:natureza,
      descricao:referenciaHumana(item.descricao, item.categoria, 'Movimentação financeira'),
      categoria:referenciaHumana(item.categoria, 'Não informada'),
      referencia:referenciaHumana(item.origem_referencia, item.lote_ref, item.negocio, item.codigo),
      origem:origemFinanceira(item, 'fluxo_caixa'),
      vencimento:vencimento,
      diasAteVencimento:dias,
      valorOriginal:valorOriginal,
      valorPago:valorPago,
      saldo:saldo,
      status:status
    };
  }).sort(function(a,b){ return String(a.vencimento || '').localeCompare(String(b.vencimento || '')); });
}

function dividasFinanceiras(emprestimos, promissorias, hoje){
  var linhas = [];
  (emprestimos || []).forEach(function(item){
    var statusOriginal = texto(item.status).toLowerCase();
    var valorOriginal = Math.max(0, primeiroNumero(item.valor_principal, item.principal, item.valor));
    var saldoInformado = primeiroNumero(item.saldo_devedor, item.saldo, item.valor_em_aberto);
    var temSaldoInformado = [item.saldo_devedor,item.saldo,item.valor_em_aberto].some(function(valor){
      return valor !== null && valor !== undefined && valor !== '';
    });
    var quitada = /quitad|pago|encerrad/.test(statusOriginal);
    var saldo = quitada ? 0 : Math.max(0, temSaldoInformado ? saldoInformado : valorOriginal);
    var vencimento = item.proximo_vencimento || item.vencimento || item.data_vencimento || null;
    var dias = diasEntre(hoje, vencimento);
    linhas.push({
      origem:'Empréstimo',
      referencia:referenciaHumana(item.numero_contrato, item.contrato, item.descricao, 'Contrato'),
      contraparte:referenciaHumana(item.credor, item.instituicao, item.banco, 'Não informada'),
      vencimento:vencimento,
      diasAteVencimento:dias,
      valorOriginal:valorOriginal,
      valorPago:Math.max(0, valorOriginal-saldo),
      saldo:saldo,
      parcelas:referenciaHumana(item.parcelas_pagas && item.numero_parcelas ? item.parcelas_pagas+'/'+item.numero_parcelas : '', item.numero_parcelas ? item.numero_parcelas+' parcela(s)' : '', 'Não informadas'),
      taxa:item.taxa_juros_aa == null ? null : numero(item.taxa_juros_aa),
      renegociada:Boolean(item.renegociado_em || item.renegociacao_id || statusOriginal === 'renegociado'),
      status:quitada ? 'Quitado' : dias !== null && dias < 0 ? 'Atrasado' : saldo < valorOriginal ? 'Parcial' : statusHumano(item.status || 'em_aberto'),
      origemLink:origemFinanceira(item, 'emprestimo')
    });
  });
  (promissorias || []).forEach(function(item){
    var statusOriginal = texto(item.status).toLowerCase();
    var valorOriginal = Math.max(0, primeiroNumero(item.valor));
    var quitada = /quitad|pago|encerrad/.test(statusOriginal);
    var valorPago = quitada ? valorOriginal : Math.max(0, Math.min(valorOriginal, primeiroNumero(item.valor_pago, item.total_pago)));
    var saldo = Math.max(0, valorOriginal-valorPago);
    var vencimento = item.vencimento || item.data_vencimento || null;
    var dias = diasEntre(hoje, vencimento);
    linhas.push({
      origem:'Promissória',
      referencia:referenciaHumana(item.numero, item.referencia, 'Documento'),
      contraparte:referenciaHumana(item.credor, 'Não informada'),
      vencimento:vencimento,
      diasAteVencimento:dias,
      valorOriginal:valorOriginal,
      valorPago:valorPago,
      saldo:saldo,
      parcelas:'Parcela única',
      taxa:null,
      renegociada:Boolean(item.renegociado_em || item.renegociacao_id || statusOriginal === 'renegociado'),
      status:quitada ? 'Quitada' : valorPago > 0 ? 'Parcial' : dias !== null && dias < 0 ? 'Atrasada' : statusHumano(item.status || 'em_aberto'),
      origemLink:origemFinanceira(item, 'promissoria')
    });
  });
  return linhas.sort(function(a,b){ return String(a.vencimento || '').localeCompare(String(b.vencimento || '')); });
}

function transacoesFinanceiras(transacoes){
  return (transacoes || []).map(function(item){
    return {
      data:item.data || item.data_transacao || item.created_at || null,
      descricao:referenciaHumana(item.descricao, item.memo, item.historico, 'Transação bancária'),
      categoria:referenciaHumana(item.categoria, item.tipo, 'Não informada'),
      negocio:referenciaHumana(item.lote_ref, item.negocio, item.subtipo),
      valor:primeiroNumero(item.valor, item.amount),
      origem:origemFinanceira(item, 'transacoes_banco')
    };
  }).sort(function(a,b){ return String(b.data || '').localeCompare(String(a.data || '')); });
}

function conciliacoesBancariasPendentes(conciliacoes, transacoesStaging, candidatos, fluxos){
  var bancoPorId = new Map((transacoesStaging || []).map(function(item){ return [String(item.id),item]; }));
  var candidatoPorId = new Map((candidatos || []).map(function(item){ return [String(item.id),item]; }));
  var fluxoPorId = new Map((fluxos || []).map(function(item){ return [String(item.id),item]; }));
  return (conciliacoes || []).filter(function(item){
    return String(item.estado || '').toLowerCase() === 'pendente';
  }).map(function(item){
    var banco = bancoPorId.get(String(item.transacao_staging_id)) || {};
    var candidato = candidatoPorId.get(String(item.negocio_candidato_id)) || null;
    var fluxo = fluxoPorId.get(String(item.fluxo_caixa_id)) || null;
    var alvo = candidato || fluxo || {};
    var classificacao = statusHumano(item.classificacao || 'possivel');
    return {
      idInterno:item.id || null,
      data:banco.data || null,
      descricao:referenciaHumana(banco.memo, banco.descricao, 'Movimentação bancária'),
      valor:Math.abs(primeiroNumero(item.valor_alocado, banco.valor)),
      negocio:referenciaHumana(candidato && candidato.codigo_fonte, fluxo && fluxo.negocio, 'Ainda não relacionado'),
      contraparte:referenciaHumana(candidato && candidato.nome, fluxo && fluxo.descricao, 'A conferir'),
      contexto:referenciaHumana(candidato && candidato.contexto, fluxo && fluxo.categoria, 'Financeiro'),
      classificacao:classificacao,
      justificativa:referenciaHumana(item.justificativa, 'Conferir a sugestão antes de relacionar.'),
      status:'Aguardando conferência'
    };
  }).sort(function(a,b){
    return String(b.data || '').localeCompare(String(a.data || '')) ||
      String(a.negocio || '').localeCompare(String(b.negocio || ''), 'pt-BR');
  });
}

function lembretesFinanceiros(obrigacoes, dividas){
  return (obrigacoes || []).concat(dividas || []).filter(function(item){
    return item.saldo > 0 && item.diasAteVencimento !== null && item.diasAteVencimento <= 30;
  }).map(function(item){
    var dias = item.diasAteVencimento;
    return {
      titulo:item.descricao || [item.origem,item.referencia].filter(Boolean).join(' · '),
      vencimento:item.vencimento,
      saldo:item.saldo,
      urgencia:dias < 0 ? 'atrasado' : dias <= 7 ? 'urgente' : 'proximo',
      mensagem:dias < 0 ? 'Vencido há '+Math.abs(dias)+' dia(s)' : dias === 0 ? 'Vence hoje' : 'Vence em '+dias+' dia(s)'
    };
  }).sort(function(a,b){ return String(a.vencimento || '').localeCompare(String(b.vencimento || '')); });
}

function resumoFinanceiroAmpliado(obrigacoes, dividas){
  var resumo = {aReceber:0, aPagar:0, realizado:0, vencido:0, proximos30:0, dividaAberta:0};
  (obrigacoes || []).forEach(function(item){
    if(item.status === 'Realizado') resumo.realizado += item.natureza === 'pagar' ? -item.valorPago : item.valorPago;
    else if(item.natureza === 'pagar') resumo.aPagar += item.saldo;
    else resumo.aReceber += item.saldo;
    if(item.saldo > 0 && item.diasAteVencimento !== null && item.diasAteVencimento < 0) resumo.vencido += item.saldo;
    if(item.saldo > 0 && item.diasAteVencimento !== null && item.diasAteVencimento >= 0 && item.diasAteVencimento <= 30) resumo.proximos30 += item.saldo;
  });
  (dividas || []).forEach(function(item){ resumo.dividaAberta += item.saldo; });
  return resumo;
}

function erroLegivel(erro){
  if(!erro) return 'Não foi possível carregar os dados.';
  return 'Não foi possível carregar os dados. Tente atualizar a página.';
}

return {
  contextoHumano:contextoHumano,
  conciliacoesBancariasPendentes:conciliacoesBancariasPendentes,
  dividasFinanceiras:dividasFinanceiras,
  erroLegivel:erroLegivel,
  eventosLegiveis:eventosLegiveis,
  lembretesFinanceiros:lembretesFinanceiros,
  obrigacoesFinanceiras:obrigacoesFinanceiras,
  pendenciasLegiveis:pendenciasLegiveis,
  planejamentosRentabilidadePendentes:planejamentosRentabilidadePendentes,
  resumoFinanceiro:resumoFinanceiro,
  resumoFinanceiroAmpliado:resumoFinanceiroAmpliado,
  transacoesFinanceiras:transacoesFinanceiras,
  statusHumano:statusHumano
};
});
