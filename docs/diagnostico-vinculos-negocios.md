# Diagnóstico privado de vínculos — etapa P1

Esta etapa compara fontes e prepara um plano de conferência. Não normaliza
registros, não aplica migração, não altera a interface e não promove operações.

## Coleta autorizada, separada da análise

`supabase/audits/vinculos_negocios_somente_leitura.sql` lê oito projeções:
operações, compras, componentes, avaliações, estimativas, candidatos, banco em
conferência e banco operacional. A consulta usa `REPEATABLE READ READ ONLY`,
limites de tempo e `ROLLBACK`. As linhas são agregadas por ID sem `LIMIT`, e o
JSON inclui a contagem de cada array. O limite de linhas do editor não reduz
os arrays: a consulta devolve um único objeto. Exportação ausente, truncada ou
inválida deve bloquear a análise.

Executar duas vezes somente por acesso autorizado. Guardar cada resultado
privadamente, como `antes.json` e `depois.json`, além dos horários, hash da
consulta e papel SQL utilizado. Não criar RPC nem ampliar permissões para
coletar. O catálogo e a visibilidade do papel devem ser verificados antes:
estabilidade de uma projeção não prova acesso a todos os dados nem segurança
das políticas RLS.

São selecionados apenas os campos de identidade e comparação necessários.
Nomes, telefone, descrição bancária, mensagens, XMLs, premissas de simulação e
credenciais não entram na exportação. Dimensões de componentes aceitam somente
texto curto ou nulo nas chaves explícitas do JSON de origem; estruturas
inesperadas são omitidas e sinalizadas, não convertidas em ausência comprovada.
Contas, identificadores e valores financeiros continuam privados.

Números de quantidade, peso, valor e versão são exportados como texto decimal
exato. Isso evita a perda de precisão na passagem pelo navegador. O analisador
usa Decimal sem arredondamento implícito; identidade textual nunca vira número.
Unidades: cabeças, kg e reais. Ausência não equivale a zero.

## Execução offline

```sh
python3 -B tools/diagnosticar_vinculos_negocios.py \
  --antes docs/privado/p1/antes.json \
  --depois docs/privado/p1/depois.json \
  --planilha-registros docs/privado/p1/planilha-entrada.json \
  --saida docs/privado/p1/resultado-novo
```

O diretório precisa ser novo, em `docs/privado/` ou área temporária. O comando
não tem caminho de rede nem opção de execução. Lê as exportações, exige IDs e
campos projetados completos, confere contagens, compara as duas fotografias e
verifica que os arquivos locais não mudaram durante a leitura. Saída: diretório
700, `analise.json` e `analise.md` com permissão 600. O terminal mostra apenas
contagens e assinaturas; detalhes por registro ficam exclusivamente nos arquivos.

A entrada opcional de planilha é uma projeção local rastreável, não uma
importação de negócios:

```json
{
  "fontes_inalteradas": true,
  "fontes": [{
    "id": "amostra_sintetica",
    "aba": "Itens",
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "linhas": [{"linha": 2, "codigo": "EXEMPLO-001", "sexo": null}],
    "leitura": {
      "formulas_sem_valor_armazenado": 0,
      "celulas_com_erro": 0,
      "aviso": "Exemplo fictício; não é uma fonte real."
    }
  }]
}
```

O extrator autorizado deve registrar arquivo, aba, intervalo, letras das colunas,
linhas e SHA-256 antes/depois. `fontes_inalteradas` é uma declaração do extrator:
o analisador verifica o JSON de entrada, não reabre por si só o Excel original.
O plano inclui as fontes e suas linhas; trocar o recorte muda sua identidade.
Sem projeção de planilha, a preservação da fonte fica como não verificada
(`null`), nunca como aprovação implícita. Durante a análise, os três arquivos
de entrada são relidos antes de salvar o resultado para detectar mudanças.
O leitor existente `perfilar_chaves_fontes.py` preserva células e caches, sem
executar fórmulas ou herdar silenciosamente mesclas. Valores armazenados podem
estar desatualizados. Erros em campos calculados não devem contaminar códigos
digitados válidos nem autorizar contas com zero inventado.

## Como interpretar

- **Código exato encontrado:** prova somente correspondência da referência.
  Múltiplas linhas podem ser componentes ou versões; não fundir negócios.
- **Código ausente ou divergente:** conferir a fonte e o escopo. Não corrigir
  caixa, espaços, prefixos ou zeros automaticamente.
- **Avaliação sem operação:** distinguir estudo, cancelamento e vínculo ausente.
  Avaliações canceladas permanecem históricas, sem pedido de promoção.
- **Componentes:** comparar soma dos filhos e total do pai separadamente.
  Divergência pode ser cobertura parcial. Não escolher um total por heurística
  nem somar pai e filhos no resultado econômico. Componentes descrevem
  fornecedores/corretores, não obrigatoriamente subgrupos de animais: ausência
  de sexo/categoria/destino requer avaliar a finalidade, não completar cadastros
  automaticamente. O total econômico continua vindo de `compras`.
- **Sexo, categoria e destino:** preservar divisões; mesma contraparte ou código
  não prova que dois grupos sejam o mesmo negócio. Um campo preenchido com
  “desconhecido” continua pendente e não caracteriza uma divisão distinta.
- **Banco:** conferir primeiro `transacao_banco_id`. Comparar conta, data e valor
  de um vínculo existente não autoriza criar vínculo para outro registro.
  `(conta, fitid)` do staging e `id_externo` da produção só podem ser relacionados
  após comprovar a regra do importador. Nulo não demonstra transação inexistente.
  Textos de conta diferentes podem ser aliases ou contas distintas: coincidência
  de FITID, data e valor não autoriza unir registros. Antes de corrigir qualquer
  consumidor que compare FITID isoladamente, acrescentar regressões para contas
  diferentes, aliases comprovados, importações repetidas e vínculos explícitos.

O aprofundamento de identidade bancária e origem dos arquivos segue
[rastreabilidade de OFX e componentes](rastreabilidade-ofx-componentes.md).
Esse perfilador é offline e não substitui o importador em produção.

Cada achado separa observação, hipótese, evidência disponível e próxima
verificação. A quantidade de achados não é quantidade de negócios errados:
um registro pode aparecer em vários campos/achados, e um achado pode agrupar
vários registros.

## Próximos gates e reversão

1. Validar leitura com amostra privada, revisão independente e regressões.
2. Conferir consumidores existentes e o mapeamento dos identificadores.
3. Preparar proposta por registro, preservando alternativas e histórico.
4. Somente com autorização específica, discutir escrita e reversão.

Não há alteração operacional para reverter nesta etapa. Remover relatórios
derivados não altera fontes. Não renumerar negócios, criar FKs, fundir registros,
conciliar pagamentos ou criar componentes para fazer as contagens coincidirem.

Testes sem Supabase:

```sh
python3 -B -m unittest tools.test_diagnosticar_vinculos_negocios tools.test_sql_vinculos_negocios
python3 -B tools/test_ecossistema.py
git diff --check
```
