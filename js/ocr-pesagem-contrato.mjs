export const LIMITE_PAGINAS_OCR = 8;
export const LIMITE_BYTES_OCR = 20 * 1024 * 1024;
export const TIPOS_OCR = new Set(['image/jpeg', 'image/png', 'application/pdf']);

export function validarArquivoOCR({ tipo, tamanho }) {
  if (!TIPOS_OCR.has(tipo)) throw new Error('Formato não suportado; use JPG, PNG ou PDF');
  if (!Number.isInteger(tamanho) || tamanho < 1) throw new Error('Arquivo vazio ou inválido');
  if (tamanho > LIMITE_BYTES_OCR) throw new Error('Arquivo excede o limite seguro de 20 MB');
  return { tipo, tamanho, pdf: tipo === 'application/pdf' };
}

export function validarResultadoPDF(resultado, totalPaginas) {
  if (!Number.isInteger(totalPaginas) || totalPaginas < 1) throw new Error('PDF sem páginas válidas');
  const origem = resultado?.paginas_origem;
  if (!Array.isArray(origem) || !origem.length || origem.some(p => !Number.isInteger(p))) throw new Error('OCR de PDF não informou origem das páginas');
  if (new Set(origem).size !== origem.length) throw new Error('OCR de PDF repetiu página de origem');
  if (origem.some(p => p < 1 || p > totalPaginas)) throw new Error('OCR informou página fora do documento');
  if (resultado.paginas_processadas !== origem.length) throw new Error('quantidade de páginas divergente');
  if (resultado.paginas_processadas > Math.min(totalPaginas, LIMITE_PAGINAS_OCR)) throw new Error('OCR excedeu o limite seguro de páginas');
  const omitidas = resultado.paginas_omitidas ?? totalPaginas - origem.length;
  if (omitidas !== totalPaginas - origem.length) throw new Error('páginas omitidas divergentes');
  return true;
}
