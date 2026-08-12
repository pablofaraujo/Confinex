const TABELA_BASES = 'confinex_bases';

function texto(valor) {
  return String(valor ?? '').trim();
}

function copiar(valor) {
  return JSON.parse(JSON.stringify(valor));
}

export function normalizarBaseConfinamento(base, agora = '1970-01-01T00:00:00.000Z') {
  if (!base || typeof base !== 'object' || Array.isArray(base)) {
    throw new Error('Base de confinamento inválida.');
  }
  const chave = texto(base.id || base.chave);
  const nome = texto(base.nome);
  if (!chave) throw new Error('A base precisa de uma identificação.');
  if (!nome) throw new Error('Informe o nome da base do confinamento.');
  const atualizadoEm = texto(base.atualizadoEm || base.atualizado_em || agora);
  const dados = copiar({ ...base, id: chave, nome, atualizadoEm });
  return { chave, nome, atualizadoEm, dados };
}

export function baseDaLinhaOnline(linha) {
  const dados = linha?.dados && typeof linha.dados === 'object' ? linha.dados : {};
  return normalizarBaseConfinamento({
    ...dados,
    id: linha?.chave || dados.id,
    nome: linha?.nome || dados.nome,
    atualizadoEm: linha?.atualizado_em || dados.atualizadoEm
  });
}

export function mesclarBasesConfinamento(locais = [], online = []) {
  const porChave = new Map();
  [...locais, ...online].forEach((entrada) => {
    let base;
    try {
      base = normalizarBaseConfinamento(entrada).dados;
    } catch {
      return;
    }
    const anterior = porChave.get(String(base.id));
    if (!anterior || String(base.atualizadoEm || '') >= String(anterior.atualizadoEm || '')) {
      porChave.set(String(base.id), base);
    }
  });
  return [...porChave.values()].sort((a, b) => a.nome.localeCompare(b.nome, 'pt-BR'));
}

async function sessaoObrigatoria(supabase) {
  if (!supabase?.auth?.getSession) throw new Error('Conexão online indisponível.');
  const { data, error } = await supabase.auth.getSession();
  if (error) throw new Error(error.message || 'Não foi possível conferir o acesso.');
  if (!data?.session?.user?.id) throw new Error('Entre no ecossistema para usar as bases online.');
  return data.session;
}

export async function listarBasesOnline({ supabase }) {
  await sessaoObrigatoria(supabase);
  const { data, error } = await supabase
    .from(TABELA_BASES)
    .select('chave,nome,dados,atualizado_em')
    .order('nome', { ascending: true });
  if (error) throw new Error(error.message || 'Não foi possível carregar as bases online.');
  return (data || []).map((linha) => baseDaLinhaOnline(linha).dados);
}

export async function salvarBaseOnline({ supabase, base }) {
  await sessaoObrigatoria(supabase);
  const preparada = normalizarBaseConfinamento(base);
  const { data, error } = await supabase.rpc('salvar_base_confinex', {
    p_chave: preparada.chave,
    p_nome: preparada.nome,
    p_dados: preparada.dados,
    p_atualizado_em: preparada.atualizadoEm
  });
  if (error) throw new Error(error.message || 'Não foi possível salvar a base online.');
  const linha = Array.isArray(data) ? data[0] : data;
  return linha ? baseDaLinhaOnline(linha).dados : preparada.dados;
}

export async function apagarBaseOnline({ supabase, chave }) {
  await sessaoObrigatoria(supabase);
  const id = texto(chave);
  if (!id) throw new Error('Selecione uma base para apagar.');
  const { error } = await supabase.from(TABELA_BASES).delete().eq('chave', id);
  if (error) throw new Error(error.message || 'Não foi possível apagar a base online.');
  return { chave: id, apagada: true };
}
