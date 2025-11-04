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

# -----------------------------
# FUNÇÃO DE CARREGAMENTO SEGURO DE JSON
# -----------------------------
def load_json(file, default):
    try:
        if not os.path.exists(file):
            with open(file, "w") as f:
                json.dump(default, f, indent=4)
        with open(file, "r") as f:
            data = json.load(f)
            # Se default é dict, garante chaves
            if isinstance(default, dict) and isinstance(data, dict):
                for k, v in default.items():
                    if k not in data:
                        data[k] = v
            # Se tipos diferentes, retorna default
            elif type(data) != type(default):
                return default
            return data
    except Exception as e:
        print(f"Erro ao ler {file}: {e}")
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

# -----------------------------
# PAINEL
# -----------------------------
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
    if torneio_data.get("active", False):
        content += "🏅 Inscrever no torneio / ver ranking de torneios\n"

    await painel_msg.edit(content=content)

# -----------------------------
# CONTINUAÇÃO: Reações, DM ranking, torneio, tasks etc.
# (Mesmas funções que já implementamos)
# -----------------------------

