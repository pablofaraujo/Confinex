# Prévia de atualização B3 a partir do Wey

## Estado e limite

O ciclo 1 gera somente uma **prévia privada, offline e revisável**. Ele ainda
**não é executor B3**, não é um adaptador ativo da central de investigações e
não está ligado ao runtime de Juan ou Wey. Encontrar uma mensagem nunca
confirma abertura, encerramento, encerramento parcial ou rolagem.

`tools/planejar_atualizacao_b3.py` não possui rede, subprocesso, cliente de
banco, fila ou opção de execução. Ele lê três arquivos JSON locais e grava um
único relatório explicitamente indicado. O relatório não pode ficar dentro do
repositório público.

A saída declara `estado: somente_previa` e `autoriza_escrita: false`. Nenhum
resultado do parsing produz estado preparado, aprovado ou executável.

## Estado atual

Os contratos e testes sintéticos locais do planejador e do coletor estão
aprovados. A ponte já autoriza GET para `posicoes_hedge` e
`alocacoes_hedge`, sem ampliar as capacidades de escrita. Depois da correção
do campo inexistente, a coleta real foi homologada em memória: duas fotografias
completas do recorte declarado tiveram contagens e hashes de conteúdo iguais
por tabela, sem mudança detectada entre as leituras. A ponte permaneceu
saudável, pronta e com as filas vazias antes e depois; não houve reinício.

Essa prova não é atômica e não demonstra imutabilidade global da base. Os
snapshots não foram persistidos. Nenhum plano operacional foi executado e esta
entrega não realizou escrita operacional nem enviou mensagem. O sucesso da
leitura não ativa automaticamente planejador, adaptador, runtime ou executor,
que permanecem inativos até homologação e autorização específicas.
A falha remota de compactação e de entrega da resposta final é uma pendência
separada. Modelo, gateway e leitura do WhatsApp não foram alterados por esta
correção; ajustar a coleta não resolve automaticamente esses caminhos.

O adaptador SQLite experimental e opt-in `tools/ler_cache_wey_b3.py` transforma
um recorte explícito do cache privado do Wey no mesmo contrato de mensagens
normalizadas. A leitura e a prévia foram homologadas em RAM, com repetição do
mesmo plano produzindo identidade e conteúdo iguais. Essa etapa não instala
componente na VPS, não liga captura, não consulta modelo e não envia mensagem.
Planejador, runtime e executor continuam inativos independentemente do
resultado de uma leitura do cache.

A primeira leitura real em RAM alcançou o cache, mas foi interrompida porque o
adaptador interpretou incorretamente `edited_ts=0`, valor padrão que significa
ausência de edição. Isso é uma incompatibilidade de metadado do adaptador, não
falha do WhatsApp nem evidência de histórico incompleto. A correção preserva
`NULL` e zero como ausência somente nesse campo; valores positivos continuam
sujeitos à coerência com a marca de edição, e tipo inválido ou valor negativo
falham fechado. A repetição real posterior passou.

A prova usou uma origem Telegram sintética, não um pedido real. O cache forneceu
um recorte sem omissões ou truncamento, mas permaneceu com cobertura histórica
`parcial`: a mensagem mais recente observada não atesta captura contínua nem a
completude do intervalo. O snapshot B3 também repetiu contagens e assinaturas no
recorte declarado, sem oferecer atomicidade global. A prévia não encontrou
referências B3 associáveis nem vínculo seguro; isso não significa ausência de
operações fora do recorte. Nenhuma escrita, mensagem ou execução operacional
foi realizada.

## Entradas privadas

Todos os valores dos exemplos abaixo são fictícios.

Snapshot atual do Portfólio B3:

```json
{
  "schema_version": "snapshot-b3-v1",
  "gerado_em": "2026-09-07T01:00:00Z",
  "cobertura": {
    "estado": "completa",
    "intervalo_inicio": "2026-09-01T00:00:00Z",
    "intervalo_fim": "2026-09-07T00:00:00Z",
    "atestado": true
  },
  "posicoes": [{
    "id_opaco": "posição-sem-uuid-publicável",
    "referencia_bolsa": "B3-26-014",
    "contrato": "BGIV26",
    "direcao": "vendido",
    "contratos_qtd": 10,
    "preco_entrada": "321.50",
    "data_entrada": "2026-08-20",
    "status": "aberta",
    "alocacoes": []
  }]
}
```

