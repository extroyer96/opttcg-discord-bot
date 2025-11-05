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
