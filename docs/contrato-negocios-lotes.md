# Contrato de negócios, lotes e chaves — P1

Status: **contrato de diagnóstico e proposta incremental**. Não é uma migração,
uma nova tabela de negócios, nem declaração de que o histórico já foi saneado.
O sistema publicado continua com os mesmos fluxos e dados.

## Cinco processos, sem refatoração geral

| Etapa | Responsabilidade | Entrega antes de mudar o sistema |
|---|---|---|
| P1 — Negócios e lotes | Identidade econômica, subdivisões e origem | Catálogo de chaves e relações; contrato deste documento |
| P2 — Compras e documentos | Compra, NF, GTA e complementos | Regras de associação, sem considerar toda NF um novo negócio |
| P3 — Confinamento | Entrada, pesagem, permanência e acerto | Separação entre previsão e realizado, com unidades e datas |
| P4 — Venda e abate | Saída, carcaça, descontos e recebimento | Relação entre evento físico, negócio e documento |
| P5 — Financeiro transversal | Pagamentos, recebimentos e conciliação | Cobertura financeira sem duplicar parcelas ou totais |

Cada etapa deve ter adaptador de leitura, contrato explícito, testes sintéticos,
amostra privada rastreável e plano reversível antes de qualquer alteração de
dados. Nenhum módulo novo deve assumir a responsabilidade de todos os demais.

## O que significa cada chave

| Conceito | Finalidade | Não confundir com |
|---|---|---|
| Identificador técnico | Referenciar um registro estável | Código apresentado ao usuário |
| Referência humana | Encontrar/comunicar um negócio no padrão `NEG-26-001` aprovado | Prova automática de unicidade ou PK já existente |
| Chave composta | Identificar algo dentro de um escopo com vários campos | Concatenação de nomes sem restrição comprovada |
| Vínculo pai/filho | Associar componente, versão ou documento ao registro correto | Semelhança textual ou posição na planilha |
| Chave idempotente | Evitar repetir o mesmo comando/importação | Identidade de todos os negócios parecidos |
| Rastreio de fonte | Encontrar arquivo, aba/linha, mensagem e versão | Aprovação ou confirmação da informação |

“Subchave” será descrita concretamente: **FK**, chave composta ou referência de
componente dentro de um negócio. Não haverá contador único somando conceitos
diferentes. A regra de atribuição do ano e numeração dos subgrupos precisa ser
explícita antes de gerar ou renomear códigos existentes.

## Estruturas a reutilizar

O mapeamento vem do código e migrações versionadas. Presença na API e restrições
efetivas no banco são evidências distintas, registradas em relatório privado.
Não se presume que um `CREATE TABLE` no repositório foi aplicado.

| Estrutura | Papel | Limite importante |
|---|---|---|
| `operacoes` | Referência operacional usada por compras/vendas/acompanhamento | Verificar unicidade e escopo de `codigo` |
| `compras`, `vendas` | Fatos econômicos relacionados à operação | Não gerar de candidato/documento sem confirmação |
| `entradas_confinamento`, `abates` | Eventos físicos e vínculos | Lote/curral textual não prova identidade única |
| `negocios_fazenda` | Negócio econômico da fazenda | Não duplicar seu valor somando o movimento físico |
| `fazenda_ametista` | Histórico físico de entradas/saídas | Não substitui o negócio econômico |
| `movimentacoes_interunidades` | Associação dos eventos de origem e destino | Transferência não é duas compras independentes |
| `compras_componentes` | Detalhamento de compra agregada | Somar agregado **ou** componentes, nunca ambos |
| `operacao_participantes` | Participantes/papéis por operação | Participação não cria outro total de animais |
| `confinex_avaliacoes`, `confinex_estimativas` | Estudos e versões de previsão | Não são realizados ou compra confirmada |
| `negocios_candidatos`, `negocio_versoes` | Hipóteses e versões em conferência | Versão não é negócio adicional por padrão |
| `fontes_importacao`, `evidencias_negocio`, `vinculos_documentais_candidatos` | Origem e vínculos propostos | Candidato não confirma GTA, NF ou pagamento |
| `operation_drafts`, `pending_actions`, `eventos` | Revisão, decisão e auditoria | Rascunho não integra estoque/resultado definitivo |

