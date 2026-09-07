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
aprovados. A coleta B3 no ambiente remoto, porém, está bloqueada no gate de
permissão de leitura: a ponte ainda não autoriza `posicoes_hedge` e
`alocacoes_hedge`. A tentativa observada foi recusada antes da leitura da
tabela; uma chamada recusada não conta como consulta bem-sucedida.

Por isso, nenhum plano real foi gerado ou homologado. O runtime continua
inativo, e esta entrega não realizou escrita operacional nem enviou mensagem.
A falha remota de compactação e de entrega da resposta final é uma pendência
separada: corrigir a coleta não resolve automaticamente esse caminho.

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
divergirem e deixa a fonte WhatsApp indisponível quando o arquivo de mensagens
não é fornecido. O planejador também pode ser usado isoladamente para testar
entradas já coletadas:

Terminar com código zero sem `--mensagens-json` significa apenas que o coletor
produziu uma prévia com a fonte WhatsApp marcada como indisponível. Não prova
cobertura histórica nem autoriza concluir que não existem mensagens.

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

## Requisitos para homologar a coleta remota

1. O caminho deste coletor deve usar exclusivamente `GET` pela ponte,
   autorizando `posicoes_hedge` e `alocacoes_hedge` somente para leitura. As
   capacidades legadas mutantes da ponte não são desabilitadas por este ciclo.
   A lista de campos declarada no schema é contrato de código, não atestado de
   que a permissão está ativa no ambiente.
2. A alteração deve ser preparada com backup, compilação e teste do contrato
   da ponte. Se for necessário recarregar a unidade, reiniciar somente a ponte,
   numa janela segura; Juan, gateway e demais serviços ficam fora desse
   procedimento.
3. Executar duas amostras completas e independentes, com paginação limitada e
   ordenação estável. Contagens e hashes de posições e alocações devem
   coincidir; truncamento, duplicidade, alocação órfã ou divergência falham
   fechados e não geram snapshot utilizável.
4. Validar stdout sanitizado, arquivo privado `0600`, ausência de escrita e
   ausência de mensagens enviadas. O relatório não deve publicar conteúdo de
   conversa ou identificadores privados.
5. Cobertura do WhatsApp permanece parcial até o intervalo histórico exato ser
   atestado. Cache recente e execução bem-sucedida do comando não equivalem a
   histórico completo.

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
