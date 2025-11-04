import discord
from discord.ext import tasks, commands
from discord import Intents
import json, os, datetime, pytz
from colorama import Fore, Style, init
import asyncio
from aiohttp import web

init(autoreset=True)

# -----------------------------
# CONFIGURAÇÕES
# -----------------------------
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
PANEL_CHANNEL_ID = int(os.getenv("PANEL_CHANNEL_ID"))
PANEL_MESSAGE_ID = int(os.getenv("PANEL_MESSAGE_ID", 0))
BOT_OWNER = int(os.getenv("BOT_OWNER"))

intents = Intents.default()
intents.members = True
intents.message_content = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents)

# -----------------------------
# ARQUIVOS DE DADOS
# -----------------------------
DATA_DIR = "data"
RANKING_FILE = os.path.join(DATA_DIR, "ranking.json")
TORNEIO_FILE = os.path.join(DATA_DIR, "torneios.json")
HIST_FILE = os.path.join(DATA_DIR, "historico.json")

os.makedirs(DATA_DIR, exist_ok=True)

def load_json(file, default):
    if not os.path.exists(file):
        with open(file, "w") as f:
            json.dump(default, f, indent=4)
    with open(file, "r") as f:
        return json.load(f)

def save_json(file, data):
    with open(file, "w") as f:
        json.dump(data, f, indent=4)

# -----------------------------
# ESTADOS
# -----------------------------
fila = []
ranking = load_json(RANKING_FILE, {"scores": {}, "__last_reset": ""})
torneio = load_json(TORNEIO_FILE, {
    "active": False, "signup_msg_id": None, "players": [], "decklists": {},
    "round": 0, "pairings": {}, "results": {}, "scores": {}, "played": {},
    "byes": [], "finished": False, "rounds_target": None
})
historico = load_json(HIST_FILE, [])

# -----------------------------
# BANNER INICIAL
# -----------------------------
print(Fore.MAGENTA + "="*40)
print(Fore.CYAN + "🃏 OPTCG Discord Bot - Iniciado")
print(Fore.GREEN + "🟢 Status: Online")
print(Fore.YELLOW + f"👑 Dono: {BOT_OWNER}")
print(Fore.MAGENTA + "🏆 Módulos: Fila | Ranking | Torneio | Cancelar")
print(Fore.MAGENTA + "="*40)

# -----------------------------
# FUNÇÕES AUXILIARES
# -----------------------------
def gerar_ranking_texto():
    txt = ""
    sorted_rank = sorted(ranking.get("scores", {}).items(), key=lambda x: x[1], reverse=True)
    for i, (uid, pts) in enumerate(sorted_rank, 1):
        txt += f"{i}. <@{uid}> - {pts} vitórias\n"
    return txt if txt else "Nenhum registro ainda."

def registrar_partida(u1, u2, vencedor_id):
    global historico
    historico.append({
        "timestamp": datetime.datetime.now().isoformat(),
        "player1": u1,
        "player2": u2,
        "vencedor": vencedor_id
    })
    if len(historico) > 100:  # limitar histórico
        historico = historico[-100:]
    save_json(HIST_FILE, historico)

def salvar_ranking(uid, pontos=1):
    if "scores" not in ranking:
        ranking["scores"] = {}
    ranking["scores"][str(uid)] = ranking["scores"].get(str(uid), 0) + pontos
    save_json(RANKING_FILE, ranking)