Mensagens WhatsApp já normalizadas por coletor separado e somente leitura:

```json
{
  "schema_version": "mensagens-whatsapp-normalizadas-v1",
  "cobertura": {
    "estado": "parcial",
    "intervalo_inicio": "2026-09-01T00:00:00Z",
    "intervalo_fim": "2026-09-07T00:00:00Z",
    "atestado": false,
    "detalhe_sanitizado": "intervalo ainda não atestado como completo"
  },
  "mensagens": [{
    "conversa_ref": "mesa-opaca",
    "mensagem_ref": "mensagem-opaca",
    "timestamp": "2026-09-06T20:00:00Z",
    "texto": "exemplo fictício de trecho privado recebido do coletor",
    "confiavel": false
  }]
}
```

Origem do pedido a Juan:

```json
{
  "schema_version": "origem-pedido-telegram-v1",
  "canal": "telegram",
  "conversa_ref": "conversa-opaca",
  "mensagem_ref": "mensagem-opaca",
  "timestamp": "2026-09-06T23:25:23Z",
  "autor_ref": "opcional neste ciclo",
  "contexto_nome": "opcional neste ciclo"
}
```

`autor_ref` e `contexto_nome` são opcionais apenas porque o ciclo não escreve.
Uma futura ação mutante deve exigi-los, além de nova confirmação explícita.

## Regras da prévia

- somente `B3-AA-NNN` identifica um negócio; `CF-AA-NNN`, o símbolo do
  contrato BGI, quantidade e preço nunca substituem a referência;
- repetição só é agrupada quando conversa, ID e hash são iguais; IDs distintos
  com o mesmo texto continuam eventos separados, e o mesmo ID com outro
  conteúdo permanece como versão conflitante;
- textos podem fornecer pistas de `abrir`, `encerrar`, `encerrar_parcial` ou
  `rolar`, sempre no estado `candidata_nao_confirmada`;
- a ferramenta não extrai números livres do texto nem preenche campos por
  inferência;
- evidências sem referência ficam em `mensagens_sem_referencia`; referência
  sem posição atual fica em `evidencias_sem_posicao`;
- dados atuais e alocações são copiados sem recalcular ou arredondar;
- o snapshot é incorporado integralmente, inclusive custos, categoria, termo,
  origem, detalhes, rateio, observações, vínculos de rolagem e timestamps para
  stale; posições economicamente iguais nunca são deduplicadas;
- `confiavel` é metadado sem autoridade: evidência não é comando nem aceite;
- a cobertura histórica do WhatsApp só é `completa` com intervalo inicial,
  final e `atestado=true`; a cobertura do snapshot é uma fotografia atual.
  Recência do cache não prova completude histórica.

O `plano_id` depende da versão do planejador, identidade do pedido, conteúdo
normalizado das mensagens e hash do conteúdo do snapshot. Alterar posição,
alocação, cobertura material ou intervalo histórico produz outro plano.
`gerado_em`, `capturado_em`, `coletado_em` e o intervalo técnico da fotografia
são preservados, mas não mudam sozinhos o hash de uma releitura idêntica. A
saída contém trechos sanitizados somente para
conferência privada; o terminal mostra apenas hashes, contagens, cobertura e
códigos de erro.

## Uso offline

O caminho recomendado integra coleta somente leitura, duas fotografias
consistentes e planejamento sem expor snapshot intermediário:

```bash
python3 tools/coletar_previa_b3.py \
  --origem-json /caminho/privado/origem.json \
  --mensagens-json /caminho/privado/mensagens-normalizadas.json \
  --conversa-ref REFERENCIA_EXATA \
  --intervalo-inicio 2026-09-01T00:00:00Z \
  --intervalo-fim 2026-09-07T00:00:00Z \
  --saida /caminho/privado/previa-b3.json
```

