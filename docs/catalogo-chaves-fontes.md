# Catálogo local de chaves e relações entre fontes

`tools/catalogo_chaves.py` é um motor puro, sem leitura de arquivos, rede ou
Supabase. `tools/perfilar_chaves_fontes.py` é o invólucro somente leitura que
lê intervalos configurados de XLSX, CSV/TSV e JSON, entrega os registros ao
motor e grava um relatório privado. O objetivo é diagnosticar qualidade de
identificadores antes de uma eventual normalização; não é migrar tabelas nem
criar chaves no banco.

## Execução offline

O manifesto é um JSON local. O diretório da saída precisa ser novo e ficar em
`docs/privado/` ou em um diretório temporário:

```sh
python3 tools/perfilar_chaves_fontes.py \
  --manifesto docs/privado/catalogo-exemplo/manifesto.json \
  --saida docs/privado/catalogo-exemplo/resultado-2026-09-04
```

Não existe opção `--executar`. A execução lê cada fonte, calcula uma assinatura
SHA-256 antes e depois, verifica que o manifesto e as fontes não mudaram e
cria somente `catalogo.json` e `catalogo.md` no diretório novo. O resumo no
terminal informa `acessos_rede: 0`, `escritas_operacionais: 0` e que as fontes
foram preservadas. Em caso de erro, a mensagem não ecoa caminhos, células ou
valores privados.

O relatório inclui a assinatura do manifesto em representação JSON canônica;
mudanças de fonte, aba, intervalo ou escopo declarado mudam a identidade do
plano, mesmo quando as contagens coincidem. Formatação/espaços do JSON não
alteram essa identidade.

Os intervalos devem ser explícitos quando a fonte tiver mais de uma tabela ou
quando for necessário limitar o diagnóstico. O leitor XLSX não executa
fórmulas: usa somente o valor armazenado (cache) e informa fórmulas sem cache e
células com erro. Datas XLSX não são reinterpretadas. O leitor CSV/TSV mantém
as células como texto; JSON preserva seus tipos JSON (incluindo número,
booleano e nulo).

## Manifesto sintético

O exemplo abaixo é deliberadamente fictício. Os arquivos são apenas exemplos
locais e não devem conter dados reais no repositório público:

```json
{
  "versao": 1,
  "fontes": [
    {
      "id": "negocios",
      "arquivo": "negocios.json",
      "tabela": "negocios",
      "chaves": [
        {"nome": "codigo_negocio", "campos": ["codigo"]},
        {"nome": "lote_por_negocio", "campos": ["codigo", "lote"]}
      ]
    },
    {
      "id": "documentos",
      "arquivo": "documentos.csv",
      "separador": ";",
      "linha_cabecalho": 1,
      "chaves": [
        {"nome": "documento_fiscal", "campos": ["numero", "serie"]}
      ]
    },
    {
      "id": "lotes",
      "arquivo": "lotes.xlsx",
      "aba": "Itens",
      "linha_cabecalho": 2,
      "linha_final": 6,
      "linhas_ignorar": [6],
      "chaves": [
        {"nome": "lote", "campos": ["A", "B"]}
      ]
    }
  ],
  "relacoes": [
    {
      "origem": "lotes",
      "destino": "negocios",
      "campos_origem": ["A"],
      "campos_destino": ["codigo"]
    }
  ]
}
```

Cada fonte tem `id` local, `arquivo` relativo ao manifesto e, para JSON com
várias tabelas, `tabela`. JSON também pode ser diretamente uma lista de
objetos. CSV e TSV aceitam separador `,`, `;` ou tabulação e número da linha de
cabeçalho. XLSX exige `aba` e `linha_cabecalho`; pode limitar `linha_final` e
`linhas_ignorar`. Em XLSX, campos são **letras de coluna** (`A`, `B`, `AA`),
não rótulos: dois cabeçalhos iguais não sobrescrevem as células. O exemplo
supõe o código de negócio na coluna A e a subchave do lote na B.
O nome de cada fonte e os campos de relações são explícitos:
uma coluna com nome parecido não cria vínculo.

O exemplo `numero + serie` mede apenas essa hipótese fiscal, não recomenda
uma chave: a identidade de NF-e exige a chave de acesso ou a combinação
completa adequada, incluindo emitente/modelo/série/número. Uma GTA também
precisa do escopo emissor apropriado; seu número isolado não prova unicidade.

Uma relação compara a lista de campos na ordem declarada em cada lado. Os dois
lados precisam ter o mesmo número de campos. Em tabela não vazia, uma coluna
referenciada pela chave ou relação precisa existir em pelo menos um registro;
uma tabela vazia não prova unicidade nem existência operacional.

## O que o motor mede

`perfilar_tabela(registros, campos, chaves=None)` devolve o total de registros,
um perfil por campo e um perfil por chave declarada. Por campo, `preenchidos`
exclui nulos, `nulos` inclui `None`, string vazia, string só com espaços e
coluna ausente, e `distintos` usa igualdade exata tipada. `grupos_duplicados`
conta grupos com frequência maior que um e `repeticoes_excedentes` soma
`frequência - 1`. O campo só é `candidata_unica_na_amostra` se houver pelo
menos um registro, todos estiverem preenchidos e os valores forem distintos.

