import discord
from discord.ext import tasks, commands
from discord import Intents
import json, os, datetime, pytz, asyncio, random
from aiohttp import web

# -----------------------------
# CONFIGURAÇÕES
# -----------------------------
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
PANEL_CHANNEL_ID = int(os.getenv("PANEL_CHANNEL_ID"))
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
PAINEL_FILE = os.path.join(DATA_DIR, "painel.json")

os.makedirs(DATA_DIR, exist_ok=True)

# -----------------------------
# CRIAÇÃO AUTOMÁTICA DE JSONS
# -----------------------------
json_defaults = {
    RANKING_FILE: {"scores": {}, "__last_reset": ""},
    TORNEIO_FILE: {
        "active": False,
        "signup_msg_id": None,
        "players": [],
        "decklists": {},
        "round": 0,
        "pairings": {},
        "results": {},
        "scores": {},
        "played": {},
        "byes": [],
        "finished": False,
        "rounds_target": None
    },
    HIST_FILE: [],
    PAINEL_FILE: {"message_id": 0}
}

for file_path, default_content in json_defaults.items():
    if not os.path.exists(file_path):
        with open(file_path, "w") as f:
            json.dump(default_content, f, indent=4)

# -----------------------------
# FUNÇÕES DE JSON
# -----------------------------
def load_json(file, default):
    try:
        with open(file, "r") as f:
            data = json.load(f)
            if isinstance(default, dict) and isinstance(data, dict):
                for k, v in default.items():
                    if k not in data:
                        data[k] = v
            elif type(data) != type(default):
                return default
            return data
    except:
        return default

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
    "active": False,
    "signup_msg_id": None,
    "players": [],
    "decklists": {},
    "round": 0,
    "pairings": {},
    "results": {},
    "scores": {},
    "played": {},
    "byes": [],
    "finished": False,
    "rounds_target": None
})
historico = load_json(HIST_FILE, [])
panel_data = load_json(PAINEL_FILE, {"message_id": 0})
PANEL_MESSAGE_ID = panel_data.get("message_id", 0)

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
        return "(vazia)"
    return ", ".join([f"<@{uid}>" for uid in fila])

def gerar_partidas_texto():
    if not partidas_ativas:
        return "Nenhuma"
    txt = ""
    for m in partidas_ativas.values():
        txt += f"<@{m['player1']}> vs <@{m['player2']}>\n"
    return txt

def gerar_historico_texto():
    if not historico:
        return "Nenhuma partida ainda"
    txt = ""
    for h in historico[-3:]:
        txt += f"<@{h['player1']}> vs <@{h['player2']}> → vencedor: <@{h['vencedor']}>\n"
    return txt

def save_panel_id(message_id):
    save_json(PAINEL_FILE, {"message_id": message_id})

# -----------------------------
# PAINEL COMPACTO
# -----------------------------
async def atualizar_painel():
    global PANEL_MESSAGE_ID
    channel = bot.get_channel(PANEL_CHANNEL_ID)
    if not channel:
        return

    if PANEL_MESSAGE_ID == 0:
        painel_msg = await channel.send("🎨 **Painel inicializando...**")
        PANEL_MESSAGE_ID = painel_msg.id
        save_panel_id(PANEL_MESSAGE_ID)
    else:
        try:
            painel_msg = await channel.fetch_message(PANEL_MESSAGE_ID)
        except discord.NotFound:
            painel_msg = await channel.send("🎨 **Painel inicializando...**")
            PANEL_MESSAGE_ID = painel_msg.id
            save_panel_id(PANEL_MESSAGE_ID)

    content = "🎮 **PAINEL OPTCG** 🎮\n"
    content += "──────────────────────────\n\n"
    content += "✅ **Fila 1x1:**\n" + gerar_fila_texto() + "\n\n"
    content += "⚔️ **Partidas em andamento:**\n" + gerar_partidas_texto() + "\n\n"
    content += "📜 **Últimas partidas:**\n" + gerar_historico_texto() + "\n\n"
    if torneio_data.get("active", False):
        content += "🏆 **Torneio ativo!**\nRodada atual: {}\nClique na reação 🏅 para se inscrever!\n\n".format(
            torneio_data.get("round", 0)
        )
    content += "💡 **Interaja com o painel:**\n"
    content += "✅ Entrar na fila\n❌ Sair da fila\n"
    content += "🏆 Ver ranking 1x1\n"
    if torneio_data.get("active", False):
        content += "🏅 Inscrever no torneio / ver ranking de torneios\n"
    content += "\n──────────────────────────"
    await painel_msg.edit(content=content)
    await adicionar_reacoes_painel()

