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
            print(f"✅ Arquivo criado automaticamente: {file_path}")

# -----------------------------
# FUNÇÃO DE CARREGAMENTO SEGURO DE JSON
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

def save_panel_id(message_id):
    save_json(PAINEL_FILE, {"message_id": message_id})

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
        save_panel_id(PANEL_MESSAGE_ID)
    else:
        try:
            painel_msg = await channel.fetch_message(PANEL_MESSAGE_ID)
        except discord.NotFound:
            painel_msg = await channel.send("Painel inicializando...")
            PANEL_MESSAGE_ID = painel_msg.id
            save_panel_id(PANEL_MESSAGE_ID)

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
    print("✅ Painel atualizado")

# -----------------------------
# DUMMY SERVER PARA RENDER
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
# TASKS DE RANKING E SALVAMENTO
# -----------------------------
@tasks.loop(hours=1)
async def check_reset_ranking():
    now = datetime.datetime.now(pytz.timezone("America/Sao_Paulo"))
    if now.day == 1 and ranking.get("__last_reset") != now.strftime("%Y-%m-%d"):
        ranking["scores"] = {}
        ranking["__last_reset"] = now.strftime("%Y-%m-%d")
        save_json(RANKING_FILE, ranking)
        print("[RANKING] Reset mensal realizado.")

@tasks.loop(seconds=30)
async def save_states():
    save_json(RANKING_FILE, ranking)
    save_json(TORNEIO_FILE, torneio_data)
    save_json(HIST_FILE, historico)
    save_panel_id(PANEL_MESSAGE_ID)

# -----------------------------
# RODAR BOT
# -----------------------------
if __name__ == "__main__":
    try:
        bot.run(DISCORD_TOKEN)
    except Exception as e:
        print(f"Erro crítico ao iniciar o bot: {e}")
