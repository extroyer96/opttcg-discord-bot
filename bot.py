import discord
from discord.ext import tasks, commands
from discord import Intents
import json, os, datetime, pytz
import asyncio
from aiohttp import web

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
partidas_ativas = {}
ranking = load_json(RANKING_FILE, {"scores": {}, "__last_reset": ""})
torneio_data = load_json(TORNEIO_FILE, {
    "active": False, "signup_msg_id": None, "players": [], "decklists": {},
    "round": 0, "pairings": {}, "results": {}, "scores": {}, "played": {},
    "byes": [], "finished": False, "rounds_target": None
})
historico = load_json(HIST_FILE, [])

# -----------------------------
# FUNÇÕES AUXILIARES
# -----------------------------
def gerar_ranking_texto():
    txt = ""
    sorted_rank = sorted(ranking.get("scores", {}).items(), key=lambda x: x[1], reverse=True)
    for i, (uid, pts) in enumerate(sorted_rank, 1):
        txt += f"{i}. <@{uid}> - {pts} vitórias\n"
    return txt if txt else "Nenhum registro ainda."

def gerar_ranking_torneio_texto():
    scores = torneio_data.get("scores", {})
    if not scores:
        return "🏅 Nenhum campeão registrado ainda."
    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    txt = "🏅 Ranking de Torneios:\n"
    for i, (uid, wins) in enumerate(sorted_scores, 1):
        txt += f"{i}. <@{uid}> - {wins} campeonato(s)\n"
    return txt

def registrar_partida(u1, u2, vencedor_id):
    global historico
    historico.append({
        "timestamp": datetime.datetime.now().isoformat(),
        "player1": u1,
        "player2": u2,
        "vencedor": vencedor_id
    })
    if len(historico) > 100:
        historico = historico[-100:]
    save_json(HIST_FILE, historico)

def salvar_ranking(uid, pontos=1):
    if "scores" not in ranking:
        ranking["scores"] = {}
    ranking["scores"][str(uid)] = ranking["scores"].get(str(uid), 0) + pontos
    save_json(RANKING_FILE, ranking)

def gerar_fila_texto():
    if not fila:
        return "Fila atual: (vazia)"
    return "Fila atual: " + ", ".join([f"<@{uid}>" for uid in fila])

def gerar_partidas_texto():
    if not partidas_ativas:
        return "Partidas em andamento: nenhuma"
    txt = "Partidas em andamento:\n"
    for m in partidas_ativas.values():
        txt += f"<@{m['player1']}> vs <@{m['player2']}>\n"
    return txt

def gerar_historico_texto():
    if not historico:
        return "Histórico: nenhuma partida ainda"
    txt = "Últimas 3 partidas:\n"
    for h in historico[-3:]:
        txt += f"<@{h['player1']}> vs <@{h['player2']}> → vencedor: <@{h['vencedor']}>\n"
    return txt

async def atualizar_painel():
    global PANEL_MESSAGE_ID
    channel = bot.get_channel(PANEL_CHANNEL_ID)
    if not channel:
        print("❌ Canal do painel não encontrado!")
        return

    if PANEL_MESSAGE_ID == 0:
        painel_msg = await channel.send("Painel inicializando...")
        PANEL_MESSAGE_ID = painel_msg.id
    else:
        try:
            painel_msg = await channel.fetch_message(PANEL_MESSAGE_ID)
        except discord.NotFound:
            painel_msg = await channel.send("Painel inicializando...")
            PANEL_MESSAGE_ID = painel_msg.id

    content = "🎮 **Painel OPTCG**\n\n"
    content += gerar_fila_texto() + "\n"
    content += gerar_partidas_texto() + "\n"
    content += gerar_historico_texto() + "\n\n"
    content += "Reaja para interagir:\n"
    content += "🟢 Entrar na fila 1x1\n"
    content += "🔴 Sair da fila 1x1\n"
    content += "🏆 Ver ranking 1x1\n"
    if torneio_data["active"]:
        content += "🏅 Inscrever no torneio / ver ranking de torneios\n"

    await painel_msg.edit(content=content)

