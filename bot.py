import os
import discord
from discord.ext import commands, tasks
import asyncio
import json
import datetime
import math

# -----------------------------
# CONFIGURAÇÕES
# -----------------------------
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", 0))
PANEL_CHANNEL_ID = int(os.getenv("PANEL_CHANNEL_ID", 0))
BOT_OWNER = int(os.getenv("BOT_OWNER", 0))

DATA_PATH = "data"
RANKING_FILE = f"{DATA_PATH}/ranking.json"
TORNEIO_FILE = f"{DATA_PATH}/torneio.json"
HISTORICO_FILE = f"{DATA_PATH}/historico.json"

os.makedirs(DATA_PATH, exist_ok=True)

# -----------------------------
# UTILIDADES
# -----------------------------
def save_json(file, data):
    with open(file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

def load_json(file, default):
    if not os.path.exists(file):
        save_json(file, default)
        return default
    with open(file, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except:
            return default

# -----------------------------
# ESTADOS
# -----------------------------
ranking = load_json(RANKING_FILE, {"scores": {}, "torneio": {}, "__last_reset": None})
torneio_data = load_json(TORNEIO_FILE, {
    "active": False,
    "inscriptions_open": False,
    "players": [],
    "decklists": {},
    "round": 0,
    "rounds_target": None,
    "pairings": {},
    "results": {},
    "scores": {},
    "played": {},
    "byes": [],
    "finished": False
})
historico = load_json(HISTORICO_FILE, [])
fila = []
partidas_ativas = {}
mostrar_inscritos = True
PANEL_MESSAGE_ID = 0

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# Emojis
CHECK = "✅"
X = "❌"
YES = "➡️"
NO = "❌"

# -----------------------------
# PAINEL
# -----------------------------
async def atualizar_painel():
    global PANEL_MESSAGE_ID
    channel = bot.get_channel(PANEL_CHANNEL_ID)
    if not channel:
        print("Canal do painel não encontrado.")
        return

    fila_txt = "\n".join([f"<@{uid}>" for uid in fila]) or "Vazia"
    partidas_txt = "\n".join([f"<@{p['player1']}> vs <@{p['player2']}>" for p in partidas_ativas.values()]) or "Nenhuma"
    ultimas_txt = "\n".join([f"<@{h['winner']}> venceu <@{h['loser']}>" for h in historico[-3:]]) or "Nenhuma"
    inscritos_txt = "\n".join([f"<@{uid}>" for uid in torneio_data.get("players", [])]) if mostrar_inscritos else "Oculto"

    txt = (
        f"**Fila 1x1:**\n{fila_txt}\n\n"
        f"**Partidas em andamento:**\n{partidas_txt}\n\n"
        f"**Últimas 3 partidas:**\n{ultimas_txt}\n\n"
        f"**Inscritos Torneio:**\n{inscritos_txt}"
    )

    if PANEL_MESSAGE_ID == 0:
        msg = await channel.send("🟢 Painel inicializando...")
        PANEL_MESSAGE_ID = msg.id
    else:
        try:
            msg = await channel.fetch_message(PANEL_MESSAGE_ID)
            await msg.edit(content=txt)
        except:
            msg = await channel.send(txt)
            PANEL_MESSAGE_ID = msg.id

# -----------------------------
# LOOP DE SALVAR ESTADOS
# -----------------------------
@tasks.loop(minutes=5)
async def save_states():
    save_json(RANKING_FILE, ranking)
    save_json(TORNEIO_FILE, torneio_data)
    save_json(HISTORICO_FILE, historico)

# -----------------------------
# FUNÇÕES AUXILIARES
# -----------------------------
async def checar_fila_1x1():
    while True:
        if len(fila) >= 2:
            p1 = fila.pop(0)
            p2 = fila.pop(0)
            match_id = f"{p1}_{p2}_{int(datetime.datetime.now().timestamp())}"
            partidas_ativas[match_id] = {"player1": p1, "player2": p2, "result": None, "cancel_requested": False}
            try:
                await bot.get_user(p1).send(f"⚔️ Você foi emparelhado contra <@{p2}>! Envie o resultado via DM após a partida.")
                await bot.get_user(p2).send(f"⚔️ Você foi emparelhado contra <@{p1}>! Envie o resultado via DM após a partida.")
            except:
                pass
            await atualizar_painel()
        await asyncio.sleep(5)

# -----------------------------
# TORNEIO SUÍÇO - funções
# -----------------------------
def calcular_rodadas(num_jogadores):
    return max(1, math.ceil(math.log2(num_jogadores)))

async def gerar_pairings():
    jogadores = sorted(torneio_data["players"], key=lambda u: torneio_data["scores"].get(str(u),0), reverse=True)
    pairings = {}
    used = set()
    for i in range(0, len(jogadores)-1, 2):
        j1, j2 = jogadores[i], jogadores[i+1]
        pairings[f"{j1}_{j2}"] = {"player1": j1, "player2": j2, "result": None, "cancel_requested": False}
        used.add(j1)
        used.add(j2)
    if len(jogadores) %2 !=0:
        last = jogadores[-1]
        torneio_data["byes"].append(last)
        torneio_data["scores"][str(last)] = torneio_data["scores"].get(str(last),0)+1
    torneio_data["pairings"] = pairings
# -----------------------------
# EVENTOS
# -----------------------------
@bot.event
async def on_ready():
    print(f"Bot conectado como {bot.user}")
    await atualizar_painel()
    save_states.start()
    asyncio.create_task(checar_fila_1x1())

@bot.event
async def on_reaction_add(reaction, user):
    if user.bot: return
    if reaction.message.channel.id != PANEL_CHANNEL_ID: return

    if str(reaction.emoji) == CHECK:
        if user.id not in fila:
            fila.append(user.id)
            await user.send("✅ Você entrou na fila 1x1!")
            await atualizar_painel()
        await reaction.remove(user)
    elif str(reaction.emoji) == X:
        if user.id in fila:
            fila.remove(user.id)
            await user.send("❌ Você saiu da fila 1x1!")
            await atualizar_painel()
        await reaction.remove(user)

# -----------------------------
# COMANDOS
# -----------------------------
@bot.command()
async def novopainel(ctx):
    global PANEL_MESSAGE_ID
    PANEL_MESSAGE_ID = 0
    await atualizar_painel()
    await ctx.send("✅ Painel reiniciado!")

@bot.command()
async def fila(ctx):
    msg = await ctx.send("Reaja com ✅ para entrar na fila e ❌ para sair da fila.")
    await msg.add_reaction(CHECK)
    await msg.add_reaction(X)

@bot.command()
async def torneio(ctx):
    if ctx.author.id != BOT_OWNER:
        await ctx.send("Apenas o dono do bot pode abrir inscrições.")
        return
    torneio_data["inscriptions_open"] = True
    await ctx.send("✅ Torneio aberto para inscrições! Jogadores podem reagir para entrar.")

@bot.command()
async def começartorneio(ctx):
    if ctx.author.id != BOT_OWNER:
        await ctx.send("Apenas o dono do bot pode iniciar o torneio.")
        return
    if len(torneio_data["players"])<2:
        await ctx.send("❌ Não há jogadores suficientes.")
        return
    torneio_data["active"] = True
    torneio_data["rounds_target"] = calcular_rodadas(len(torneio_data["players"]))
    torneio_data["round"] = 1
    torneio_data["scores"] = {str(u):0 for u in torneio_data["players"]}
    await gerar_pairings()
    await ctx.send(f"🏆 Torneio iniciado com {len(torneio_data['players'])} jogadores, {torneio_data['rounds_target']} rodadas.")
    await atualizar_painel()

@bot.command()
async def statustorneio(ctx):
    if not torneio_data["active"]:
        await ctx.send("Nenhum torneio ativo.")
        return
    txt = f"Rodada {torneio_data['round']}/{torneio_data['rounds_target']}\nConfrontos:\n"
    for p in torneio_data["pairings"].values():
        txt+=f"<@{p['player1']}> vs <@{p['player2']}>\n"
    await ctx.send(txt)

# -----------------------------
# EXECUÇÃO
# -----------------------------
if not DISCORD_TOKEN:
    print("⚠️ DISCORD_TOKEN não encontrado nas variáveis de ambiente!")
else:
    bot.run(DISCORD_TOKEN)
