import discord
from discord.ext import commands, tasks
import asyncio, os, json, datetime, math

# -----------------------------
# CONFIGURAÇÕES
# -----------------------------
DISCORD_TOKEN = "SEU_DISCORD_TOKEN_AQUI"
GUILD_ID = 0
PANEL_CHANNEL_ID = 0
BOT_OWNER = 0

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
    "rounds_target": None,
    "inscriptions_open": False
})
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
    if not channel: return

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
# MATCHMAKING 1x1
# -----------------------------
async def checar_fila_1x1():
    global fila, partidas_ativas
    while True:
        await asyncio.sleep(2)
        while len(fila) >= 2:
            p1 = fila.pop(0)
            p2 = fila.pop(0)
            match_id = f"1x1_{p1}_{p2}_{int(datetime.datetime.now().timestamp())}"
            partidas_ativas[match_id] = {"player1": p1, "player2": p2, "status": "em andamento", "cancel_attempted": False}
            try:
                await bot.fetch_user(p1).send(f"⚔️ Você foi emparelhado com <@{p2}>!")
                await bot.fetch_user(p2).send(f"⚔️ Você foi emparelhado com <@{p1}>!")
            except: pass
            await atualizar_painel()
            asyncio.create_task(solicitar_resultado(match_id, partida_type="1x1"))

# -----------------------------
# RESULTADO DE PARTIDAS
# -----------------------------
async def solicitar_resultado(match_id, partida_type="1x1"):
    partida = partidas_ativas.get(match_id) if partida_type=="1x1" else torneio_data["pairings"].get(match_id)
    if not partida: return

    u1 = partida["player1"] if partida_type=="1x1" else partida[0]
    u2 = partida["player2"] if partida_type=="1x1" else partida[1]
    emojis = ["1️⃣","2️⃣","❌"]
    resultados = {}

    for uid in [u1,u2]:
        try:
            user = await bot.fetch_user(uid)
            msg = await user.send(
                f"⚔️ Partida {match_id}!\nQuem venceu?\n1️⃣ = <@{u1}>\n2️⃣ = <@{u2}>\n❌ = Solicitar Cancelamento"
            )
            for e in emojis: await msg.add_reaction(e)

            def check(reaction, user_react):
                return user_react.id==uid and str(reaction.emoji) in emojis and reaction.message.id==msg.id

            reaction,_ = await bot.wait_for("reaction_add", check=check, timeout=3600)
            if str(reaction.emoji)=="❌":
                asyncio.create_task(solicitar_cancelamento(match_id, partida_type))
                return
            else:
                resultados[uid] = str(reaction.emoji)
        except asyncio.TimeoutError:
            resultados[uid] = None
            await user.send("⏱️ Tempo esgotado para enviar resultado.")

    if resultados.get(u1)==resultados.get(u2) and resultados[u1] is not None:
        vencedor = u1 if resultados[u1]=="1️⃣" else u2
        if partida_type=="1x1":
            historico.append({"winner":vencedor,"loser":u2 if vencedor==u1 else u1,"time":str(datetime.datetime.now())})
            save_json(HISTORICO_FILE,historico)
            partidas_ativas.pop(match_id)
            ranking["scores"][vencedor] = ranking.get("scores",{}).get(vencedor,0)+1
            save_json(RANKING_FILE,ranking)
        else:
            torneio_data["results"][match_id] = vencedor
            torneio_data["scores"][vencedor] = torneio_data.get("scores",{}).get(vencedor,0)+1
            save_json(TORNEIO_FILE,torneio_data)
        await atualizar_painel()
        for uid in [u1,u2]:
            try: await bot.fetch_user(uid).send(f"✅ Resultado confirmado! Vencedor: <@{vencedor}>")
            except: pass
    else:
        for uid in [u1,u2]:
            try: await bot.fetch_user(uid).send("⚠️ Resultado divergente ou não enviado. Conversem e reenviem.")
            except: pass

# -----------------------------
# CANCELAMENTO DE PARTIDA
# -----------------------------
async def solicitar_cancelamento(match_id, partida_type="1x1"):
    partida = partidas_ativas.get(match_id) if partida_type=="1x1" else torneio_data["pairings"].get(match_id)
    if not partida or partida.get("cancel_attempted"): return

    u1 = partida["player1"] if partida_type=="1x1" else partida[0]
    u2 = partida["player2"] if partida_type=="1x1" else partida[1]

    partida["cancel_attempted"] = True
    emojis = ["✅","❌"]
    respostas = {}

    for uid in [u1,u2]:
        try:
            user = await bot.fetch_user(uid)
            msg1 = await user.send(
                f"⚠️ Você solicitou cancelar a partida {match_id}.\nTem certeza?\n✅ Confirmar\n❌ Cancelar"
            )
            for e in emojis: await msg1.add_reaction(e)
            def check1(reaction,user_react): return user_react.id==uid and str(reaction.emoji) in emojis and reaction.message.id==msg1.id
            reaction,_ = await bot.wait_for("reaction_add", check=check1, timeout=3600)
            respostas[uid]=str(reaction.emoji)
        except asyncio.TimeoutError:
            respostas[uid] = "❌"

    if respostas[u1]=="✅" and respostas[u2]=="✅":
        if partida_type=="1x1": partidas_ativas.pop(match_id,None)
        else: torneio_data["pairings"].pop(match_id,None)
        await atualizar_painel()
        for uid in [u1,u2]:
            try: await bot.fetch_user(uid).send("✅ A partida foi cancelada com sucesso.")
            except: pass
    else:
        for uid in [u1,u2]:
            try: await bot.fetch_user(uid).send("❌ Cancelamento abortado. Partida continuará normalmente.")
            except: pass

# -----------------------------
# EVENTOS
# -----------------------------
@bot.event
async def on_ready():
    print(f"Bot conectado como {bot.user}")
    await atualizar_painel()
    asyncio.create_task(checar_fila_1x1())

@tasks.loop(minutes=5)
async def save_states():
    save_json(RANKING_FILE, ranking)
    save_json(TORNEIO_FILE, torneio_data)
    save_json(HISTORICO_FILE, historico)
save_states.start()

# -----------------------------
# COMANDOS BÁSICOS
# -----------------------------
@bot.command()
async def fila(ctx):
    txt="\n".join([f"<@{uid}>" for uid in fila]) or "Vazia"
    await ctx.send(f"**Fila 1x1:**\n{txt}")

@bot.command()
async def novopainel(ctx):
    global PANEL_MESSAGE_ID
    PANEL_MESSAGE_ID=0
    await atualizar_painel()
    await ctx.send("✅ Painel reiniciado!")

# -----------------------------
# EXECUÇÃO
# -----------------------------
bot.run(DISCORD_TOKEN)
