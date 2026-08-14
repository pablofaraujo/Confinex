'use strict';

function recursoExternoNaoCritico(url) {
  if (!url) return false;
  try {
    const recurso = new URL(url);
    return recurso.hostname === 'fonts.gstatic.com' &&
      /\.(?:woff2?|ttf|otf)$/i.test(recurso.pathname);
  } catch {
    return false;
  }
}

module.exports = { recursoExternoNaoCritico };
