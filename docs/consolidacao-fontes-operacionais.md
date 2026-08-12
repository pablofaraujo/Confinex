# Consolidação das fontes operacionais

`tools/consolidar_fontes_operacionais.py` cruza snapshots privados e somente
leitura de Supabase, mensagens do Juan, planilha de NFs/GTAs e extrato bancário.
O resultado é um plano de conferência; ele não possui caminho de execução e não
faz chamadas de rede.

O cruzamento cobre:

- contagens e datas de corte das fontes;
- correspondência da planilha bancária com `transacoes_banco` por identificador;
- candidatos de conciliação por data e valor, sempre marcados como não confirmados;
- ambiguidades preservadas sem vínculo;
- NFs e GTAs entre planilha, staging fiscal, entradas de confinamento e tabela `gtas`;
- menções operacionais deduplicadas das sessões do Juan;
- estados de rascunhos, ações e eventos;
- datas futuras atípicas no fluxo de caixa.

Exemplo, usando somente arquivos privados temporários:

```bash
python3 tools/consolidar_fontes_operacionais.py \
  --supabase /caminho/privado/snapshot.json \
  --juan /caminho/privado/mensagens.json \
  --gta-planilha /caminho/privado/gta.json \
  --banco-planilha /caminho/privado/banco.json \
  --data-referencia AAAA-MM-DD \
  --saida-json /caminho/privado/plano.json \
  --saida-md /caminho/privado/relatorio.md
```

O relatório não autoriza conciliar banco, criar GTA, vincular documento,
alterar rascunho ou lançar operação. Uma data de corte anterior à referência é
um bloqueio de atualização, não um dado a ser preenchido por inferência.

## Conciliação documental por registro

`tools/conciliar_documentos_operacionais.py` complementa o inventário agregado
com um cruzamento por registro entre exportação fiscal do Agronotas, ficha
detalhada do IMA, extrato OFX e planilha de negócios. Ele aceita `.xlsx`, `.csv`
ou `.json` nas fontes tabulares, lê o `.xlsx` sem executar macros e grava apenas
um plano privado em JSON e Markdown.

```bash
python3 tools/conciliar_documentos_operacionais.py \
  --agronotas /caminho/privado/documentos.xlsx \
  --aba-agronotas "Fiscal GTAs" \
  --ima-pdf /caminho/privado/ficha-detalhada.pdf \
  --ofx /caminho/privado/extrato.ofx \
  --negocios /caminho/privado/negocios.xlsx \
  --aba-negocios "Negocios" \
  --data-referencia AAAA-MM-DD \
  --saida-json docs/privado/conciliacao-documentos.json \
  --saida-md docs/privado/conciliacao-documentos.md
```

As regras permanentes são:

- GTA igual na nota e no IMA é vínculo documental forte;
- NF ou GTA igual ao negócio é candidato forte, ainda não confirmado;
- valor bancário igual e único em até 90 dias é somente candidato provável;
- valor e data sem identificador documental nunca formam vínculo forte;
- duas ou mais correspondências ficam ambíguas e intactas;
- documentos sem relação com gado são ignorados, mas contabilizados;
- fonte desatualizada vira pendência explícita;
- não existe argumento `--executar`, chamada de rede ou escrita operacional.

Números de GTA, NF, lançamentos e negócios permanecem somente nos relatórios em
`docs/privado/`, que não são versionados. O repositório público guarda apenas o
código, testes e regras sanitizadas.

## Histórico exportado do Telegram

`tools/consolidar_historico_telegram.py` transforma um ou mais arquivos
`messages.html` exportados pelo Telegram Desktop em um plano privado e
reexecutável. Ele trabalha apenas com os arquivos locais: não chama o Telegram,
o Supabase ou qualquer serviço de OCR.

```bash
python3 tools/consolidar_historico_telegram.py \
  --telegram-html /caminho/privado/grupo/messages.html \
  --contexto "Nome humano do grupo" \
  --aliases /caminho/privado/aliases.json \
  --documentos-plano /caminho/privado/conciliacao-documentos.json \
  --complemento-ima /caminho/privado/saldo-ima.json \
  --saida-json /caminho/privado/consolidacao-telegram.json \
  --saida-md /caminho/privado/consolidacao-telegram.md
```

Para vários exports, repita `--telegram-html` e `--contexto` na mesma ordem.
Partes sucessivas do mesmo chat devem receber o mesmo nome humano. Apelidos de
fornecedor ficam num JSON privado explícito, nunca embutidos no código público.