Uma chave composta considera somente linhas completas; por isso informa
`completos` e `incompletos` separadamente. Sua unicidade estatística também
nunca é uma afirmação de chave primária. Listas e dicionários são comparáveis
por representação canônica, mas não viram candidatos automáticos a chave
simples. A decisão de criar PK, índice ou chave de negócio continua dependendo
do esquema, da cobertura histórica e de revisão humana.

A igualdade é tipada: `bool`, número, texto, objeto e data ficam separados.
Inteiros, `float` e `Decimal` equivalentes são comparados como o mesmo número,
sem converter texto (`"001"` continua diferente de `1`) e sem tolerância ou
arredondamento de ponto flutuante. `0` e `False` não são nulos. Datas não são
convertidas de texto nem aproximadas.

`colisoes_normalizacao` é um alerta auxiliar: NFKC, `strip` e `casefold`
podem mostrar que valores textuais diferentes seriam confundidos por uma
padronização. A contagem não altera o valor exato, não remove duplicatas e não
força correspondência em relação. Colisão exige decisão humana e regra de
domínio; não é uma resolução automática.

`perfilar_relacao(origem, campos_origem, destino, campos_destino)` conta
registros de origem completos que encontram destino por igualdade exata,
órfãos de origem completos sem destino, incompletos dos dois lados e grupos
ambíguos no destino. A cardinalidade é observada somente nos grupos que
correspondem: um filho para um pai é `1:1`, vários filhos para um pai é `N:1`,
um registro de origem para vários registros de destino é `1:N`, e duplicidade
dos dois lados é `N:N`. Um destino com múltiplos pais/linhas para a mesma chave
fica marcado como ambíguo e não constitui uma FK válida. Sem grupos
correspondidos, o resultado é `sem_correspondencia`.

## Sequência de diagnóstico em cinco processos

O catálogo pode ser aplicado em etapas, mantendo as fontes e sem migrar tudo
de uma vez:

1. **Negócios e lotes:** começar pelos códigos de negócio, lotes e eventuais
   chaves compostas. Medir faltas, duplicidades e relações explícitas entre
   negócio e lote; não transformar um código apenas parecido em vínculo.
2. **Compras e documentos:** perfilar número/série, GTA, NF e referências
   documentais em exportações separadas. Colisões de formatação ficam como
   pendências; o catálogo não anexa documento, cria compra ou completa campos.
3. **Confinamento:** comparar identificadores de confinamento, operação,
   curral e entrada. Chaves compostas podem descrever, por exemplo, operação +
   lote, enquanto a cardinalidade observada revela órfãos e duplicidades sem
   alterar o ledger.
4. **Venda e abate:** verificar códigos de operação, abate e romaneio, além de
   relações declaradas entre cabeçalho e itens. A análise não confirma peso,
   quantidade, recebimento ou promoção operacional.
5. **Financeiro transversal:** por último, cruzar referências explícitas de
   operação com fluxo de caixa, banco, promissórias e demais fontes financeiras.
   O resultado é apenas diagnóstico de cobertura e ambiguidade: não concilia,
   baixa, paga, cria lançamento ou substitui a revisão financeira.

Em todas as fases, os relatórios podem permanecer em `docs/privado/`, com
permissões restritas, enquanto o código e este contrato público permanecem
livres de números de negócio, nomes de parceiros, credenciais e outros dados
operacionais. O catálogo é uma fotografia do intervalo escolhido; ausência de
linha, unicidade observada ou ausência de colisão não prova a realidade de toda
a base.

## Limites de leitura e testes

O leitor é diagnóstico, não importador geral de planilhas: aceita XLSX com
XML UTF-8, não executa macros, links externos nem fórmulas, não herda células
mescladas e não recupera casas decimais já perdidas na origem. Linhas contendo
apenas erros ou fórmulas sem cache permanecem como incompletas. Linhas de
resumo não são excluídas por adivinhação: use intervalos e exclusões explícitos
e preserve também o catálogo integral como referência. Relatórios identificam
as linhas lidas e as excluídas. CSV/TSV exigem UTF-8; arquivos criptografados,
legados `.xls` e fontes acima dos limites são recusados sem alterar a origem.

Um snapshot JSON exportado anteriormente pode ser lido como
`{"tabelas":{"operacoes":[...]}}`; isso não verifica o Supabase ao vivo.
O diagnóstico local não conta PKs/FKs/índices efetivamente existentes no banco.
Essa confirmação requer inventário de esquema separado e somente leitura,
antes de propor qualquer migração.

Testes locais sem rede ou dados reais:

```sh
python3 -B -m unittest tools.test_catalogo_chaves tools.test_perfilar_chaves_fontes
python3 tools/test_ecossistema.py
git diff --check
```

A descoberta automática de `tools/test_*.py` inclui essas regressões na bateria
existente. Não é necessário instalar plugin, agendar nova rotina ou implantar
na VPS para usar o catálogo.
