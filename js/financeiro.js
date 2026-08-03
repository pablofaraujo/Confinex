(function(){
'use strict';
var fluxoBruto = [];
var emprestimosBrutos = [];
var promissoriasBrutas = [];
var transacoesBrutas = [];
var obrigacoes = [];
var dividas = [];
var transacoes = [];
var lembretes = [];
var el = function(id){ return document.getElementById(id); };
var esc = function(v){ return CFAgro.esc(v); };

function badge(situacao){
  var normal = String(situacao || '').toLowerCase();
  var classe = /realizado|quitad|recebid/.test(normal) ? 'b-green' :
    /erro|atras|vencid/.test(normal) ? 'b-red' : 'b-amber';
  return '<span class="badge '+classe+'">'+esc(CFAgroGestao.statusHumano(situacao))+'</span>';
}

function linkOrigem(origem){
  if(!origem || !origem.href) return 'Financeiro';
  return '<a href="'+esc(origem.href)+'">'+esc(origem.rotulo)+'</a>';
}

function renderKpis(){
  var r = CFAgroGestao.resumoFinanceiroAmpliado(obrigacoes, dividas);
  el('kpis').innerHTML = [
    ['A receber', CFAgro.fmtR$2(r.aReceber), 'Saldo previsto ainda em aberto'],
    ['A pagar', CFAgro.fmtR$2(r.aPagar), 'Saldo previsto ainda em aberto'],
    ['Realizado', CFAgro.fmtR$2(r.realizado), 'Entradas menos saídas realizadas'],
    ['Vencido', CFAgro.fmtR$2(r.vencido), 'Obrigações vencidas e não realizadas'],
    ['Próximos 30 dias', CFAgro.fmtR$2(r.proximos30), 'Obrigações a vencer'],
    ['Dívida em aberto', CFAgro.fmtR$2(r.dividaAberta), 'Empréstimos e promissórias']
  ].map(function(k){
    return '<div class="kpi"><div class="l">'+k[0]+'</div><div class="v">'+k[1]+'</div><div class="d">'+k[2]+'</div></div>';
  }).join('');
}

function bateFiltroObrigacao(item, filtro){
  if(filtro === 'todos') return true;
  if(filtro === 'pagar' || filtro === 'receber') return item.natureza === filtro && item.status !== 'Realizado';
  if(filtro === 'realizado') return item.status === 'Realizado';
  if(filtro === 'atrasado') return item.saldo > 0 && item.diasAteVencimento !== null && item.diasAteVencimento < 0;
  if(filtro === 'proximos30') return item.saldo > 0 && item.diasAteVencimento !== null && item.diasAteVencimento >= 0 && item.diasAteVencimento <= 30;
  return true;
}

function renderObrigacoes(){
  var filtro = el('filtroSituacao').value;
  var busca = el('filtroTexto').value.trim().toLowerCase();
  var linhas = obrigacoes.filter(function(item){
    var base = [item.descricao,item.categoria,item.referencia,item.origem.rotulo,item.status].join(' ').toLowerCase();
    return bateFiltroObrigacao(item, filtro) && (!busca || base.includes(busca));
  });
  el('obrigacoes').innerHTML = linhas.map(function(item){
    var natureza = item.natureza === 'pagar' ? 'A pagar' : 'A receber';
    var classe = item.natureza === 'pagar' ? 'neg' : 'pos';
    return '<tr>'+
      '<td>'+CFAgro.fmtD(item.vencimento)+'</td>'+
      '<td class="wrap"><strong>'+esc(item.descricao)+'</strong><br><span class="muted">'+esc(item.referencia)+'</span></td>'+
      '<td>'+natureza+'</td>'+
      '<td>'+linkOrigem(item.origem)+'</td>'+
      '<td class="num">'+CFAgro.fmtR$2(item.valorOriginal)+'</td>'+
      '<td class="num">'+CFAgro.fmtR$2(item.valorPago)+'</td>'+
      '<td class="num '+classe+'">'+CFAgro.fmtR$2(item.saldo)+'</td>'+
      '<td>'+badge(item.status)+'</td>'+
    '</tr>';
  }).join('') || '<tr><td colspan="8" class="wrap">Nenhuma obrigação corresponde aos filtros.</td></tr>';
}

function renderDividas(){
  el('dividas').innerHTML = dividas.map(function(item){
    var prazo = item.diasAteVencimento === null ? 'Data não informada' :
      item.diasAteVencimento < 0 ? 'Vencida há '+Math.abs(item.diasAteVencimento)+' dia(s)' :
      item.diasAteVencimento === 0 ? 'Vence hoje' : 'Vence em '+item.diasAteVencimento+' dia(s)';
    return '<tr>'+
      '<td>'+esc(item.origem)+'</td>'+
      '<td class="wrap"><strong>'+esc(item.referencia)+'</strong><br><span class="muted">'+esc(item.contraparte)+'</span></td>'+
      '<td>'+CFAgro.fmtD(item.vencimento)+'<br><span class="muted">'+esc(prazo)+'</span></td>'+
      '<td>'+esc(item.parcelas)+'</td>'+
      '<td class="num">'+(item.taxa == null ? '—' : CFAgro.fmtN(item.taxa,2)+'% a.a.')+'</td>'+
      '<td class="num">'+CFAgro.fmtR$2(item.valorOriginal)+'</td>'+
      '<td class="num">'+CFAgro.fmtR$2(item.valorPago)+'</td>'+
      '<td class="num neg">'+CFAgro.fmtR$2(item.saldo)+'</td>'+
      '<td>'+badge(item.status)+(item.renegociada ? '<br><span class="badge b-amber">Renegociada</span>' : '')+'</td>'+
    '</tr>';
  }).join('') || '<tr><td colspan="9" class="wrap">Nenhuma dívida ou promissória encontrada.</td></tr>';
}

function renderLembretes(){
  el('lembretes').innerHTML = lembretes.map(function(item){
    var classe = item.urgencia === 'atrasado' ? 'b-red' : 'b-amber';
    return '<tr><td>'+CFAgro.fmtD(item.vencimento)+'</td><td class="wrap">'+esc(item.titulo)+'</td><td><span class="badge '+classe+'">'+esc(item.mensagem)+'</span></td><td class="num">'+CFAgro.fmtR$2(item.saldo)+'</td></tr>';
  }).join('') || '<tr><td colspan="4" class="wrap">Nenhum vencimento exige alerta nos próximos 30 dias.</td></tr>';
}

function renderTransacoes(){
  var busca = el('filtroBanco').value.trim().toLowerCase();
  var linhas = transacoes.filter(function(item){
    return !busca || [item.descricao,item.categoria,item.negocio,item.origem.rotulo].join(' ').toLowerCase().includes(busca);
  }).slice(0,200);
  el('transacoes').innerHTML = linhas.map(function(item){
    return '<tr><td>'+CFAgro.fmtD(item.data)+'</td><td class="wrap">'+esc(item.descricao)+'</td><td>'+esc(item.categoria)+'</td><td>'+esc(item.negocio)+'</td><td>'+linkOrigem(item.origem)+'</td><td class="num '+CFAgro.cls(item.valor)+'">'+CFAgro.fmtR$2(item.valor)+'</td></tr>';
  }).join('') || '<tr><td colspan="6" class="wrap">Nenhuma transação bancária corresponde à busca.</td></tr>';
}

function renderTudo(){
  var hoje = new Date().toISOString().slice(0,10);
  obrigacoes = CFAgroGestao.obrigacoesFinanceiras(fluxoBruto, hoje);
  dividas = CFAgroGestao.dividasFinanceiras(emprestimosBrutos, promissoriasBrutas, hoje);
  transacoes = CFAgroGestao.transacoesFinanceiras(transacoesBrutas);
  lembretes = CFAgroGestao.lembretesFinanceiros(obrigacoes, dividas);
  renderKpis();
  renderObrigacoes();
  renderDividas();
  renderLembretes();
  renderTransacoes();
}

async function carregar(){
  el('subtitle').textContent = 'Carregando dados financeiros…';
  el('erroBanco').textContent = '';
  var respostas = await Promise.all([
    db.from('fluxo_caixa').select('*').limit(500),
    db.from('emprestimos').select('*').limit(200),
    db.from('promissorias').select('*').limit(200),
    db.from('transacoes_banco').select('*').limit(500)
  ]);
  var falhasPrincipais = respostas.slice(0,3).filter(function(r){ return r.error; });
  if(falhasPrincipais.length){
    el('subtitle').textContent = CFAgroGestao.erroLegivel(falhasPrincipais[0].error);
    fluxoBruto = []; emprestimosBrutos = []; promissoriasBrutas = [];
  }else{
    fluxoBruto = respostas[0].data || [];
    emprestimosBrutos = respostas[1].data || [];
    promissoriasBrutas = respostas[2].data || [];
  }
  if(respostas[3].error){
    transacoesBrutas = [];
    el('erroBanco').textContent = 'A conciliação bancária não pôde ser carregada. As demais áreas continuam disponíveis.';
  }else{
    transacoesBrutas = respostas[3].data || [];
  }
  renderTudo();
  if(!falhasPrincipais.length){
    el('subtitle').textContent = 'Fluxo, compromissos e movimentações financeiras';
  }
}

el('filtroSituacao').addEventListener('change', renderObrigacoes);
el('filtroTexto').addEventListener('input', renderObrigacoes);
el('filtroBanco').addEventListener('input', renderTransacoes);
el('entrarBtn').addEventListener('click', function(){ entrar(); });
CFAgro.authInit(carregar);
})();