# -----------------------------
# REAÇÕES INTERATIVAS
# -----------------------------
async def adicionar_reacoes_painel():
    global PANEL_MESSAGE_ID
    channel = bot.get_channel(PANEL_CHANNEL_ID)
    if not channel:
        return
    try:
        painel_msg = await channel.fetch_message(PANEL_MESSAGE_ID)
    except discord.NotFound:
        return

    reacoes_fixas = ["✅", "❌", "🏆"]
    if torneio_data.get("active", False):
        reacoes_fixas.append("🏅")

    for r in reacoes_fixas:
        if r not in [str(e.emoji) for e in painel_msg.reactions]:
            await painel_msg.add_reaction(r)

@bot.event
async def on_reaction_add(reaction, user):
    if user.bot:
        return

    # Painel
    if reaction.message.id == PANEL_MESSAGE_ID:
        emoji = str(reaction.emoji)
        if emoji == "✅":
            if user.id not in fila:
                fila.append(user.id)
            await reaction.message.remove_reaction(emoji, user)
            await atualizar_painel()
        elif emoji == "❌":
            if user.id in fila:
                fila.remove(user.id)
            await reaction.message.remove_reaction(emoji, user)
            await atualizar_painel()
        elif emoji == "🏆":
            await reaction.message.remove_reaction(emoji, user)
            txt = gerar_ranking_texto()
            try:
                msg = await user.send(f"📊 **Ranking 1x1:**\n{txt}\nDeseja ver ranking de torneios?")
                await msg.add_reaction("⬅️")
                await msg.add_reaction("❌")
            except:
                pass
        elif emoji == "🏅" and torneio_data.get("active", False):
            if user.id not in torneio_data["players"]:
                torneio_data["players"].append(user.id)
            await reaction.message.remove_reaction(emoji, user)
            save_json(TORNEIO_FILE, torneio_data)
            await atualizar_painel()

    # DM ranking de torneio
    elif str(reaction.emoji) in ["⬅️", "❌"]:
        try:
            await reaction.message.remove_reaction(reaction.emoji, user)
        except:
            pass

        if str(reaction.emoji) == "⬅️":
            txt_torneio = gerar_ranking_torneio_texto()
            try:
                await user.send(f"📊 **Ranking de Torneios:**\n{txt_torneio}")
            except:
                pass
        # ❌ apenas encerra

# -----------------------------
# SERVER DUMMY
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
    await asyncio.sleep(2)
    check_reset_ranking.start()
    save_states.start()
    loop = asyncio.get_event_loop()
    loop.create_task(run_web_server())
    await atualizar_painel()

# -----------------------------
# TASKS
# -----------------------------
@tasks.loop(hours=1)
async def check_reset_ranking():
    now = datetime.datetime.now(pytz.timezone("America/Sao_Paulo"))
    if now.day == 1 and ranking.get("__last_reset") != now.strftime("%Y-%m-%d"):
        ranking["scores"] = {}
        ranking["__last_reset"] = now.strftime("%Y-%m-%d")
        save_json(RANKING_FILE, ranking)

@tasks.loop(seconds=30)
async def save_states():
    save_json(RANKING_FILE, ranking)
    save_json(TORNEIO_FILE, torneio_data)
    save_json(HIST_FILE, historico)
    save_panel_id(PANEL_MESSAGE_ID)

# -----------------------------
# EXECUÇÃO
# -----------------------------
if __name__ == "__main__":
    try:
        bot.run(DISCORD_TOKEN)
    except Exception as e:
        print(f"Erro crítico ao iniciar o bot: {e}")
