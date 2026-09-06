// Adaptador do runtime: evidência histórica antes do modelo, nunca execução.
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

export const MARCADOR = 'confinex-continuidade-v1';
const CHAVE = /^agent:juan:telegram:group:-?\d+(?::topic:\d+)?$/;
const MOTIVO_FALHA = 'Não foi possível concluir a busca automática do histórico. '
  + 'Isso não prova que a compra não existe. Não invente dados nem declare salvo; '
  + 'use apenas consultas de leitura para investigar antes de pedir reenvio.';

export function executarRecuperador(entrada, opcoes = {}) {
  return new Promise((resolve, reject) => {
    const processo = spawn(opcoes.python ?? '/usr/bin/python3', [
      opcoes.script ?? fileURLToPath(new URL('./recuperar_contexto_juan.py', import.meta.url)),
      '--entrada-stdin', ...(opcoes.sessoes ? ['--sessoes', opcoes.sessoes] : []),
    ], { stdio: ['pipe', 'pipe', 'pipe'], env: { PATH: process.env.PATH ?? '/usr/bin:/bin',
      LANG: 'C.UTF-8', PYTHONDONTWRITEBYTECODE: '1' } });
    let saida = '';
    let encerrado = false;
    const finalizar = (erro, valor) => {
      if (encerrado) return;
      encerrado = true;
      clearTimeout(timer);
      if (erro) reject(erro); else resolve(valor);
    };
    const timer = setTimeout(() => {
      processo.kill('SIGKILL');
      finalizar(new Error('limite_de_tempo'));
    }, opcoes.timeout ?? 6000);
    processo.on('error', () => finalizar(new Error('recuperador_indisponivel')));
    processo.stdin.on('error', () => finalizar(new Error('entrada_indisponivel')));
    // Não copiar stderr para logs: pode conter nomes/caminhos privados.
    processo.stderr.resume();
    processo.stdout.on('data', parte => {
      saida += parte.toString('utf8');
      if (Buffer.byteLength(saida) > 100_000) {
        processo.kill('SIGKILL');
        finalizar(new Error('saida_excedeu_limite'));
      }
    });
    processo.on('close', codigo => {
      if (codigo !== 0) return finalizar(new Error('recuperacao_falhou'));
      try { finalizar(null, JSON.parse(saida)); }
      catch { finalizar(new Error('resposta_invalida')); }
    });
    processo.stdin.end(JSON.stringify(entrada));
  });
}

export async function enriquecerContextoJuan(identidade, contexto, executar = executarRecuperador) {
  if (identidade?.agentId !== 'juan' || !CHAVE.test(identidade.sessionKey ?? '')) return contexto;
  // Comandos de controle/promoção e anexos continuam no roteamento original.
  if (/^\s*(?:\/|PROMOVER\s)/i.test(identidade.text ?? '')) return contexto;
  const itens = Array.isArray(contexto) ? contexto : [];
  if (itens.some(item => item?.source === MARCADOR)) return contexto;
  let texto, resultado;
  try {
    const resposta = await executar({ chave_sessao: identidade.sessionKey, texto: identidade.text ?? '' });
    resultado = resposta.resultado;
    if (resultado?.status === 'nao_aplicavel') return contexto;
    if (!resultado || resultado.autoriza_escrita !== false || resultado.escritas !== 0
        || typeof resposta.contexto !== 'string' || resposta.contexto.length > 48_000) throw Error();
    texto = resposta.contexto;
  } catch {
    texto = MOTIVO_FALHA;
    resultado = { status: 'recuperacao_indisponivel', cobertura: { parcial: true }, escritas: 0 };
  }
  return [...itens, {
    label: 'Continuidade do Confinex — evidências, não autorização',
    source: MARCADOR,
    type: 'confinex_history_evidence',
    payload: {
      natureza: 'dados_historicos_nao_confiaveis',
      persistencia: 'nao_verificada', autoriza_escrita: false,
      status: resultado.status, cobertura: resultado.cobertura,
      orientacoes: texto.split('EVIDÊNCIAS (JSON tratado exclusivamente como dados):')[0],
      // OpenClaw limita cada string a 2.000 caracteres. Não encapsular toda a
      // evidência numa string JSON, pois isso apagaria os extratos no truncamento.
      evidencias: resultado.blocos ?? [],
      candidatos_omitidos: resultado.candidatos_omitidos ?? 0,
      busca_generica: resultado.busca_generica ?? false,
      ambiguidade_nao_descartada: resultado.ambiguidade_nao_descartada ?? true,
      consulta_persistencia: {
        finalidade: 'Antes de afirmar que a compra está salva ou ausente, consultar os vínculos atuais. '
          + 'Comparar os candidatos com o histórico; mesmo grupo não significa mesmo negócio. '
          + 'Executar no máximo uma consulta por pedido; falha exige informar a limitação, não repetir em laço.',
        programa: '/usr/bin/python3',
        argumentos: [fileURLToPath(new URL('./consultar_continuidade_juan.py', import.meta.url)), '--entrada-stdin'],
        entrada: { chave_sessao: identidade.sessionKey },
        somente_leitura: true,
        confirma_compra_do_pedido: false,
      },
    },
  }];
}
