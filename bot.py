import os
import discord
from discord.ext import commands, tasks
import asyncio, json, datetime

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
ranking = load_json(RANKING_FILE, {"scores": {}, "__last_reset": None})
torneio_data = load_json(TORNEIO_FILE, {"active": False,"players":[],"decklists":{},"round":0,"pairings":{},"results":{},"scores":{},"played":{},"byes":[],"finished":False,"rounds_target":None,"inscriptions_open":False})
historico = load_json(HISTORICO_FILE, [])
fila = []
partidas_ativas = {}
mostrar_inscritos = True
PANEL_MESSAGE_ID = 0

bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())

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
# EVENTOS
# -----------------------------
@bot.event
async def on_ready():
    print(f"Bot conectado como {bot.user}")
    await atualizar_painel()
    save_states.start()
    asyncio.create_task(checar_fila_1x1())  # Loop de fila 1x1

# -----------------------------
# COMANDOS BÁSICOS
# -----------------------------
@bot.command()
async def novopainel(ctx):
    global PANEL_MESSAGE_ID
    PANEL_MESSAGE_ID = 0
    await atualizar_painel()
    await ctx.send("✅ Painel reiniciado!")

# -----------------------------
# TODO: implementar toda lógica de:
# - Fila 1x1
# - Torneio suíço
# - Solicitação de decklist via DM
# - Cancelamento de partidas
# - Ranking de torneios
# - Resultados com confirmação via DM
# -----------------------------

# -----------------------------
# EXECUÇÃO
# -----------------------------
if not DISCORD_TOKEN:
    print("⚠️ DISCORD_TOKEN não encontrado nas variáveis de ambiente!")
else:
    bot.run(DISCORD_TOKEN)
