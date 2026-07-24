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
  erro:'Falha ao processar',
  erro_pos_gravacao:'Precisa de conferência',
  executado:'Concluído',
  pendente:'Pendente',
  quitada:'Quitada',
  quitado:'Quitado',
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

function statusHumano(valor){
  var chave = texto(valor).toLowerCase();
  if(!chave) return 'Não informado';
  return STATUS[chave] || chave.replace(/_/g,' ').replace(/^\w/, function(c){ return c.toUpperCase(); });
}

function contextoHumano(item){
  return texto(item && item.contexto_nome) || 'Contexto não informado';
}

function dataItem(item){
  return item && (item.atualizado_em || item.criado_em || item.created_at || item.data || item.vencimento) || null;
}

function resumoItem(item, tipo){
  if(tipo === 'rascunho') return texto(item.resumo || item.tipo_operacao) || 'Rascunho aguardando conferência';
  if(tipo === 'ação') return texto(item.resumo || item.acao_tipo) || 'Ação aguardando conferência';
  if(tipo === 'documento') return 'Documento: ' + (texto(item.tipo) || 'tipo não informado');
  return texto(item.observacao || item.resumo || item.descricao || item.tipo) || 'Evento sem descrição';
}

function pendenciasLegiveis(rascunhos, acoes, documentos){
  var linhas = [];
  (rascunhos || []).forEach(function(item){
    linhas.push({origem:'Revisões', resumo:resumoItem(item,'rascunho'), contexto:contextoHumano(item), status:statusHumano(item.status), data:dataItem(item)});
  });
  (acoes || []).forEach(function(item){
    linhas.push({origem:'Ações', resumo:resumoItem(item,'ação'), contexto:contextoHumano(item), status:statusHumano(item.status), data:dataItem(item)});
  });
  (documentos || []).forEach(function(item){
    var codigo = texto(item && item.operacoes && item.operacoes.codigo);
    linhas.push({origem:'Documentos', resumo:resumoItem(item,'documento'), contexto:codigo || 'Operação vinculada', status:statusHumano(item.status), data:dataItem(item)});
  });
  return linhas.sort(function(a,b){ return String(b.data || '').localeCompare(String(a.data || '')); });
}

function eventosLegiveis(eventos){
  return (eventos || []).map(function(item){
    return {
      tipo:statusHumano(item.tipo),
      resumo:resumoItem(item,'evento'),
      contexto:contextoHumano(item),
      status:statusHumano(item.status),
      responsavel:texto(item.usuario || item.agente) || 'Não informado',
      data:dataItem(item)
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

function erroLegivel(erro){
  if(!erro) return 'Não foi possível carregar os dados.';
  return 'Não foi possível carregar os dados. Tente atualizar a página.';
}

return {
  contextoHumano:contextoHumano,
  erroLegivel:erroLegivel,
  eventosLegiveis:eventosLegiveis,
  pendenciasLegiveis:pendenciasLegiveis,
  resumoFinanceiro:resumoFinanceiro,
  statusHumano:statusHumano
};
});
