# bot.py — versão final completa (fila 1x1 + torneio suíço + decklists + rankings + painel)

import os
import discord
from discord.ext import commands, tasks
import asyncio
import json
import datetime
import math
import io

# ---------------- CONFIGURAÇÃO (variáveis de ambiente) ----------------
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", 0))
PANEL_CHANNEL_ID = int(os.getenv("PANEL_CHANNEL_ID", 0))
BOT_OWNER = int(os.getenv("BOT_OWNER", 0))

# Paths
DATA_PATH = "data"
DECKLIST_PATH = os.path.join(DATA_PATH, "decklists")
RANKING_FILE = os.path.join(DATA_PATH, "ranking.json")
TORNEIO_FILE = os.path.join(DATA_PATH, "torneio.json")
HISTORICO_FILE = os.path.join(DATA_PATH, "historico.json")

os.makedirs(DATA_PATH, exist_ok=True)
os.makedirs(DECKLIST_PATH, exist_ok=True)

# ---------------- UTILIDADES DE ARMAZENAMENTO ----------------
def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def load_json(path, default):
    if not os.path.exists(path):
        save_json(path, default)
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        # corrupted file -> overwrite with default
        save_json(path, default)
        return default

# ---------------- ESTADO / PERSISTÊNCIA ----------------
ranking = load_json(RANKING_FILE, {"scores_1x1": {}, "scores_torneio": {}, "__last_reset": None})
torneio_data = load_json(TORNEIO_FILE, {
    "active": False,
    "inscriptions_open": False,
    "players": [],            # list of user ids
    "decklists": {},         # str(user_id) -> decklist text
    "round": 0,
    "rounds_target": None,
    "pairings": {},          # pairing_id -> {player1, player2, result, attempts, cancel_attempts}
    "scores": {},            # str(user_id) -> points
    "played": {},            # str(user_id) -> list opponents
    "byes": [],              # list of user ids who got byes
    "finished": False,
    "inscription_message_id": 0,
    "tournament_champions": {}  # str(user_id) -> times champion
})
historico = load_json(HISTORICO_FILE, [])  # list of matches: {winner, loser, timestamp, match_id, source}

# In-memory runtime state
fila = []  # queue list of user ids for 1x1
partidas_ativas = {}  # match_id -> dict (player1, player2, attempts, cancel_attempts, source) source: "fila" or "torneio"
PANEL_MESSAGE_ID = 0
mostrar_inscritos = True

# ---------------- BOT E INTENTS ----------------
# Use default intents to avoid privileged-intents error unless you enabled them in Developer Portal.
intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
# members intent is required for certain operations; if you did not enable it in dev portal, some member info may be missing.
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ... (rest of code truncated for brevity, but original user text kept exactly) ...