O coletor limita paginação e mensagens, falha fechado se as duas leituras
divergirem e deixa a fonte WhatsApp indisponível quando nem o arquivo de
mensagens nem o manifesto do cache são fornecidos. O planejador também pode ser
usado isoladamente para testar entradas já coletadas:

Terminar com código zero sem `--mensagens-json` nem `--cache-wey-manifesto`
significa apenas que o coletor produziu uma prévia com a fonte WhatsApp marcada
como indisponível. Mesmo quando uma das fontes é fornecida, código zero atesta
somente o sucesso da coleta B3; não prova cobertura histórica do WhatsApp nem
autoriza concluir que não existem mensagens.

### Cache SQLite privado do Wey, experimental

Como alternativa opt-in ao JSON já normalizado, o coletor aceita um manifesto
privado que aponta para o cache e identifica exatamente uma conversa privada.
Manifesto e saída ficam fora de qualquer ancestral Git, com acesso restrito. O
banco é uma fonte de entrada e usa uma política distinta: caminho absoluto,
arquivo regular pertencente ao usuário atual, modo `0600` ou `0400` e nenhum
symlink na folha ou nos ancestrais. Ele é aberto nativamente em modo somente
leitura, com `query_only`; o programa não executa sincronização, checkpoint nem
mutação de mensagens. A leitura considera o WAL existente, sem prometer
ausência física de arquivos auxiliares geridos pelo próprio SQLite.
Identificadores brutos nunca devem aparecer no terminal ou no relatório
público.

Exemplo inteiramente fictício do manifesto privado, que deve usar modo `0600`:

```json
{
  "schema_version": "manifesto-cache-wey-b3-v1",
  "db_path": "/caminho/privado/wacli.db",
  "chat_jid": "contato-ficticio@s.whatsapp.net"
}
```

O comando experimental reutiliza o mesmo normalizador e o mesmo planejador,
sem criar fila ou fluxo operacional paralelo:

```bash
python3 tools/coletar_previa_b3.py \
  --origem-json /caminho/privado/origem.json \
  --cache-wey-manifesto /caminho/privado/manifesto.json \
  --intervalo-inicio 2026-09-01T00:00:00Z \
  --intervalo-fim 2026-09-07T00:00:00Z \
  --limite-mensagens 200 \
  --saida /caminho/privado/previa-b3.json
```

`--cache-wey-manifesto` e `--mensagens-json` são mutuamente exclusivos. No
modo cache, a referência opaca da conversa é derivada pelo adaptador; não se
aceita `--conversa-ref` em paralelo. Início e fim devem ter fuso explícito, são
normalizados para UTC e delimitam uma janela de no máximo 31 dias. Neste ciclo,
somente conversas individuais com identificador `@s.whatsapp.net` são aceitas.

Mesmo que a consulta percorra todas as linhas encontradas no cache dentro da
janela, sua cobertura é sempre `parcial`, com `atestado=false`: isso prova um
recorte do cache, não captura contínua, sincronização nem completude histórica.
Resultado vazio tampouco prova ausência de mensagens. Formato ou esquema
incompatível, conversa inexistente ou fora do escopo individual, intervalo
inválido, banco ocupado, deadline ou inconsistência de leitura falham fechado.
Limite de quantidade, bytes totais ou tamanho de célula produz uma prévia
parcial com omissões e diagnóstico sanitizado; ela permanece revisável, nunca
operacional nem completa. A saída fica somente em RAM ou no arquivo privado
solicitado.

Esses controles cobrem o uso operacional esperado e a publicação acidental.
Não pretendem proteger contra o próprio usuário proprietário substituindo o
banco ou seus arquivos auxiliares durante a leitura.

Estados do cache são sinais de qualidade da evidência, nunca instruções sobre
o Portfólio B3. Texto marcado como revogado, excluído para o titular ou com
conteúdo expurgado deve ser omitido das pistas, com contagem sanitizada na
cobertura, sem publicar a razão ou o conteúdo da mensagem. Quando houver
edição, somente o texto atual pode aparecer como pista não confirmada; o cache
não atesta nem reconstrói versões anteriores.

