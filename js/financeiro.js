(function(){
'use strict';
var fluxo = [];
var dividas = [];
var el = function(id){ return document.getElementById(id); };
var esc = function(v){ return CFAgro.esc(v); };

function badge(situacao){
  var normal = String(situacao || '').toLowerCase();
  var classe = /realizado|quitad|recebid/.test(normal) ? 'b-green' : /erro|atras/.test(normal) ? 'b-red' : 'b-amber';
  return '<span class="badge '+classe+'">'+esc(CFAgroGestao.statusHumano(situacao))+'</span>';
}

function renderKpis(){
  var r = CFAgroGestao.resumoFinanceiro(fluxo);
  el('kpis').innerHTML = [
    ['Saldo previsto', CFAgro.fmtR$2(r.previsto)],
    ['Saldo realizado', CFAgro.fmtR$2(r.realizado)],
    ['A receber', CFAgro.fmtR$2(r.aReceber)],
    ['A pagar', CFAgro.fmtR$2(r.aPagar)]
  ].map(function(k){ return '<div class="kpi"><div class="l">'+k[0]+'</div><div class="v">'+k[1]+'</div></div>'; }).join('');
}

function renderMovimentos(){
  var situacao = el('filtroSituacao').value;
  var busca = el('filtroTexto').value.trim().toLowerCase();
  var linhas = fluxo.filter(function(item){
    var tipo = String(item.tipo || '').toLowerCase();
    var bateSituacao = situacao === 'todos' ||
      (situacao === 'realizado' && item.realizado === true) ||
      (situacao === 'previsto' && item.realizado !== true) ||
      ((situacao === 'entrada' || situacao === 'saida') && tipo === situacao && item.realizado !== true);
    var base = [item.descricao,item.categoria,CFAgroGestao.statusHumano(item.realizado ? 'realizado' : 'pendente')].join(' ').toLowerCase();
    return bateSituacao && (!busca || base.includes(busca));
  });
  el('movimentos').innerHTML = linhas.map(function(item){
    var tipo = String(item.tipo || '').toLowerCase();
    var classe = tipo === 'saida' ? 'neg' : 'pos';
    return '<tr><td>'+CFAgro.fmtD(item.data)+'</td><td class="wrap">'+esc(item.descricao || 'Sem descrição')+'</td><td>'+esc(item.categoria || 'Não informada')+'</td><td>'+badge(item.realizado ? 'realizado' : 'pendente')+'</td><td class="num '+classe+'">'+CFAgro.fmtR$2(item.valor)+'</td></tr>';
  }).join('') || '<tr><td colspan="5" class="wrap">Nenhuma movimentação corresponde aos filtros.</td></tr>';
}

function renderDividas(){
  el('dividas').innerHTML = dividas.map(function(item){
    return '<tr><td>'+esc(item.origem)+'</td><td>'+esc(item.referencia)+'</td><td>'+CFAgro.fmtD(item.vencimento)+'</td><td>'+badge(item.status)+'</td><td class="num">'+CFAgro.fmtR$2(item.valor)+'</td></tr>';
  }).join('') || '<tr><td colspan="5" class="wrap">Nenhuma dívida ou promissória encontrada.</td></tr>';
}

async function carregar(){
  el('subtitle').textContent = 'Carregando dados financeiros…';
  var respostas = await Promise.all([
    db.from('fluxo_caixa').select('data,descricao,tipo,valor,realizado,categoria').order('data',{ascending:false}).limit(500),
    db.from('emprestimos').select('*').order('vencimento',{ascending:true}),
    db.from('promissorias').select('*').order('vencimento',{ascending:true})
  ]);
  var falha = respostas.find(function(r){ return r.error; });
  if(falha){
    el('subtitle').textContent = CFAgroGestao.erroLegivel(falha.error);
    fluxo = []; dividas = []; renderKpis(); renderMovimentos(); renderDividas();
    return;
  }
  fluxo = respostas[0].data || [];
  dividas = (respostas[1].data || []).map(function(item){
    return {origem:'Empréstimo', referencia:item.numero_contrato || item.contrato || item.descricao || 'Contrato', vencimento:item.vencimento, status:item.status, valor:item.saldo_devedor || item.saldo || item.valor};
  }).concat((respostas[2].data || []).map(function(item){
    return {origem:'Promissória', referencia:item.numero || 'Documento', vencimento:item.vencimento, status:item.status, valor:item.valor};
  }));
  renderKpis(); renderMovimentos(); renderDividas();
  el('subtitle').textContent = 'Atualizado '+CFAgro.fmtDT(new Date().toISOString())+' · consulta somente leitura';
}

el('filtroSituacao').addEventListener('change', renderMovimentos);
el('filtroTexto').addEventListener('input', renderMovimentos);
el('atualizarBtn').addEventListener('click', carregar);
el('entrarBtn').addEventListener('click', function(){ entrar(); });
el('sairBtn').addEventListener('click', function(){ sair(); });
CFAgro.authInit(carregar);
})();