Não se cria tabela genérica concorrente de negócios/lotes nesta fase. Uma
lacuna será demonstrada pelo catálogo real e por um caso de uso antes de propor
estrutura nova.

## Subdivisão de um negócio

Sexo, categoria e destino são dimensões separadas. Uma negociação pode conter
novilhas para confinamento, vacas para abate e garrotes repartidos entre fazenda
e confinamento. Cada grupo deve permanecer distinguível, ligado ao negócio de
origem e à evidência que comprova a divisão.

- Não consolidar grupos com sexo, categoria ou destino diferentes só porque
  vendedor, data ou referência principal coincidem.
- Não criar subgrupo por documento: várias NF/GTA podem complementar o mesmo
  grupo; uma NF também pode descrever vários componentes.
- Versão idêntica pode ser apontada como repetição de evidência, mantendo todas
  as fontes. Não apagar/fundir registros automaticamente.
- Quantidade adicional comprovada é complemento do negócio, não mera versão.
  Fonte, quantidades e decisão precisam distinguir os casos.
- Sem correspondência única, preservar ambiguidade e mostrar alternativas.
- Datas de negócio, pesagem, emissão, movimentação e registro não são sinônimos.
- Peso vivo, carcaça, arrobas, quantidade e rateio exigem unidade/base de cálculo.

## Caminho de informação e fronteiras

1. Receber fonte autorizada, preservando identificação, hash, versão e data.
2. Extrair campos para conferência, sem escrever fato operacional.
3. Procurar identidades e vínculos explícitos nas fontes disponíveis.
4. Apresentar hipótese de negócio/complemento/subgrupo, motivos e divergências.
5. Revisar e confirmar no fluxo existente; promoção exige decisão separada.
6. Registrar resultado/origem em evento; correção posterior preserva histórico.

Esse é o contrato futuro, não garantia de cobertura de todos os adaptadores.
O inventário atual executa apenas leituras de metadados/arquivos solicitadas.
Memória permanente guarda regras/preferências/aprendizados reutilizáveis;
handoff guarda continuidade temporária. Nenhum substitui fatos operacionais.

## Gate de normalização

- [x] Motor offline de fontes, chaves candidatas e relações exatas.
- [x] Consulta estrutural versionada em transação somente leitura.
- [x] Leitor com modo parcial OpenAPI explicitamente identificado.
- [x] Contrato P1 separando negócios, versões, componentes e movimentos.
- [x] Capturar PK/FK/UNIQUE/índices do banco real com acesso SQL de leitura
  (duas observações conferidas em 04/09/2026; evidências privadas).
- [ ] Comparar códigos e relações com fontes históricas autorizadas.
- [ ] Quantificar colisões, órfãos, incompletos e totais por subgrupo.
- [ ] Produzir plano privado por registro, com ambiguidades preservadas.
- [ ] Revisar consumidores antes de propor migração aditiva.
- [ ] Obter autorização específica para escrita real e sua reversão.

Ter um script não comprova que a coleta foi executada. Resultados/limitações
ficam nos relatórios privados. Ver [catálogo do esquema](catalogo-esquema-chaves.md)
e [perfilamento das fontes](catalogo-chaves-fontes.md).
O cruzamento incremental de registros segue o
[diagnóstico privado P1](diagnostico-vinculos-negocios.md), sem autorizar
normalização ou correção automática dos dados.
Em 04/09/2026, foi concluído um primeiro recorte privado das abas Compras e
Consolidado contra oito projeções estáveis do banco, com roteiro por ocorrência.
Os três gates de fontes/quantificação/plano acima continuam abertos para a
cobertura integral: o recorte não comprova todos os negócios nem todas as fontes.
