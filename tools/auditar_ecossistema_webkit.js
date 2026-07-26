#!/usr/bin/env node
/* Auditoria móvel real no WebKit. Não autentica nem escreve no produto. */
'use strict';

const fs = require('node:fs');
const { webkit } = require('playwright');

function argumento(nome) {
  const indice = process.argv.indexOf(nome);
  if (indice < 0 || !process.argv[indice + 1]) throw new Error(`faltou ${nome}`);
  return process.argv[indice + 1];
}

const baseUrl = new URL(argumento('--base-url'));
const saida = argumento('--saida');
const iphone = {
  viewport: { width: 390, height: 844 },
  userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 14_8 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1 Mobile/15E148 Safari/604.1',
  isMobile: true,
  hasTouch: true,
};

function item(id, requisito, cenario, esperado, ok, evidencia) {
  return {
    id,
    requisito,
    cenario,
    esperado,
    status: ok ? 'aprovado' : 'falhou',
    evidencia,
    camada: 'navegador',
  };
}

async function abrir(context, { atrasarPrimeiroPacote = false } = {}) {
  const page = await context.newPage();
  const erros = [];
  let pacotes = 0;
  page.on('console', mensagem => {
    if (mensagem.type() === 'error') erros.push(`console: ${mensagem.text()}`);
  });
  page.on('pageerror', erro => erros.push(`javascript: ${erro.message}`));
  page.on('response', resposta => {
    if (resposta.status() >= 400) erros.push(`http ${resposta.status()}: ${resposta.url()}`);
  });
  await page.route('**/confinex-app.mobile.js*', async rota => {
    pacotes += 1;
    if (atrasarPrimeiroPacote && pacotes === 1) {
      await new Promise(resolve => setTimeout(resolve, 350));
      await rota.abort('timedout');
      return;
    }
    await rota.continue();
  });
  await page.route('https://cdn.jsdelivr.net/**', rota => rota.abort('blockedbyclient'));
  await page.goto(new URL('confinex.html?validacao=webkit', baseUrl).href, {
    waitUntil: 'commit',
    timeout: 30000,
  });
  await page.locator('#root .app').waitFor({ state: 'visible', timeout: 30000 });
  const estado = await page.evaluate(() => ({
    build: window.__CONFINEX_BUILD,
    bootOk: window.__CONFINEX_BOOT_OK === true,
    appIniciado: window.__CONFINEX_APP_INICIADO === true,
    tentativas: window.__CONFINEX_CARGA_TENTATIVAS,
    aplicativos: document.querySelectorAll('#root .app').length,
    falhaFinal: document.getElementById('boot-status')?.textContent?.includes('Não foi possível abrir') || false,
    larguraDocumento: document.documentElement.scrollWidth,
  }));
  await context.close();
  return { estado, erros, pacotes };
}

(async () => {
  const navegador = await webkit.launch({
    headless: true,
    executablePath: process.env.PLAYWRIGHT_WEBKIT_EXECUTABLE_PATH || undefined,
  });
  const resultados = [];
  try {
    const normal = await abrir(await navegador.newContext(iphone));
    const normalOk = normal.estado.bootOk && normal.estado.appIniciado &&
      normal.estado.aplicativos === 1 && normal.estado.larguraDocumento <= 390 &&
      !normal.estado.falhaFinal && normal.erros.length === 0;
    resultados.push(item(
      'browser:webkit:iphone:confinex:abertura',
      'Abertura real no WebKit do iPhone',
      'pacote disponível na primeira tentativa',
      'a interface abre uma vez, sem erro ou estouro horizontal',
      normalOk,
      `estado=${JSON.stringify(normal.estado)} pacotes=${normal.pacotes} erros=${normal.erros.join(' | ') || 'nenhum'}`,
    ));

    const contextoRecuperacao = await navegador.newContext(iphone);
    await contextoRecuperacao.addInitScript(() => {
      window.__CONFINEX_TEMPO_RECARGA_MS = 100;
      window.__CONFINEX_TEMPO_FALHA_MS = 2000;
    });
    const recuperacao = await abrir(contextoRecuperacao, { atrasarPrimeiroPacote: true });
    const recuperacaoOk = recuperacao.estado.bootOk && recuperacao.estado.appIniciado &&
      recuperacao.estado.aplicativos === 1 && recuperacao.estado.tentativas === 2 &&
      recuperacao.pacotes === 2 && !recuperacao.estado.falhaFinal &&
      recuperacao.erros.length === 0;
    resultados.push(item(
      'browser:webkit:iphone:confinex:recuperacao',
      'Recuperação de conexão lenta no iPhone',
      'o primeiro pacote demora além do limite e falha',
      'uma nova cópia abre automaticamente, sem montagem duplicada',
      recuperacaoOk,
      `estado=${JSON.stringify(recuperacao.estado)} pacotes=${recuperacao.pacotes} erros=${recuperacao.erros.join(' | ') || 'nenhum'}`,
    ));
  } finally {
    await navegador.close();
  }
  fs.writeFileSync(saida, JSON.stringify({ resultados }, null, 2) + '\n');
})().catch(erro => {
  console.error(erro);
  process.exitCode = 1;
});
