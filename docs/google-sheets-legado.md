# Google Sheets — compatibilidade legada

Status: **mantido como legado durante a transição; não migrado nem removido**.

O inventário somente leitura do Drive confirmou planilhas de confinamento em
formatos Google Sheets e Excel. Nenhum arquivo foi aberto, movido,
compartilhado, renomeado ou alterado.

## Fonte de verdade

- Dados operacionais confirmados: Supabase.
- Simulação local e recuperação offline: `localStorage`.
- Bases reutilizáveis de confinamento entre aparelhos: `confinex_bases` no
  Supabase, com a migração aplicada em 12/08/2026.
- Sheets/Apps Script: compatibilidade entre dispositivos e histórico antigo.
- Código: GitHub; a pasta de código no Drive é somente um espelho antigo.

Enquanto a migração não for autorizada e homologada, o Apps Script deve
permanecer ativo. Abrir o Confinex não consulta nem grava essa cópia em segundo
plano. A conexão ocorre somente quando a pessoa escolhe **Carregar cópia**,
**Salvar cópia** ou uma ação de versões dentro da seção recolhida **Cópia
online e segurança**. Depois dessa escolha explícita, a sessão mantém as
proteções de conflito por horário, versões nomeadas e restauração local.

## Gate para uma migração futura

Uma migração só pode começar com autorização específica e precisa:

1. inventariar versões e assinaturas sem alterar os arquivos;
2. definir o modelo correspondente no Supabase;
3. comparar Sheets, Supabase e `localStorage`;
4. testar conflito entre dispositivos, restauração e histórico;
5. migrar uma cópia controlada;
6. homologar antes de desativar qualquer Apps Script;
7. preservar exportação e reversão.

Até esse gate ser cumprido, Sheets é formalmente **legado em uso**, não uma
fonte operacional paralela a ser atualizada por agentes.

O catálogo de bases é uma migração independente e menor: ele não move o estudo
completo nem desativa o Apps Script. A primeira cópia das bases que já existem
em um navegador continua exigindo o clique em **Sincronizar bases**. Em um
computador novo, após entrar no ecossistema, essas bases são apenas lidas e
mescladas ao cache local; o estudo aberto não é substituído.
