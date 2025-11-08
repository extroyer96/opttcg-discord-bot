# 🔥 OPTCG Sorocaba — Bot de Torneios & Fila 1x1 para Discord

Este bot foi desenvolvido para gerenciamento de **torneios suíços**, **fila 1x1 automática**, **coleta de decklists**, **painel ao vivo**, **reporte de resultados por DM** e **organização de partidas com código de sala** para o jogo **One Piece Card Game**.

Ele é totalmente configurável e funciona em hospedagens gratuitas como **Render + UptimeRobot**.

---

## 🚀 Funcionalidades

### 🎮 Fila 1x1 Automática
- Entrar e sair da fila via **reação**.
- Quando houver dois jogadores → emparelha automaticamente.
- Jogadores recebem **DM** informando o oponente.
- Um dos jogadores é **sorteado para criar a sala** e informar o **código**.
- Código é encaminhado automaticamente ao adversário.
- Resultado é reportado via **reação** na DM.

---

### 🏆 Torneio Suíço (Automatizado)
- Inscrição via reação.
- Confirmação de participação via DM.
- Solicitação e verificação de **decklist obrigatória** (51 cartas).
- Decklist só é aceita após confirmação via **reação "✅ sim" ou "❌ não"**.
- Torneio só inicia após **todos confirmarem decklist**.
- Emparelhamento automático por pontuação.
- Byes automáticos.
- Reporte de resultado via DM com reação.
- Possibilidade de **cancelamento de partida** se ambos concordarem.
- Ao finalizar → envia **arquivo com todas as decklists** para o administrador.

---

### 📊 Rankings e Painel ao Vivo
- Ranking 1x1 e ranking de torneio separados.
- Reset automático mensal + reset manual por comandos.
- Painel no Discord mostra:
  - Fila 1x1
  - Partidas em andamento
  - Últimos resultados
  - Inscritos no torneio (com opção para **ocultar/mostrar**)
- Reação no painel para **ver ranking via DM**.

---

## 🧠 Comandos

| Comando | Função |
|--------|--------|
| `!novopainel` | Reinicia o painel e remove painéis antigos |
| `!torneio` | Abre inscrições |
| `!fecharinscricoes` | Fecha inscrições |
| `!começartorneio` | Inicia o torneio (aguarda decklists) |
| `!statustorneio` | Mostra confrontos da rodada |
| `!proximarodada` | Avança para próxima rodada |
| `!cancelartorneio` | **Cancela o torneio instantaneamente** |
| `!encerrar` | **Finaliza o torneio e declara campeão no estado atual** |
| `!resetranking` | Reseta ranking 1x1 |
| `!torneiorankreset` | Reseta ranking do torneio |
| `!ff` | Jogador abandona o torneio |

Feito com ❤️ para a comunidade OPTCG Sorocaba.
