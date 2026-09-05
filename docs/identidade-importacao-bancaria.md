# Identidade na importação e conferência bancária

Status: código preparado para revisão, sem migração, implantação na VPS ou
saneamento de registros existentes nesta etapa. As provas com arquivos reais
são locais, usando fotografias privadas anteriores; não atestam o estado atual
de todo o Supabase.

## Contrato incremental

O parser estrito de `perfilar_identidade_ofx.py` é compartilhado pelas duas
leituras: `extrair_ofx_privado()` conserva NAME/MEMO para a conferência de PIX;
`perfilar_ofx()` projeta explicitamente apenas os campos do diagnóstico, sem
esses textos. Nenhuma função acessa rede ou importa registros. O perfil com
conta/FITID continua privado, mesmo sem descrição.

`identidade_bancaria.py` define a identidade lógica como **BANKID + BRANCHID +
ACCTID + ACCTTYPE + CURDEF + FITID**. São textos exatos, sem conversão numérica,
remoção de zeros ou aproximação de nomes. O rótulo livre da conta não comprova
essa identidade. A evidência estruturada fica em `dados_origem.ofx`, versão 1:
identidade, assinatura da identidade, data original, formato e hash do bloco.
Hashes verificam consistência, não autenticidade bancária nem autorização.

Valores são decimais assinados. Zero continua sendo uma ocorrência do extrato;
campo ausente não vira zero. A importação não arredonda o valor. Propostas de
conciliação em centavos exigem valor finito, não zero e sem arredondamento.
Data civil é armazenada separadamente do horário/fuso original.

## Importador: nenhuma repetição silenciosa

`tools/importar_ofx_staging.py` mantém a chave física `(conta, fitid)` e o
algoritmo UUID legado. Não renumera registros, não cria alias e não modifica
índice ou coluna. O contrato completo fica no JSON já existente:

- cada demonstrativo mantém sua própria identidade;
- ocorrências idênticas geram uma candidata, com todas as referências preservadas;
- mesma identidade/FITID com horário, valor, tipo, descrição ou conteúdo diferente
  bloqueia o arquivo inteiro — não aparece como “já importado”;
- legado sem prova, identidade incompleta e colisão de UUID ou chave física
  bloqueiam; não se cria outra chave para contornar a restrição;
- identidade comprovada já registrada preserva seu ID e rótulo;
- a fonte conserva referências de todas as ocorrências, inclusive as já vistas
  em outro arquivo, sem duplicar os textos do PIX em seu catálogo;
- o plano identifica também os dados e a fotografia usados, não apenas IDs;
- `executavel: false` impede **qualquer** inserção, inclusive da fonte.

Diferenças de formatação podem alterar o hash do bloco XML/SGML. Isso é uma
conferência conservadora, não uma prova de alteração econômica. Não eliminar
uma versão só porque dia civil e valor coincidem.

O escritor conserva tentativa única e tabela permitida. Não usa mais
`ignore-duplicates`: exige uma linha de resposta compatível com o conteúdo
enviado antes de contar criação. Colisão concorrente, timeout ou resposta
incompleta interrompem sem repetição automática. O processo é sequencial, não
uma transação atômica entre as duas tabelas: pode haver resultado parcial.
Nesse caso, conferir a fonte e cada registro antes de preparar outro plano;
nenhuma compensação ou exclusão automática é permitida.

## Consumidores sem FITID global

`analisar_extrato_ofx.py` separa presença comprovada, ausência na amostra e
casos indeterminados. Identidade ausente ou conteúdo divergente não prova nem
presença nem lançamento novo. Repetição idêntica tem um caso único e contagem
de ocorrências separada. Resumo não contém conta, FITID, nome, MEMO ou valores.

`propor_conciliacoes_staging.py` usa vínculo explícito conferido ou identidade
comprovada. Vínculo quebrado, conflito e legado sem prova ficam pendentes.
Uma chave lógica repetida no staging bloqueia a proposta, mesmo com descrições
diferentes. A heurística de repetição conserva sinal e identidade; crédito
nunca é igualado a débito pelo valor absoluto. Para fluxo de caixa, a direção
entrada/saída deve ser explícita e compatível. Como o candidato de negócio não
declara direção bancária, crédito não é proposto como pagamento de compra.
Texto, data e magnitude só orientam uma sugestão pendente, nunca comprovam
conciliação. O escritor de propostas também exige resposta conferida e não
repete envio incerto.

Escopo: estes três caminhos. As funções legadas `campo_ofx`/`data_ofx` ainda
usadas por `conciliar_documentos_operacionais.py` não foram substituídas nesta
etapa. Esse cruzamento documental offline requer gate próprio antes de adotar
o mesmo contrato. Não declarar todos os importadores do ecossistema corrigidos.

## Ensaiar sem credenciais

Snapshot privado JSON, com as duas tabelas explicitamente presentes (listas):
`tabelas.fontes_importacao` e `tabelas.transacoes_banco_staging`.

```sh
python3 -B tools/importar_ofx_staging.py \
  --ofx /caminho/privado/extrato.ofx \
  --snapshot /caminho/privado/snapshot.json

python3 -B -m unittest tools.test_identidade_bancaria \
  tools.test_perfilar_identidade_ofx tools.test_importar_ofx_staging \
  tools.test_analisar_extrato_ofx tools.test_propor_conciliacoes_staging
python3 -B tools/test_ecossistema.py
git diff --check
```

`--snapshot` não instancia cliente Supabase e proíbe `--executar`. Uma projeção
sem metadados suficientes gera indeterminação, não preenche a identidade por
inferência. No modo conectado existente, a ausência de `--executar` mantém
somente leituras. Não usar esta documentação como autorização de escrita.

Testes usam identidades fictícias e chamadas simuladas, incluindo concorrência
por resposta 409 e timeout. A prova com os arquivos reais fica em
`docs/privado/`, com hashes das entradas antes/depois. Nenhum registro de teste
é criado na base; não existe limpeza de produção a executar.

## Saneamento posterior e reversão

Antes de alterar duplicidades antigas:

1. Atualizar a fotografia completa dos alvos e dependências, incluindo propostas
   de conciliação e decisões. O catálogo de FKs não cobre referências em JSON;
   conferir também os consumidores e históricos pertinentes.
2. Provar identidade por fonte/hash e comparar todas as versões, preservando
   horários e descrições. Um alias proposto vale apenas para as fontes e IDs
   comprovados, não para todas as contas com nome parecido.
3. Definir versão de referência sem apagar a evidência anterior nem somar
   ambos os registros. Mudança em `(conta, fitid)` não pode colidir com UNIQUE.
4. Preparar plano específico com antes/depois, dependências e histórico, testar
   a reversão e obter autorização antes de qualquer escrita.

Esta etapa não oferece comando de saneamento real: ainda não há autorização
nem inventário atualizado de todas as dependências. Os casos com horários
diferentes permanecem como conflitos, não duplicatas automaticamente removíveis.

Reversão de código: reverter o commit desta etapa por um novo commit, depois
dos testes, sem reset de histórico. Não há migração ou dados a desfazer. Caso
seja necessário recuar após futura implantação, suspender a escrita do
importador antes; retornar ao parser permissivo não resolve os conflitos.
