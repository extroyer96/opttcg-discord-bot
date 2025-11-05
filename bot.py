import discord
from discord.ext import commands, tasks
from discord import Intents
import asyncio, json, os, datetime, random
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
mostrar_inscritos = False  # toggle para painel

# -----------------------------
# FUNÇÕES DE RANKING / HISTÓRICO
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
# PAINEL
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
        content += "🏆 **Torneio ativo!**\nRodada atual: {}\nClique na reação 🏅 para se inscrever!\n".format(torneio_data.get("round",0))
        if mostrar_inscritos:
            inscritos_txt = ", ".join([f"<@{pid}>" for pid in torneio_data["players"]]) or "(nenhum)"
            content += f"📝 **Inscritos:** {inscritos_txt}\n"
    content += "💡 **Interaja com o painel:**\n"
    content += "✅ Entrar na fila\n❌ Sair da fila\n🏆 Ver ranking 1x1\n"
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
                await asyncio.sleep(0.5)
        except discord.errors.HTTPException:
            pass

async def atualizar_painel_delay():
    await asyncio.sleep(1)
    try:
        await atualizar_painel()
    except discord.errors.HTTPException:
        pass

# -----------------------------
# EMPARELHAMENTO AUTOMÁTICO 1x1
# -----------------------------
async def checar_fila_1x1():
    global fila, partidas_ativas
    while True:
        await asyncio.sleep(2)
        while len(fila) >= 2:
            p1 = fila.pop(0)
            p2 = fila.pop(0)
            match_id = f"{p1}_{p2}_{int(datetime.datetime.now().timestamp())}"
            partidas_ativas[match_id] = {"player1": p1, "player2": p2, "status": "em andamento"}
            try:
                user1 = await bot.fetch_user(p1)
                user2 = await bot.fetch_user(p2)
                await user1.send(f"⚔️ Você foi emparelhado com <@{p2}> para uma partida 1x1!")
                await user2.send(f"⚔️ Você foi emparelhado com <@{p1}> para uma partida 1x1!")
            except:
                pass
            await atualizar_painel()

# -----------------------------
# EMPARELHAMENTO TORNEIO SUÍÇO
# -----------------------------
def gerar_pairings_suico():
    players = torneio_data["players"].copy()
    random.shuffle(players)
    scores = torneio_data.get("scores", {})
    players_sorted = sorted(players, key=lambda x: scores.get(x,0), reverse=True)
    pairings = {}
    while len(players_sorted) >= 2:
        p1 = players_sorted.pop(0)
        p2 = players_sorted.pop(0)
        pairings[p1] = p2
    if players_sorted:
        p = players_sorted.pop(0)
        pairings[p] = "BYE"
        torneio_data["scores"][p] = torneio_data.get("scores", {}).get(p,0)+1
    return pairings

async def iniciar_rodada_torneio():
    rodada = torneio_data.get("round", 0) + 1
    torneio_data["round"] = rodada
    pairings = gerar_pairings_suico()
    torneio_data["pairings"][f"round_{rodada}"] = pairings
    save_json(TORNEIO_FILE, torneio_data)
    for p1, p2 in pairings.items():
        try:
            user1 = await bot.fetch_user(p1)
            if p2 != "BYE":
                await user1.send(f"🏁 Rodada {rodada}: você enfrenta <@{p2}>")
            else:
                await user1.send(f"🏁 Rodada {rodada}: você recebe um BYE automático")
        except:
            pass
    await atualizar_painel()

# -----------------------------
# REAÇÕES
# -----------------------------
@bot.event
async def on_reaction_add(reaction, user):
    if user.bot:
        return
    emoji = str(reaction.emoji)
    if emoji == "✅":
        if user.id not in fila:
            fila.append(user.id)
            try: await user.send("Você entrou na fila 1x1!"); await reaction.message.remove_reaction(emoji, user)
            except: pass
    elif emoji == "❌":
        if user.id in fila:
            fila.remove(user.id)
            try: await user.send("Você saiu da fila 1x1!"); await reaction.message.remove_reaction(emoji, user)
            except: pass
    elif emoji == "🏆":
        try:
            msg = await user.send(gerar_ranking_texto() + "\nDeseja ver ranking de torneios? (⬅️ = sim, ❌ = não)")
            await msg.add_reaction("⬅️")
            await msg.add_reaction("❌")
        except: pass
    elif emoji == "🏅" and torneio_data.get("active", False):
        if user.id not in torneio_data["players"]:
            torneio_data["players"].append(user.id)
            save_json(TORNEIO_FILE, torneio_data)
            try: await user.send("✅ Você se inscreveu no torneio suíço!\nAguarde o início.")
            except: pass
            owner = await bot.fetch_user(BOT_OWNER)
            inscritos_txt = "\n".join([f"<@{pid}>" for pid in torneio_data["players"]])
            try:
                msg_owner = await owner.send(f"📋 **Jogadores inscritos:**\n{inscritos_txt}\nDeseja iniciar o torneio agora?")
                await msg_owner.add_reaction("✅")
                await msg_owner.add_reaction("❌")

                async def owner_iniciar_torneio(msg_owner):
                    def check(reaction, u):
                        return u.id == BOT_OWNER and str(reaction.emoji) in ["✅","❌"] and reaction.message.id == msg_owner.id
                    try:
                        reaction_owner, u = await bot.wait_for('reaction_add', check=check, timeout=3600)
                        await msg_owner.remove_reaction(reaction_owner.emoji, u)
                        if str(reaction_owner.emoji) == "✅":
                            asyncio.create_task(iniciar_rodada_torneio())
                    except asyncio.TimeoutError:
                        await msg_owner.channel.send("⏱️ Tempo esgotado. Torneio não iniciado.")
                asyncio.create_task(owner_iniciar_torneio(msg_owner))
            except: pass
    await atualizar_painel_delay()

