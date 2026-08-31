'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const raiz = path.resolve(__dirname, '..');
const pendenciasHtml = fs.readFileSync(path.join(raiz, 'pendencias.html'), 'utf8');
const pendenciasJs = fs.readFileSync(path.join(raiz, 'js/pendencias.js'), 'utf8');
const eventosHtml = fs.readFileSync(path.join(raiz, 'eventos.html'), 'utf8');
const eventosJs = fs.readFileSync(path.join(raiz, 'js/eventos.js'), 'utf8');
const confinadosHtml = fs.readFileSync(path.join(raiz, 'confinados.html'), 'utf8');
const confinamentoHtml = fs.readFileSync(path.join(raiz, 'confinamento.html'), 'utf8');
const bbHtml = fs.readFileSync(path.join(raiz, 'bb.html'), 'utf8');
const auditoriaBrowser = fs.readFileSync(path.join(raiz, 'tools/auditar_ecossistema_browser.js'), 'utf8');

for(const id of ['listaPendencias','filtroOrigem','filtroTexto','erroFontes']){
  assert.ok(pendenciasHtml.includes(`id="${id}"`), `Pendências sem #${id}`);
}
for(const [tabela, projection] of [
  ['operation_drafts','DRAFT_PENDENCIAS_COLUNAS'],
  ['pending_actions','ACAO_PENDENCIAS_COLUNAS'],
]){
  assert.ok(pendenciasJs.includes(`db.from('${tabela}').select(${projection})`), `projeção ausente: ${tabela}`);
}
assert.ok(pendenciasJs.includes("db.from('pendencias_documentos').select('*')"), 'consulta ausente: pendencias_documentos');
for(const coluna of ['investigacao_origem_id','promocao_origem_id','promocao_lease_token','promocao_fencing_token']){
  assert.ok(!pendenciasJs.match(new RegExp(`(?:DRAFT|ACAO)_PENDENCIAS_COLUNAS[^\\n]*${coluna}`)), `pendências não pode projetar ${coluna}`);
}
for(const tabela of ['operacoes','confinex_avaliacoes']){
  assert.ok(pendenciasJs.includes(`db.from('${tabela}').select(`), `consulta ausente: ${tabela}`);
}
assert.ok(pendenciasHtml.includes('Próxima etapa'));
assert.ok(pendenciasHtml.includes('<option>Planejamento</option>'));
assert.ok(pendenciasJs.includes('planejamentosRentabilidadePendentes'));
assert.ok(confinamentoHtml.includes('id="tbPlanejamento"'));
assert.ok(confinamentoHtml.includes('Planejamento de rentabilidade pendente'));
assert.ok(confinamentoHtml.includes('planejamentosRentabilidadePendentes(ops.data, avaliacoes.data||[])'));
assert.ok(pendenciasJs.includes('Os demais itens continuam disponíveis.'));
assert.ok(pendenciasJs.includes("href=\"'+esc(item.destino.href)"));
assert.ok(!/\.(insert|update|delete|upsert|rpc)\s*\(/.test(pendenciasJs));

for(const id of ['listaEventos','filtroSituacao','filtroTipo','filtroPeriodo','filtroTexto']){
  assert.ok(eventosHtml.includes(`id="${id}"`), `Eventos sem #${id}`);
}
assert.ok(eventosJs.includes("db.from('eventos').select('*')"));
assert.ok(eventosHtml.includes('Todo o histórico'));
assert.ok(eventosHtml.includes('Origem'));
assert.ok(eventosJs.includes("href=\"'+esc(item.origem.href)"));
assert.ok(!/\.(insert|update|delete|upsert|rpc)\s*\(/.test(eventosJs));

assert.ok(pendenciasHtml.includes('cfagro-gestao.js?v=20260831-1'));
assert.ok(eventosHtml.includes('cfagro-gestao.js?v=20260831-1'));
assert.ok(confinamentoHtml.includes('cfagro-gestao.js?v=20260831-1'));
assert.ok(pendenciasHtml.includes('pendencias.js?v=20260831-1'));
assert.ok(eventosHtml.includes('eventos.js?v=20260803-1'));
assert.ok(pendenciasJs.includes("new Set(['realizado','rejeitado','cancelado'])"));
assert.ok(pendenciasJs.includes("new Set(['executado','rejeitado','cancelado','expirado'])"));
assert.ok(pendenciasJs.includes("new Set(['aguardando_vendedor','revisao_necessaria'])"));
for(const estadoFechado of ['recebido','dispensado']){
  const registros = [
    {status: estadoFechado, tipo: 'nf_entrada'},
    {status: 'aguardando_vendedor', tipo: 'gta'}
  ];
  const ativos = registros.filter(item => new Set(['aguardando_vendedor','revisao_necessaria']).has(String(item.status || '').toLowerCase()));
  assert.deepStrictEqual(ativos.map(item => item.tipo), ['gta'], `documento ${estadoFechado} não pode aparecer como pendência`);
}
for(const [pagina, html] of [['Confinados', confinadosHtml], ['Boi Balança', bbHtml]]){
  assert.ok(
    html.includes(".in('status',['aguardando_vendedor','revisao_necessaria'])"),
    `${pagina} deve consultar documentos ausentes ou em revisão`
  );
  assert.ok(
    html.includes("String(p.tipo||'').toLowerCase()==='gta'"),
    `${pagina} deve reconhecer GTA sem depender de maiúsculas/minúsculas`
  );
  assert.ok(
    html.includes('Negócio encerrado · documento pendente'),
    `${pagina} deve manter o alerta documental após o encerramento do negócio`
  );
  assert.ok(
    html.includes('Revisar vínculo documental'),
    `${pagina} deve distinguir revisão documental de documento ausente`
  );
}
assert.ok(auditoriaBrowser.includes('linhasRestauradas === 6'));
assert.ok(auditoriaBrowser.includes('estado.linhas === 5'));

// --- Correção 1 (pendências): `executavel` em pending_actions ainda não
// existe em produção (migração 202608290001 não aplicada). Uma segunda
// tentativa sem a coluna evita perder a fonte inteira de Ações.
assert.ok(pendenciasJs.includes("ACAO_PENDENCIAS_COLUNAS_SEM_EXECUTAVEL = ACAO_PENDENCIAS_COLUNAS.replace(',executavel','')"), 'projeção reduzida ausente');
assert.ok(pendenciasJs.includes("error.code === '42703'"), 'deve reconhecer o código PostgREST de coluna ausente');
assert.ok(pendenciasJs.includes('does not exist'), 'deve reconhecer a mensagem de coluna ausente');
assert.ok(pendenciasJs.includes('async function selectAcoesPendencias()'), 'helper de fallback ausente');
assert.ok(pendenciasJs.includes('selectAcoesPendencias(),'), 'carregar() deve usar o helper com fallback em vez do select direto');

(async () => {
  try {
    function makeEl(){ return {textContent:'', innerHTML:'', value:'', addEventListener(){}}; }
    const elMocks = {
      filtroOrigem: Object.assign(makeEl(), {value:'todas'}), filtroTexto: makeEl(), entrarBtn: makeEl(),
      subtitle: makeEl(), erroFontes: makeEl(), listaPendencias: makeEl(), kpis: makeEl(),
    };
    let carregarCapturado = null;
    const pendContext = {
      CFAgro: {
        esc(v){ return String(v == null ? '' : v); },
        fmtDT(v){ return String(v || ''); },
        authInit(fn){ carregarCapturado = fn; },
      },
      CFAgroGestao: {
        pendenciasLegiveis(revisoes, acoes){
          return (acoes || []).map(a => ({resumo:a.resumo || '', contexto:'', status:a.status, origem:'Ações', data:a.criado_em, destino:{href:'#'}, acao:'Abrir'}));
        },
        erroLegivel(err){ return 'Erro: ' + ((err && err.message) || 'desconhecido'); },
        planejamentosRentabilidadePendentes(){ return []; },
      },
      document: { getElementById(id){ return elMocks[id] || makeEl(); } },
      console,
    };
    vm.createContext(pendContext);
    new vm.Script(pendenciasJs, {filename:'js/pendencias.js (simulação)'}).runInContext(pendContext);
    assert.ok(typeof carregarCapturado === 'function', 'CFAgro.authInit deve receber a função carregar');

    const chamadasAcoes = [];
    pendContext.db = {
      from(table){
        if(table === 'operation_drafts') return {select(){ return {limit(){ return Promise.resolve({data:[], error:null}); }}; }};
        if(table === 'pending_actions') return {select(cols){
          chamadasAcoes.push(cols);
          return {limit(){
            if(cols.indexOf('executavel') !== -1) return Promise.resolve({data:null, error:{code:'42703', message:'column pending_actions.executavel does not exist'}});
            return Promise.resolve({data:[{id:'a1', status:'em_revisao', resumo:'Conferir compra', criado_em:'2026-08-20'}], error:null});
          }};
        }};
        if(table === 'pendencias_documentos') return {select(){ return {in(){ return {limit(){ return Promise.resolve({data:[], error:null}); }}; }}; }};
        if(table === 'operacoes') return {select(){ return {limit(){ return Promise.resolve({data:[], error:null}); }}; }};
        if(table === 'confinex_avaliacoes') return {select(){ return {limit(){ return Promise.resolve({data:[], error:null}); }}; }};
        throw new Error('tabela inesperada: ' + table);
      },
    };

    await carregarCapturado();
    assert.equal(chamadasAcoes.length, 2, 'deve repetir a consulta de pending_actions exatamente uma vez, com a projeção reduzida');
    assert.ok(chamadasAcoes[0].indexOf('executavel') !== -1, 'primeira tentativa usa a projeção completa');
    assert.equal(chamadasAcoes[1].indexOf('executavel'), -1, 'segunda tentativa não deve pedir a coluna ausente');
    assert.equal(elMocks.erroFontes.textContent, '', 'a página deve funcionar sem marcar a fonte de ações como falha após o fallback');
    assert.equal(elMocks.subtitle.textContent, 'Itens que exigem conferência ou próxima ação');
    assert.match(elMocks.listaPendencias.innerHTML, /Conferir compra/, 'itens de pending_actions devem aparecer mesmo sem a coluna executavel');

    // Erro que não é de coluna ausente não deve repetir a consulta nem esconder a falha.
    const chamadasSemFallback = [];
    pendContext.db.from = (table) => {
      if(table === 'operation_drafts') return {select(){ return {limit(){ return Promise.resolve({data:[], error:null}); }}; }};
      if(table === 'pending_actions') return {select(cols){ chamadasSemFallback.push(cols); return {limit(){ return Promise.resolve({data:null, error:{message:'permission denied for table pending_actions'}}); }}; }};
      if(table === 'pendencias_documentos') return {select(){ return {in(){ return {limit(){ return Promise.resolve({data:[], error:null}); }}; }}; }};
      if(table === 'operacoes') return {select(){ return {limit(){ return Promise.resolve({data:[], error:null}); }}; }};
      if(table === 'confinex_avaliacoes') return {select(){ return {limit(){ return Promise.resolve({data:[], error:null}); }}; }};
      throw new Error('tabela inesperada: ' + table);
    };
    await carregarCapturado();
    assert.equal(chamadasSemFallback.length, 1, 'erro que não é de coluna ausente não deve gerar nova tentativa');
    assert.match(elMocks.erroFontes.textContent, /1 fonte\(s\) não puderam ser carregadas/, 'falha real de uma fonte continua sinalizada, sem quebrar a página');

    console.log('Pendências e Eventos: 48 verificações estáticas + simulação de fallback de projeção aprovadas.');
  } catch (error) {
    console.error(error);
    process.exitCode = 1;
  }
})();
