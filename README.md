# OPTTCG Discord Bot 🎮  
Sistema completo de gerenciamento para competições de One Piece TCG no Discord!

Este bot automatiza **fila 1x1**, **torneios suíços**, **coleta e envio de decklists**, **ranking automático**, **painel ao vivo**, **histórico de partidas**, cancelamentos, confirmações via reação e muito mais.

---

## ✨ Funcionalidades

| Sistema | Descrição |
|--------|-----------|
| 🎮 Fila 1x1 | Jogadores entram via reação ✅ e são pareados automaticamente |
| ⚔️ Partidas Automáticas | Ambos jogadores recebem DM perguntando resultados |
| ✅ Confirmação Mútua | Resultado só é validado quando os dois confirmam |
| ❌ Cancelamento de Partida | Só acontece se **ambos** concordarem |
| 🏆 Torneio Suíço Automático | Emparelhamento, byes, pontuação, rodadas |
| 📥 Coleta de Decklist | Jogador envia por DM e o bot salva tudo em `.txt` |
| 📦 Envio de Todas Decklists ao Dono | Em um único arquivo `.txt` |
| 📊 Ranking Automático | Rankings separados: **1x1** e **Campeões de Torneios** |
| 🔄 Reset Mensal Automático do Ranking 1x1 | Todo mês no dia 1 |
| 🖥️ Painel ao Vivo | Atualizado automaticamente no canal configurado |
| 📨 DM Inteligente | Confirmando ações, avisando turnos e entrega de confrontos |

---

## 🛠 Requisitos

- Python 3.10 ou superior
- Biblioteca `discord.py 2.4+`

---

## 📦 Instalação

### 1. Clone o repositório
```bash
git clone https://github.com/seuusuario/opttcg-discord-bot
cd opttcg-discord-bot

### 2. Instale dependências
pip install -r requirements.txt

### 3. Crie arquivo .env (local)
DISCORD_TOKEN=SEU_TOKEN_AQUI
GUILD_ID=ID_DO_SERVIDOR
PANEL_CHANNEL_ID=ID_DO_CANAL_DO_PAINEL
BOT_OWNER=ID_DO_DONO_DO_BOT
PORT=10000

## ☁️ Deploy Grátis no Render

Suba o repositório para o GitHub

Vá em https://render.com
 → New Web Service

Conecte o repositório

Configure:

Campo	Valor
Build Command	pip install -r requirements.txt
Start Command	python bot.py

Adicione as variáveis ambiente citadas acima

## 💡 Dica: no Discord Developer Portal → Bot → Privileged Gateway Intents
Ativar:

✅ PRESENCE INTENT

✅ SERVER MEMBERS INTENT

✅ MESSAGE CONTENT INTENT

## 🚀 Uso
Entrar e sair da fila 1x1

Reaja no painel com:

✅ para entrar

❌ para sair

Comandos Principais
Comando	Uso
!torneio	Abre inscrições
!fecharinscricoes	Fecha inscrições
!começartorneio	Inicia o torneio
!proximarodada	Avança para próxima rodada
!statustorneio	Mostra confrontos atuais
!ff	Abandonar o torneio
!reportar <match_id> <vitoria/derrota/empate>	Reportar resultado
!cancelarpartida <match_id>	Solicitar cancelamento
!verranking	Recebe ranking via DM

## 📂 Estrutura importante do projeto
data/
 ┣ decklists/         # Arquivos .txt individuais por jogador
 ┣ ranking.json       # Ranking 1x1 e Torneios
 ┣ torneio.json       # Estado do torneio
 ┗ historico.json     # Histórico das partidas

## ❤️ Suporte / Contribuição

Sinta-se à vontade para:

Reportar bugs

Sugerir melhorias

Contribuir com PRs

Feito com ⚡ dedicação para a comunidade OPTTCG.