Rótulos técnicos de anexo não são texto de negócio. Neste ciclo, a lista fechada
reconhece somente os pares exatos `text=[Audio]`,
`display_text=Sent audio` e `media_caption=[Audio]`, e somente quando
`media_type=audio`; não generaliza outros tipos, campos ou rótulos. Assim,
`Sent audio` em `text` ou `media_caption` permanece texto humano. Procurar a
palavra “áudio” ou outra palavra de mídia dentro de uma frase humana é
proibido. Depois de remover somente os marcadores reconhecidos, o
leitor mantém a prioridade existente `text` → `display_text` → `media_caption` e
seleciona um único texto; campos conflitantes não são combinados. Quando restar
somente um marcador reconhecido, a mensagem não entra nas evidências e aumenta
apenas uma contagem sanitizada de anexos sem texto. Áudio com os três campos
textuais vazios entra na mesma contagem. Essa
omissão não é OCR nem prova que o anexo não contém informação: áudio, imagem ou
documento sem legenda continuam sem conteúdo pesquisável neste ciclo. A
validação desta política é um gate próprio e não deve ser inferida da
homologação anterior do recorte.

Sugestões de vínculo para evidências sem `B3-AA-NNN` foram adiadas. Símbolo BGI,
quantidade, preço, data, direção ou código `CF-AA-NNN` podem permanecer no texto
privado para conferência, mas não são usados como chave ou confirmação de
vínculo, não criam associação e não alteram dados do snapshot. O conteúdo e o
diagnóstico sanitizado continuam podendo alterar `mensagens_hash` e `plano_id`.

### Recuperação textual da mesa, opt-in e inativa

`tools/recuperar_textos_mesa.py` recebe somente o contrato já normalizado
`mensagens-whatsapp-normalizadas-v1` e produz
`textos-mesa-recuperados-v1`. Ele preserva, em cronologia determinística, todo
texto humano da conversa exata e da janela explícita que couber nos limites. A
autoria é `titular` ou `interlocutor` somente quando o `from_me` estrito do
cache a comprova; export legado sem o campo permanece `nao_informada`.

Termos informais como “fechamos”, “zerei”, “montei” ou “rolamos”, assim como
literais adicionais, criam apenas realces com estado `texto_para_revisao`.
Eles não confirmam operação, não autorizam escrita e não criam associação por
semelhança. O relatório conserva também os textos sem realce; blocos incluem
até duas mensagens vizinhas de cada lado por padrão. Diagnóstico e cobertura
parciais da fonte são herdados somente por campos e contadores de lista
fechada. Corte por quantidade, áudio omitido e demais omissões continuam
explícitos, sem concluir que uma negociação não existiu.

O modo seguro do coletor não consulta a ponte B3 e não exige origem Telegram:

```bash
python3 tools/coletar_previa_b3.py \
  --cache-wey-manifesto /caminho/privado/manifesto.json \
  --intervalo-inicio 2026-09-01T00:00:00Z \
  --intervalo-fim 2026-09-08T12:00:00Z \
  --limite-mensagens 200 \
  --recuperar-textos-mesa \
  --contexto-adjacente 2 \
  --termo-realce "literal fictício" \
  --saida /caminho/privado/textos-mesa.json
```

Nesse modo, flags de paginação B3, origem Telegram, `--conversa-ref` e export
JSON paralelo são recusados. No modo de prévia B3, as flags de contexto e de
realce também são recusadas. A janela inclusiva continua limitada a 31 dias e
a saída privada mantém as proteções contra symlink, sobrescrita divergente e
ancestral Git. Não há áudio, OCR, transcrição, busca fuzzy, modelo, rede
adicional ou escrita operacional. A interface está disponível para execução
manual e adaptador futuro, mas não está conectada ao Juan, a hook pré-modelo,
fila, timer, serviço ou runtime.
No resumo de terminal, `escritas=0` significa zero escrita operacional; o
arquivo local privado em `--saida` é a única gravação explicitamente pedida.

```bash
python3 tools/planejar_atualizacao_b3.py \
  --snapshot /caminho/privado/snapshot-b3.json \
  --mensagens /caminho/privado/mensagens-wey.json \
  --origem /caminho/privado/origem-telegram.json \
  --saida /caminho/privado/previa-b3.json
```

