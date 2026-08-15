(function(){
'use strict';

var dados = {ofertas:[], negociacoes:[], contatos:[], followups:[]};
var porContato = {};
var el = function(id){ return document.getElementById(id); };
var esc = function(valor){ return CFAgro.esc(valor == null ? '' : valor); };

function rotuloStatus(status){
  return String(status || '—').replace(/_/g, ' ');
}

function classeStatus(status){
  if(/ganha|convertida|concluido/.test(status)) return 'b-green';
  if(/perdida|cancelada|descartada|expirada/.test(status)) return 'b-red';
  if(/incompleta|pendente|aguardando/.test(status)) return 'b-amber';
  return 'b-blue';
}

function nomeContato(id){
  return porContato[id] ? porContato[id].nome : 'Não identificado';
}

function localizacao(item){
  return [item.municipio, item.uf].filter(Boolean).join(' / ') || '—';
}

function renderKpis(){
  var abertas = dados.ofertas.filter(function(item){ return !/descartada|convertida|expirada/.test(item.status); });
  var incompletas = abertas.filter(function(item){ return item.status === 'incompleta'; });
  var ativas = dados.negociacoes.filter(function(item){ return !/fechada|cancelada/.test(item.status); });
  var ganhas = dados.negociacoes.filter(function(item){ return item.status === 'fechada_ganha'; });
  var perdidas = dados.negociacoes.filter(function(item){ return item.status === 'fechada_perdida'; });
  var itens = [
    ['Contatos', dados.contatos.length, 'fornecedores, corretores e frigoríficos'],
    ['Ofertas abertas', abertas.length, incompletas.length+' com informação faltante'],
    ['Em negociação', ativas.length, 'acompanhamento ativo'],
    ['Fechadas', ganhas.length+' / '+perdidas.length, 'ganhas / perdidas']
  ];
  el('kpis').innerHTML = itens.map(function(item){
    return '<div class="kpi"><div class="l">'+esc(item[0])+'</div><div class="v">'+esc(item[1])+'</div><div class="d">'+esc(item[2])+'</div></div>';
  }).join('');
}

function renderOfertas(){
  var abertas = dados.ofertas.filter(function(item){ return !/descartada|convertida|expirada/.test(item.status); });
  el('listaOfertas').innerHTML = abertas.map(function(item){
    var faltantes = (item.campos_faltantes || []).map(function(campo){ return campo.replace(/_/g, ' '); }).join(', ') || '—';
    return '<tr><td>'+fmtDT(item.recebida_em)+'</td><td>'+esc(nomeContato(item.fornecedor_id))+'</td><td>'+esc(rotuloStatus(item.sexo))+'</td><td class="num">'+fmtN(item.quantidade)+'</td><td class="num">'+(item.peso_medio_kg == null ? '—' : fmtN(item.peso_medio_kg, 1)+' kg')+'</td><td class="num">'+(item.preco_arroba == null ? '—' : fmtR$2(item.preco_arroba))+'</td><td>'+esc(localizacao(item))+'</td><td><span class="badge '+classeStatus(item.status)+'">'+esc(rotuloStatus(item.status))+'</span></td><td class="wrap">'+esc(faltantes)+'</td></tr>';
  }).join('') || '<tr><td colspan="9" class="wrap">Nenhuma oferta em aberto.</td></tr>';
}

function proximoFollowup(negociacaoId){
  var itens = dados.followups.filter(function(item){ return item.negociacao_id === negociacaoId && item.status === 'pendente'; });
  itens.sort(function(a,b){ return String(a.previsto_para).localeCompare(String(b.previsto_para)); });
  return itens[0];
}

function renderNegociacoes(){
  el('listaNegociacoes').innerHTML = dados.negociacoes.map(function(item){
    var proximo = proximoFollowup(item.id);
    return '<tr><td>'+fmtDT(item.iniciada_em)+'</td><td>'+esc(nomeContato(item.contato_id))+'</td><td><span class="badge '+classeStatus(item.status)+'">'+esc(rotuloStatus(item.status))+'</span></td><td class="num">'+fmtN(item.quantidade_acordada)+'</td><td class="num">'+(item.preco_acordado == null ? '—' : fmtR$2(item.preco_acordado))+'</td><td class="wrap">'+(proximo ? fmtDT(proximo.previsto_para)+' · '+esc(proximo.descricao) : '—')+'</td></tr>';
  }).join('') || '<tr><td colspan="6" class="wrap">Nenhuma negociação registrada.</td></tr>';
}

function contatoDoNegocio(item){
  var tipos = item.tipos || [];
  return tipos.some(function(tipo){ return /fornecedor|corretor|frigorifico|confinamento/.test(tipo); });
}

function renderContatos(){
  var contatos = dados.contatos.filter(contatoDoNegocio);
  el('listaContatos').innerHTML = contatos.map(function(item){
    return '<tr><td>'+esc(item.nome)+'</td><td class="wrap">'+esc((item.tipos || []).join(', ') || 'contato')+'</td><td>'+esc([item.municipio,item.uf].filter(Boolean).join(' / ') || '—')+'</td></tr>';
  }).join('') || '<tr><td colspan="3" class="wrap">Nenhum contato de negócio classificado.</td></tr>';
}

function renderFollowups(){
  var pendentes = dados.followups.filter(function(item){ return item.status === 'pendente'; });
  pendentes.sort(function(a,b){ return String(a.previsto_para).localeCompare(String(b.previsto_para)); });
  el('listaFollowups').innerHTML = pendentes.map(function(item){
    return '<tr><td>'+fmtDT(item.previsto_para)+'</td><td>'+esc(nomeContato(item.contato_id))+'</td><td class="wrap">'+esc(item.descricao)+'</td></tr>';
  }).join('') || '<tr><td colspan="3" class="wrap">Nenhuma próxima ação pendente.</td></tr>';
}

function renderTudo(){
  porContato = {};
  dados.contatos.forEach(function(item){ porContato[item.id] = item; });
  el('fornecedor').innerHTML = '<option value="">Selecione</option>'+dados.contatos.map(function(item){ return '<option value="'+esc(item.id)+'">'+esc(item.nome)+'</option>'; }).join('');
  renderKpis(); renderOfertas(); renderNegociacoes(); renderContatos(); renderFollowups();
}

function erroDasRespostas(respostas){
  for(var i=0; i<respostas.length; i+=1){ if(respostas[i].error) return respostas[i].error; }
  return null;
}

async function carregar(){
  el('subtitle').textContent = 'Carregando ofertas, negociações e contatos…';
  var respostas = await Promise.all([
    db.from('ofertas_gado').select('*').order('recebida_em', {ascending:false}).limit(500),
    db.from('negociacoes_gado').select('*').order('iniciada_em', {ascending:false}).limit(500),
    db.from('contatos').select('id,nome,tipos,municipio,uf').order('nome').limit(1000),
    db.from('crm_followups').select('*').order('previsto_para').limit(500)
  ]);
  var erro = erroDasRespostas(respostas);
  if(erro){
    el('subtitle').textContent = 'CRM indisponível: '+erro.message;
    return;
  }
  dados = {ofertas:respostas[0].data || [], negociacoes:respostas[1].data || [], contatos:respostas[2].data || [], followups:respostas[3].data || []};
  renderTudo();
  el('subtitle').textContent = 'Ofertas, negociações e contatos em um único histórico.';
}

function numeroOuNulo(id){
  var valor = el(id).value;
  return valor === '' ? null : Number(valor);
}

async function salvarOferta(evento){
  evento.preventDefault();
  el('formMsg').textContent = '';
  el('formErr').textContent = '';
  var faltantes = [];
  if(el('preco').value === '') faltantes.push('preco_arroba');
  if(el('quantidade').value === '') faltantes.push('quantidade');
  if(el('sexo').value === 'nao_informado') faltantes.push('sexo');
  if(el('pesoMedio').value === '') faltantes.push('peso_medio_kg');
  if(!el('municipio').value.trim() || !el('uf').value.trim()) faltantes.push('localizacao');
  var payload = {
    fornecedor_id: el('fornecedor').value,
    sexo: el('sexo').value,
    categoria: el('categoria').value.trim() || null,
    quantidade: numeroOuNulo('quantidade'),
    peso_medio_kg: numeroOuNulo('pesoMedio'),
    preco_arroba: numeroOuNulo('preco'),
    modalidade_preco: 'arroba',
    municipio: el('municipio').value.trim() || null,
    uf: el('uf').value.trim().toUpperCase() || null,
    status: faltantes.length ? 'incompleta' : 'nova',
    origem_canal: 'manual',
    campos_faltantes: faltantes,
    observacoes: el('observacoes').value.trim() || null
  };
  el('salvarOferta').disabled = true;
  var resposta = await db.from('ofertas_gado').insert(payload).select().single();
  el('salvarOferta').disabled = false;
  if(resposta.error){ el('formErr').textContent = 'Não foi possível salvar: '+resposta.error.message; return; }
  el('formOferta').reset();
  el('formMsg').textContent = faltantes.length ? 'Oferta salva como incompleta para conferência.' : 'Oferta salva no CRM.';
  await carregar();
}

el('entrarBtn').addEventListener('click', function(){ entrar(); });
el('formOferta').addEventListener('submit', salvarOferta);
CFAgro.authInit(carregar);
})();