O plano registra os cortes informados pelas fontes em vez de assumir posição
atual. A ficha sintética do IMA pode complementar o saldo e a data, mas uma
variação sem ficha detalhada correspondente permanece inexplicada e não gera
movimentação compensatória.

O relatório Markdown privado inclui uma fila por negócio, ordenada por
prioridade. Ela mostra contexto humano, sexo, categoria, destino, data-base,
quantidade de versões semanticamente distintas, quantidade total de evidências,
campos divergentes, campos ausentes e a próxima ação. Uma divergência financeira
recebe prioridade alta; nenhuma linha mistura campos de mensagens diferentes.
O JSON privado preserva cada versão, suas repetições e todos os respectivos IDs
de mensagem para auditoria. Códigos humanos seguem o padrão anual `NEG-AA-NNN`.

Quando recebe `--documentos-plano`, o importador exige que o plano declare
explicitamente zero escrita e zero alteração operacional. A assinatura SHA-256
do conteúdo documental, sem o horário de geração, integra o novo plano. O
relatório resume as contagens de NF/GTA, extrato, negócios e IMA; ausência de
candidato exato continua pendente e nunca é preenchida por aproximação.

As regras permanentes do importador são:

- mensagens agrupadas herdam o autor anterior e mantêm ID, ordem e contexto;
- anexos presentes recebem hash; anexos omitidos viram pendência;
- conteúdo repetido é deduplicado somente dentro do mesmo contexto;
- testes, homologações e modelos não entram nos negócios reais;
- versões iguais ou parciais compatíveis viram uma única alternativa, mas todas
  as mensagens permanecem como evidência;
- sexo, categoria e destino participam da identidade: novilha, vaca e garrote,
  ou destinos como confinamento, fazenda e abate, não são unidos;
- um resumo que soma categorias ou destinos distintos permanece evidência
  agregada e não cria uma avaliação adicional;
- números brasileiros com ponto de milhar, como peso em kg, são convertidos em
  número e nunca em data na planilha de conferência;
- campos realmente divergentes permanecem ambíguos;
- uma correção explicitamente posterior pode ser indicada como preferida, mas
  continua não confirmada e revisável;
- mesmo fornecedor e mesma data nunca bastam para unir compras distintas;
- GTA com número exatamente igual ao plano documental é candidato forte, sem
  confirmação ou escrita automática;
- não existe argumento `--executar` nem caminho de promoção operacional.

## Atualização por OFX

`tools/analisar_extrato_ofx.py` compara um OFX recém-baixado com um snapshot
somente leitura de `transacoes_banco`. Ele não guarda dados de conta, nomes,
descrições ou valores no relatório: registra apenas hash do arquivo, período,
contagens, duplicidades e quantidade de identificadores já existentes ou novos.

```bash
python3 tools/analisar_extrato_ofx.py \
  --ofx /caminho/privado/extrato.ofx \
  --snapshot /caminho/privado/transacoes-banco.json \
  --data-referencia AAAA-MM-DD \
  --saida-json /caminho/privado/plano-extrato.json \
  --saida-md /caminho/privado/relatorio-extrato.md
```

O comando não tem opção de execução. Lançamentos ausentes são apenas apontados;
uma importação posterior precisa de autorização própria, chave idempotente por
`FITID` e comparação de contagens antes/depois.

## Atualização pela ficha sanitária do IMA

`tools/analisar_ficha_ima.py` lê o período, o saldo do rebanho e as GTAs bovinas
de entrada e saída de uma ficha sanitária. Em seguida, compara somente os
números normalizados com snapshots de `gtas`, `entradas_confinamento`,
`notas_fiscais_xml_raw` e `fazenda_ametista`.

O relatório público não guarda números de GTA, nomes, documentos pessoais,
endereços, origens ou destinos. O resultado registra apenas período, contagens,
quantidades agregadas, cobertura das fontes e eventual diferença de saldo. A
ferramenta não possui caminho de escrita e nunca cria movimentação compensatória
para fazer o saldo fechar.

```bash
python3 tools/analisar_ficha_ima.py \
  --pdf /caminho/privado/ficha.pdf \
  --gtas /caminho/privado/gtas.json \
  --entradas /caminho/privado/entradas.json \
  --fiscal /caminho/privado/fiscal.json \
  --ledger /caminho/privado/ledger.json \
  --data-referencia AAAA-MM-DD \
  --saida-json /caminho/privado/plano-ima.json \
  --saida-md /caminho/privado/relatorio-ima.md
```

O modo `--pdf` usa `pdftotext -layout` quando Poppler está disponível. Se não
estiver, usa `pypdf` opcional em modo de preservação de layout; como última
alternativa, o texto pode ser fornecido por `--texto-extraido`.
