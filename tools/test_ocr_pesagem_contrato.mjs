import assert from 'node:assert/strict';
import { LIMITE_BYTES_OCR, validarArquivoOCR, validarResultadoPDF } from '../js/ocr-pesagem-contrato.mjs';
const casos = [
  () => assert.equal(validarArquivoOCR({ tipo: 'image/jpeg', tamanho: 10 }).pdf, false),
  () => assert.equal(validarArquivoOCR({ tipo: 'image/png', tamanho: 10 }).pdf, false),
  () => assert.equal(validarArquivoOCR({ tipo: 'application/pdf', tamanho: 10 }).pdf, true),
  () => assert.throws(() => validarArquivoOCR({ tipo: 'text/plain', tamanho: 10 }), /Formato/),
  () => assert.throws(() => validarArquivoOCR({ tipo: 'application/pdf', tamanho: 0 }), /vazio/),
  () => assert.throws(() => validarArquivoOCR({ tipo: 'application/pdf', tamanho: LIMITE_BYTES_OCR + 1 }), /20 MB/),
  () => assert.equal(validarResultadoPDF({ paginas_origem: [1, 2], paginas_processadas: 2, paginas_omitidas: 0 }, 2), true),
  () => assert.throws(() => validarResultadoPDF({ paginas_processadas: 1 }, 1), /origem/),
  () => assert.throws(() => validarResultadoPDF({ paginas_origem: [1, 1], paginas_processadas: 2 }, 2), /repetiu/),
  () => assert.throws(() => validarResultadoPDF({ paginas_origem: [1, 9], paginas_processadas: 2 }, 2), /fora/),
  () => assert.throws(() => validarResultadoPDF({ paginas_origem: [1,2,3,4,5,6,7,8,9], paginas_processadas: 9 }, 9), /limite/),
  () => assert.throws(() => validarResultadoPDF({ paginas_origem: [1], paginas_processadas: 1, paginas_omitidas: 0 }, 2), /omitidas/),
];
casos.forEach(fn => fn());
console.log('OCR Pesagem: 12 verificações aprovadas');
