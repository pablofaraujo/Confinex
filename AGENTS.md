# AGENTS.md

> **Este arquivo existe para o Codex (e outros agentes) lerem o mesmo contexto que o Claude Code.**
> A fonte da verdade é o **`CLAUDE.md`** na raiz deste repositório — **leia-o antes de qualquer tarefa.**

## Leitura obrigatória, nesta ordem

1. **`CLAUDE.md`** — visão geral dos apps, backend, deploy, convenções, Design System e armadilhas conhecidas
2. **`docs/arquitetura.md`** — apps, backend, deploy, convenções e dívidas técnicas
3. **`docs/regras-de-negocio.md`** — fórmulas e regras de cálculo (arrobas, capim, frete, GMD, Funrural, B3, VP)
4. **`DESIGN.md` + `design/`** — Design System (obrigatório antes de criar qualquer tela/componente)
5. `docs/historico.md` — evolução do projeto
6. `docs/privado/` — contexto de negócio, infraestrutura e pendências (**gitignored**, só local)

## Regras de trabalho (valem para TODOS os agentes)

1. **`git pull` antes de começar. `git push` ao terminar.** A fonte canônica do código é o repositório GitHub (HEAD) — nunca uma pasta espelho em nuvem (Drive/iCloud), nunca a memória de um chat.
2. **Nunca trabalhe no espelho do Google Drive.** Git dentro de pasta sincronizada corrompe e diverge. O clone de trabalho é `~/dev/Confinex`.
3. **Design System obrigatório**: antes de criar tela, componente ou estilo, verifique se já existe equivalente (`DESIGN.md`, `design/`, `js/cfagro-*.js`). Se existir, reutilize. Se não existir, crie **no Design System** e documente — nunca na página.
4. **Mudou arquitetura? Atualize o `CLAUDE.md` no mesmo commit.** A documentação envelhece junto com o código, não depois.
5. **Uma tarefa = um agente por vez** no mesmo arquivo. Não deixe Codex e Claude Code editando o mesmo arquivo simultaneamente.
6. **Este repositório é PÚBLICO.** Nada de dados financeiros, nomes de sócios, chaves ou infraestrutura fora de `docs/privado/` (que é gitignored). Segredos vivem no 1Password.
7. **`git add` explícito** dos arquivos que você mudou — nunca `git add -A`.
8. Tudo em **pt-BR**: código, variáveis, UI, commits.

## Onde mora cada coisa (fontes canônicas)

| Tipo | Fonte da verdade |
|---|---|
| Código / apps | **repositório GitHub** (`main`, HEAD) — clone em `~/dev/Confinex` |
| Dados operacionais | **Supabase** `fkmdzwjmjlmxqotznvgq` (query ao vivo) |
| Contexto / arquitetura | **`CLAUDE.md` + `docs/`** neste repo |
| Pendências de negócio/infra | **`docs/privado/pendencias.md`** (local, gitignored) |
| Segredos | **1Password** (cofres `OpenClaw - *`) |
| Documentos de negócio (PDFs, planilhas) | Google Drive → `ClaudeCoWork/<tema>` |

## Divisão de papéis entre agentes

- **Cowork (Claude no app)** — estratégia, análise, orquestração, despacho de missões para a VPS. *Não lê `CLAUDE.md` automaticamente — deve lê-lo no início de toda sessão sobre este projeto.*
- **Claude Code / Codex** — execução no clone `~/dev/Confinex` (um de cada vez por arquivo).
- **Juan (Telegram)** — operação do dia a dia (compras, vendas, pendências) gravando no Supabase.
- **Ponte (`vps_briefings`)** — trabalho autônomo de fundo na VPS.
