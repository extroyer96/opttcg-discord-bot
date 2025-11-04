import discord
from discord.ext import commands, tasks
from discord import Intents
import asyncio, json, os, datetime, pytz, random
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

# Cria arquivos caso não existam
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
# FUNÇÕES AUXILIARES
# -----------------------------
def load_json(file, default):
    try:
        with open(file, "r") as f:
            data = json.load(f)
            return data
    except:
        return default

def save_json(file, data):
    with open(file, "w") as f:
        json.dump(data, f, indent=4)

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
# FUNÇÕES DE RANKING E HISTÓRICO
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
    ranking["scores"][str(uid)] = ranking.get("scores", {}).get(str(uid), 0) + pontos
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
        try:
            if r not in [str(e.emoji) for e in painel_msg.reactions]:
                await painel_msg.add_reaction(r)
                await asyncio.sleep(0.2)  # delay para evitar rate limit
        except discord.errors.HTTPException:
            pass

# -----------------------------
# REAÇÕES INTERATIVAS
# -----------------------------
async def atualizar_painel_delay():
    await asyncio.sleep(1)  # agrupa reações
    try:
        await atualizar_painel()
    except discord.errors.HTTPException:
        pass

@bot.event
async def on_reaction_add(reaction, user):
    if user.bot:
        return

    if reaction.message.id == PANEL_MESSAGE_ID:
        emoji = str(reaction.emoji)
        try:
            if emoji == "✅":
                if user.id not in fila:
                    fila.append(user.id)
            elif emoji == "❌":
                if user.id in fila:
                    fila.remove(user.id)
            elif emoji == "🏆":
                txt = gerar_ranking_texto()
                msg = await user.send(f"📊 **Ranking 1x1:**\n{txt}\nDeseja ver ranking de torneios?")
                await msg.add_reaction("⬅️")
                await msg.add_reaction("❌")
            elif emoji == "🏅" and torneio_data.get("active", False):
                if user.id not in torneio_data["players"]:
                    torneio_data["players"].append(user.id)
                    save_json(TORNEIO_FILE, torneio_data)

                    # DM para jogador
                    try:
                        await user.send(
                            f"🏅 Você foi inscrito no torneio suíço!\n"
                            "Aguarde o início do torneio. Você será notificado via DM quando a primeira rodada começar."
                        )
                    except:
                        pass

                    # DM para dono
                    owner = await bot.fetch_user(BOT_OWNER)
                    inscritos_txt = "\n".join([f"<@{pid}>" for pid in torneio_data["players"]])
                    try:
                        msg_owner = await owner.send(
                            f"📋 **Jogadores inscritos no torneio:**\n{inscritos_txt}\n\n"
                            "Deseja iniciar o torneio agora?"
                        )
                        await msg_owner.add_reaction("✅")
                        await msg_owner.add_reaction("❌")

                        def check(reaction, u):
                            return u.id == BOT_OWNER and str(reaction.emoji) in ["✅","❌"] and reaction.message.id == msg_owner.id

                        reaction_owner, u = await bot.wait_for('reaction_add', check=check)
                        await msg_owner.remove_reaction(reaction_owner.emoji, u)
                        if str(reaction_owner.emoji) == "✅":
                            await iniciar_torneio()
                    except:
                        pass

            # Remove a reação do usuário para resetar
            await asyncio.sleep(0.2)
            await reaction.message.remove_reaction(emoji, user)
        except discord.errors.HTTPException:
            pass

        asyncio.create_task(atualizar_painel_delay())

# -----------------------------
# COMANDOS DE TEXTO
# -----------------------------
@bot.command()
async def painel(ctx):
    await atualizar_painel()
    await ctx.message.add_reaction("✅")

@bot.command()
async def torneio(ctx):
    torneio_data["active"] = True
    torneio_data["players"] = []
    torneio_data["decklists"] = {}
    torneio_data["round"] = 0
    torneio_data["pairings"] = {}
    torneio_data["results"] = {}
    torneio_data["scores"] = {}
    torneio_data["played"] = {}
    torneio_data["byes"] = []
    torneio_data["finished"] = False
    torneio_data["rounds_target"] = None
    save_json(TORNEIO_FILE, torneio_data)
    await ctx.send("🏆 Torneio ativado! Jogadores podem se inscrever clicando 🏅 no painel.")
    await atualizar_painel()

@bot.command()
async def cancelartorneio(ctx):
    torneio_data["active"] = False
    torneio_data["players"] = []
    torneio_data["decklists"] = {}
    torneio_data["round"] = 0
    torneio_data["pairings"] = {}
    torneio_data["results"] = {}
    torneio_data["scores"] = {}
    torneio_data["played"] = {}
    torneio_data["byes"] = []
    torneio_data["finished"] = False
    torneio_data["rounds_target"] = None
    save_json(TORNEIO_FILE, torneio_data)
    await ctx.send("❌ Torneio cancelado.")
    await atualizar_painel()

@bot.command()
async def resetranking(ctx):
    ranking["scores"] = {}
    ranking["__last_reset"] = datetime.datetime.now().isoformat()
    save_json(RANKING_FILE, ranking)
    await ctx.send("🔄 Ranking 1x1 resetado.")

# -----------------------------
# INÍCIO TORNEIO (placeholder)
# -----------------------------
async def iniciar_torneio():
    # Aqui você deve implementar emparelhamento suíço, rodadas e solicitações de decklist
    # Exemplo de placeholder:
    for uid in torneio_data["players"]:
        try:
            await bot.fetch_user(uid).send("O torneio começou! Prepare seu deck e aguarde a primeira rodada.")
        except:
            pass
    await atualizar_painel()

# -----------------------------
# EVENTO ON READY
# -----------------------------
@bot.event
async def on_ready():
    print(f"Bot conectado como {bot.user}")
    asyncio.create_task(atualizar_painel_delay())

# -----------------------------
# EXECUÇÃO
# -----------------------------
bot.run(DISCORD_TOKEN)