# -----------------------------
# REAÇÕES AUTOMÁTICAS
# -----------------------------
@bot.event
async def on_raw_reaction_add(payload):
    if payload.user_id == bot.user.id:
        return
    if payload.channel_id != PANEL_CHANNEL_ID:
        return

    guild = bot.get_guild(payload.guild_id)
    member = guild.get_member(payload.user_id)
    channel = guild.get_channel(payload.channel_id)
    message = await channel.fetch_message(payload.message_id)
    emoji = str(payload.emoji)

    # Fila 1x1
    if emoji == "🟢":
        if member.id not in fila:
            fila.append(member.id)
            await member.send("✅ Você entrou na fila!")
        else:
            await member.send("⚠️ Já está na fila!")
    elif emoji == "🔴":
        if member.id in fila:
            fila.remove(member.id)
            await member.send("❌ Você saiu da fila!")
        else:
            await member.send("⚠️ Você não estava na fila!")

    # Torneio
    elif emoji == "🏅" and torneio["active"]:
        if member.id not in torneio["players"]:
            torneio["players"].append(member.id)
            await member.send(
                "🎴 Você se inscreveu no torneio!\n"
                "Por favor, envie sua decklist via DM:\n"
                "1. Abra 'Deck Editor'\n"
                "2. Copie o deck\n"
                "3. Cole aqui."
            )
        else:
            await member.send("⚠️ Já está inscrito no torneio!")

    # Ranking
    elif emoji == "🏆":
        ranking_text = gerar_ranking_texto()
        await member.send(f"🏅 Ranking atual:\n{ranking_text}")

    # Remove a reação
    for react in message.reactions:
        if str(react.emoji) == emoji:
            await react.remove(member)

# -----------------------------
# RESET MENSAL AUTOMÁTICO
# -----------------------------
@tasks.loop(hours=1)
async def check_reset_ranking():
    now = datetime.datetime.now(pytz.timezone("America/Sao_Paulo"))
    if now.day == 1 and ranking.get("__last_reset") != now.strftime("%Y-%m-%d"):
        ranking["scores"] = {}
        ranking["__last_reset"] = now.strftime("%Y-%m-%d")
        save_json(RANKING_FILE, ranking)
        print(Fore.YELLOW + "[RANKING] Reset mensal realizado.")

# -----------------------------
# COMANDOS ADMIN
# -----------------------------
@bot.command()
async def reset_ranking(ctx):
    if ctx.author.id != BOT_OWNER:
        await ctx.send("❌ Apenas o dono pode resetar o ranking!")
        return
    ranking["scores"] = {}
    save_json(RANKING_FILE, ranking)
    await ctx.send("✅ Ranking resetado manualmente.")

@bot.command()
async def cancelar_torneio(ctx):
    if ctx.author.id != BOT_OWNER:
        await ctx.send("❌ Apenas o dono pode cancelar torneios!")
        return
    torneio["active"] = False
    torneio["players"] = []
    torneio["decklists"] = {}
    torneio["round"] = 0
    save_json(TORNEIO_FILE, torneio)
    await ctx.send("❌ Torneio cancelado. Nenhum campeão registrado.")

# -----------------------------
# SERVIDOR HTTP DUMMY PARA RENDER
# -----------------------------
async def _health(request):
    return web.Response(text="OPTCG bot alive")

async def run_web_server():
    port = int(os.environ.get("PORT", 8000))
    app = web.Application()
    app.router.add_get("/", _health)
    app.router.add_get("/health", _health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"🌐 Servidor HTTP dummy iniciado na porta {port} (Render)")

# -----------------------------
# INICIALIZAÇÃO
# -----------------------------
@bot.event
async def on_ready():
    print(Fore.GREEN + f"Bot conectado como {bot.user}")
    # Start tasks dentro do loop correto
    check_reset_ranking.start()
    save_states.start()
    # Inicia o servidor HTTP dummy
    loop = asyncio.get_event_loop()
    loop.create_task(run_web_server())

# -----------------------------
# SALVAR ESTADOS PERIODICAMENTE
# -----------------------------
@tasks.loop(seconds=30)
async def save_states():
    save_json(RANKING_FILE, ranking)
    save_json(TORNEIO_FILE, torneio)
    save_json(HIST_FILE, historico)

# -----------------------------
# RODAR BOT
# -----------------------------
bot.run(DISCORD_TOKEN)