# -----------------------------
# INTERAÇÃO DM RANKING
# -----------------------------
async def enviar_ranking_1x1(member):
    ranking_text = gerar_ranking_texto()
    dm_msg = await member.send(f"🏆 Ranking 1x1 atual:\n{ranking_text}\n\nDeseja também ver o ranking de campeões de torneio?")

    await dm_msg.add_reaction("✅")
    await dm_msg.add_reaction("❌")

    def check(reaction, user):
        return user == member and str(reaction.emoji) in ["✅", "❌"] and reaction.message.id == dm_msg.id

    try:
        reaction, user = await bot.wait_for('reaction_add', timeout=60.0, check=check)
        if str(reaction.emoji) == "✅":
            ranking_torneio_text = gerar_ranking_torneio_texto()
            await member.send(ranking_torneio_text)
        else:
            await member.send("👍 Ok, exibindo apenas ranking 1x1.")
    except asyncio.TimeoutError:
        await member.send("⏱ Tempo esgotado. Não será exibido ranking de torneio.")

# -----------------------------
# EVENTO DE REAÇÕES
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

    global fila, partidas_ativas

    if emoji == "🟢":
        if member.id not in fila:
            fila.append(member.id)
            await member.send("✅ Você entrou na fila!")
            await atualizar_painel()
            if len(fila) >= 2:
                p1 = fila.pop(0)
                p2 = fila.pop(0)
                partidas_ativas[f"{p1}_{p2}"] = {"player1": p1, "player2": p2}
                u1 = guild.get_member(p1)
                u2 = guild.get_member(p2)
                await u1.send(f"🎮 Você foi emparelhado com <@{p2}>!")
                await u2.send(f"🎮 Você foi emparelhado com <@{p1}>!")
                await atualizar_painel()
    elif emoji == "🔴":
        if member.id in fila:
            fila.remove(member.id)
            await member.send("❌ Você saiu da fila!")
            await atualizar_painel()
    elif emoji == "🏆":
        await enviar_ranking_1x1(member)
    elif emoji == "🏅" and torneio_data["active"]:
        if member.id not in torneio_data["players"]:
            torneio_data["players"].append(member.id)
            await member.send(
                "🎴 Você se inscreveu no torneio!\n"
                "Por favor, envie sua decklist via DM:\n"
                "1. Abra 'Deck Editor'\n"
                "2. Copie o deck\n"
                "3. Cole aqui."
            )

    # remove reação
    for react in message.reactions:
        if str(react.emoji) == emoji:
            await react.remove(member)

# -----------------------------
# COMANDOS ADMIN
# -----------------------------
@bot.command()
async def reset_ranking(ctx):
    if ctx.author.id != BOT_OWNER:
        await ctx.send("❌ Apenas o dono pode usar este comando.")
        return
    ranking["scores"] = {}
    save_json(RANKING_FILE, ranking)
    await ctx.send("✅ Ranking resetado.")

@bot.command()
async def cancelar_torneio(ctx):
    if ctx.author.id != BOT_OWNER:
        await ctx.send("❌ Apenas o dono pode usar este comando.")
        return
    torneio_data["active"] = False
    torneio_data["finished"] = True
    torneio_data["players"] = []
    save_json(TORNEIO_FILE, torneio_data)
    await ctx.send("❌ Torneio cancelado. Nenhum campeão registrado.")
    await atualizar_painel()

@bot.command()
async def torneio_cmd(ctx, action: str = None):
    if ctx.author.id != BOT_OWNER:
        await ctx.send("❌ Apenas o dono pode usar este comando.")
        return
    if action == "on":
        torneio_data["active"] = True
        await ctx.send("✅ Torneio habilitado!")
    elif action == "off":
        torneio_data["active"] = False
        await ctx.send("❌ Torneio desabilitado!")
    await atualizar_painel()

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
        print("[RANKING] Reset mensal realizado.")

# -----------------------------
# SALVAR ESTADOS PERIODICAMENTE
# -----------------------------
@tasks.loop(seconds=30)
async def save_states():
    save_json(RANKING_FILE, ranking)
    save_json(TORNEIO_FILE, torneio_data)
    save_json(HIST_FILE, historico)

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
# ON READY
# -----------------------------
@bot.event
async def on_ready():
    print(f"Bot conectado como {bot.user}")
    await asyncio.sleep(2)  # espera membros e canais carregarem
    check_reset_ranking.start()
    save_states.start()
    loop = asyncio.get_event_loop()
    loop.create_task(run_web_server())
    await atualizar_painel()

# -----------------------------
# RODAR BOT
# -----------------------------
bot.run(DISCORD_TOKEN)
