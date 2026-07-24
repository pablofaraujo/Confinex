(function(){
'use strict';
var itens = [];
var el = function(id){ return document.getElementById(id); };
var esc = function(v){ return CFAgro.esc(v); };

function classeStatus(status){
  var s = String(status || '').toLowerCase();
  return /falha|erro|cancel/.test(s) ? 'b-red' : /registr|conclu|corrigid/.test(s) ? 'b-green' : 'b-amber';
}

function dentroDoPeriodo(item, periodo){
  if(periodo === 'todos') return true;
  if(!item.data) return false;
  var data = new Date(item.data);
  if(Number.isNaN(data.getTime())) return false;
  var limite = new Date();
  limite.setDate(limite.getDate()-Number(periodo));
  return data >= limite;
}

function render(){
  var situacao = el('filtroSituacao').value;
  var tipo = el('filtroTipo').value;
  var periodo = el('filtroPeriodo').value;
  var busca = el('filtroTexto').value.trim().toLowerCase();
  var filtrados = itens.filter(function(item){
    var base = [item.tipo,item.resumo,item.contexto,item.status,item.responsavel].join(' ').toLowerCase();
    return (situacao === 'todas' || item.status === situacao) &&
      (tipo === 'todos' || item.tipo === tipo) &&
      dentroDoPeriodo(item, periodo) &&
      (!busca || base.includes(busca));
  });
  el('listaEventos').innerHTML = filtrados.map(function(item){
    return '<tr><td>'+CFAgro.fmtDT(item.data)+'</td><td>'+esc(item.tipo)+'</td><td class="wrap">'+esc(item.resumo)+'</td><td class="wrap">'+esc(item.contexto)+'</td><td><span class="badge '+classeStatus(item.status)+'">'+esc(item.status)+'</span></td><td>'+esc(item.responsavel)+'</td><td><a class="btn mini sec" href="'+esc(item.origem.href)+'">'+esc(item.origem.rotulo)+'</a></td></tr>';
  }).join('') || '<tr><td colspan="7" class="wrap">Nenhum evento corresponde aos filtros.</td></tr>';
}

function montarFiltros(){
  var situacoes = Array.from(new Set(itens.map(function(item){ return item.status; }))).sort();
  var tipos = Array.from(new Set(itens.map(function(item){ return item.tipo; }))).sort();
  el('filtroSituacao').innerHTML = '<option value="todas">Todas</option>'+situacoes.map(function(valor){ return '<option>'+esc(valor)+'</option>'; }).join('');
  el('filtroTipo').innerHTML = '<option value="todos">Todos</option>'+tipos.map(function(valor){ return '<option>'+esc(valor)+'</option>'; }).join('');
}

async function carregar(){
  el('subtitle').textContent = 'Carregando histórico…';
  var resposta = await db.from('eventos').select('*').limit(500);
  if(resposta.error){
    itens = []; montarFiltros(); render();
    el('subtitle').textContent = CFAgroGestao.erroLegivel(resposta.error);
    return;
  }
  itens = CFAgroGestao.eventosLegiveis(resposta.data).sort(function(a,b){ return String(b.data || '').localeCompare(String(a.data || '')); });
  montarFiltros(); render();
  el('subtitle').textContent = 'Atualizado '+CFAgro.fmtDT(new Date().toISOString())+' · '+itens.length+' eventos';
}

el('filtroSituacao').addEventListener('change', render);
el('filtroTipo').addEventListener('change', render);
el('filtroPeriodo').addEventListener('change', render);
el('filtroTexto').addEventListener('input', render);
el('atualizarBtn').addEventListener('click', carregar);
el('entrarBtn').addEventListener('click', function(){ entrar(); });
el('sairBtn').addEventListener('click', function(){ sair(); });
CFAgro.authInit(carregar);
})();