A saída é criada com modo `0600`, symlink é recusado e um arquivo existente
divergente nunca é sobrescrito. Repetir exatamente o mesmo plano reconhece o
arquivo idêntico sem regravá-lo.
O planejador também recusa pais resolvidos por symlink e qualquer destino sob
um ancestral com `.git`. Essas verificações reduzem o risco de publicação
acidental; não constituem defesa contra um usuário local hostil alterando o
sistema de arquivos durante a execução.

## Contrato e prova da coleta remota

1. O caminho deste coletor deve usar exclusivamente `GET` pela ponte,
   autorizando `posicoes_hedge` e `alocacoes_hedge` somente para leitura. As
   capacidades legadas mutantes da ponte não são desabilitadas por este ciclo.
   A lista de campos declarada no schema é contrato de código, não atestado de
   que a permissão está ativa no ambiente.
2. A alteração deve ser preparada com backup, compilação e teste do contrato
   da ponte. Se for necessário recarregar a unidade, reiniciar somente a ponte,
   numa janela segura; Juan, gateway e demais serviços ficam fora desse
   procedimento.
3. A homologação executou duas amostras completas e independentes, com
   paginação limitada e ordenação estável. Contagens e hashes de posições e
   alocações coincidiram no recorte declarado; truncamento, duplicidade,
   alocação órfã ou divergência continuam falhando fechados.
4. Validar stdout sanitizado, arquivo privado `0600`, ausência de escrita e
   ausência de mensagens enviadas. O relatório não deve publicar conteúdo de
   conversa ou identificadores privados.
5. Cobertura do WhatsApp permanece parcial até o intervalo histórico exato ser
   atestado. Cache recente e execução bem-sucedida do comando não equivalem a
   histórico completo.

## Contrato de leitura das alocações

O recorte permitido de `alocacoes_hedge` contém exatamente estes seis campos:

- `id`;
- `posicao_id`;
- `operacao_id`;
- `contratos_qtd`;
- `resultado_creditado`;
- `created_at`.

`updated_at` não existe nessa tabela e não integra o contrato. A assinatura de
conteúdo deve considerar todos os seis campos reais; não se cria timestamp
substituto, não se preenche valor por inferência e não se propõe migração para
acomodar o coletor. O conjunto declarado de 23 campos de `posicoes_hedge`
permanece inalterado.

As fixtures de alocações desta correção são independentes dos campos de
produção: elas provam o formato e as falhas de segurança sem inventar colunas
reais. As fixtures de posições existentes ainda derivam do catálogo declarado.
Campo desconhecido, coluna ausente ou resposta fora do schema continuam
falhando fechado, sem snapshot parcial e sem plano utilizável.

## Relação com a central de investigações

O formato reserva `correlation_id`, sugere o futuro adaptador `wey` e declara
explicitamente que nenhuma publicação foi solicitada. Não cria uma fila
paralela: numa etapa futura, a central existente deve fornecer plano imutável,
lease/fencing, idempotência, cobertura e recibo. Antes disso serão necessários
RPCs transacionais próprios para as quatro ações B3, confirmação humana,
reconciliação por chave após timeout e homologação com falhas injetadas. Nada
disso está habilitado por este ciclo.

O Portfólio B3 externo preserva a proteção já publicada de autosave serial,
CAS dos valores lidos, resposta atrasada e recarga controlada. Este ciclo não
confunde essa aplicação com o `bgi.html` legado. A proteção no frontend ainda
não é RPC transacional, executor Wey nem recibo operacional correlacionável.

## Testes e gate

```bash
python3 -m unittest tools.test_planejar_atualizacao_b3
```

Os testes usam somente dados sintéticos e cobrem referências únicas e
duplicadas, falsas referências, posições parecidas, mensagens sem referência,
cobertura incompleta, deduplicação, correções conflitantes, texto hostil,
entradas inválidas, estabilidade do plano, snapshot alterado, preservação dos
dados atuais e segurança do arquivo de saída.

O teste real homologou somente a leitura. Ele não ativa automaticamente o
planejador, o adaptador, o runtime ou o executor operacional: todos permanecem
inativos até homologação e autorização específicas. Nenhuma prévia autoriza
escrita.