# -----------------------------
# COMANDOS
# -----------------------------
@bot.command()
async def painel(ctx):
    await atualizar_painel()
    await ctx.message.add_reaction("✅")

@bot.command()
async def toggleinscritos(ctx):
    global mostrar_inscritos
    mostrar_inscritos = not mostrar_inscritos
    await ctx.send(f"✅ Lista de inscritos agora {'visível' if mostrar_inscritos else 'oculta'} no painel.")
    await atualizar_painel()

@bot.command()
async def torneio(ctx):
    torneio_data.update({
        "active": True,
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
    save_json(TORNEIO_FILE, torneio_data)
    await ctx.send("🏆 Torneio ativado! Jogadores podem se inscrever clicando 🏅 no painel.")
    await atualizar_painel()

@bot.command()
async def cancelartorneio(ctx):
    torneio_data.update(json_defaults[TORNEIO_FILE])
    save_json(TORNEIO_FILE, torneio_data)
    await ctx.send("❌ Torneio cancelado.")
    await atualizar_painel()

@bot.command()
async def resetranking(ctx):
    ranking["scores"] = {}
    ranking["__last_reset"] = datetime.datetime.now().isoformat()
    save_json(RANKING_FILE, ranking)
    await ctx.send("🔄 Ranking 1x1 resetado.")

@bot.command()
async def começartorneio(ctx):
    if ctx.author.id != BOT_OWNER:
        await ctx.send("❌ Apenas o dono do bot pode iniciar o torneio.")
        return
    if not torneio_data.get("active", False):
        await ctx.send("⚠️ Nenhum torneio ativo para iniciar.")
        return
    await iniciar_rodada_torneio()
    await ctx.send("🏁 Torneio iniciado!")

@bot.command()
async def novopainel(ctx):
    global PANEL_MESSAGE_ID
    channel = bot.get_channel(PANEL_CHANNEL_ID)
    if not channel:
        return
    async for msg in channel.history(limit=100):
        try:
            await msg.delete()
            await asyncio.sleep(0.2)
        except:
            pass
    PANEL_MESSAGE_ID = 0
    save_panel_id(PANEL_MESSAGE_ID)
    await ctx.send("🔄 Painel reiniciado. Um novo painel será criado automaticamente.")
    await atualizar_painel()

@bot.command()
async def statustorneio(ctx):
    if not torneio_data.get("active", False):
        await ctx.send("⚠️ Nenhum torneio ativo no momento.")
        return
    rodada = torneio_data.get("round", 0)
    pairings = torneio_data.get("pairings", {}).get(f"round_{rodada}", {})
    if not pairings:
        await ctx.send(f"🏆 Torneio ativo! Rodada atual: {rodada}\nConfrontos ainda não definidos.")
        return
    txt = f"🏆 **Status do Torneio**\nRodada atual: {rodada}\nConfrontos:\n"
    for p1, p2 in pairings.items():
        txt += f"<@{p1}> vs <@{p2}>\n"
    await ctx.send(txt)

@bot.command()
async def ff(ctx):
    uid = ctx.author.id
    if uid not in torneio_data.get("players", []):
        await ctx.send("❌ Você não está inscrito em nenhum torneio ativo.")
        return
    torneio_data["players"].remove(uid)
    rodada_atual = torneio_data.get("round", 0)
    for rnd, pairings in torneio_data.get("pairings", {}).items():
        for p1, p2 in pairings.items():
            if p1 == uid: pairings[p1] = "BYE"
            if p2 == uid: pairings[p2] = "BYE"
    save_json(TORNEIO_FILE, torneio_data)
    await ctx.send(f"⚠️ <@{uid}> desistiu do torneio. Próximos adversários receberão BYE automaticamente.")
    await atualizar_painel()

# -----------------------------
# ON READY
# -----------------------------
@bot.event
async def on_ready():
    print(f"Bot conectado como {bot.user}")
    asyncio.create_task(atualizar_painel_delay())
    asyncio.create_task(checar_fila_1x1())

    # Dummy HTTP server para Render
    async def handle(request):
        return web.Response(text="Bot rodando!")
    PORT = int(os.getenv("PORT", 10000))
    app = web.Application()
    app.add_routes([web.get("/", handle)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"🌐 Servidor HTTP dummy iniciado na porta {PORT}")

# -----------------------------
# EXECUÇÃO
# -----------------------------
bot.run(DISCORD_TOKEN)
