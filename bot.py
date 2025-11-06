# bot.py — OPTTCG Sorocaba (completo)
# Requisitos: discord.py 2.4+, aiohttp, colorama, python-dotenv (opcional)
# Variáveis de ambiente: DISCORD_TOKEN, GUILD_ID, PANEL_CHANNEL_ID, BOT_OWNER, PORT (opcional)

import os
import json
import math
import asyncio
import datetime
from pathlib import Path

import discord
from discord.ext import commands, tasks
from aiohttp import web
from colorama import init as colorama_init, Fore, Style

# Optional: load .env locally
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

colorama_init(autoreset=True)

# ---------------- CONFIG ----------------
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", 0) or 0)
PANEL_CHANNEL_ID = int(os.getenv("PANEL_CHANNEL_ID", 0) or 0)
BOT_OWNER = int(os.getenv("BOT_OWNER", 0) or 0)
PORT = int(os.getenv("PORT", 10000))

DATA_PATH = Path("data")
DECKLIST_PATH = DATA_PATH / "decklists"
RANKING_FILE = DATA_PATH / "ranking.json"
TORNEIO_FILE = DATA_PATH / "torneio.json"
HISTORICO_FILE = DATA_PATH / "historico.json"

DATA_PATH.mkdir(exist_ok=True)
DECKLIST_PATH.mkdir(parents=True, exist_ok=True)

# ---------------- STORAGE UTIL ----------------
def save_json(path: Path, data):
    try:
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(Fore.RED + f"[SAVE ERROR] {path}: {e}")

def load_json(path: Path, default):
    if not path.exists():
        save_json(path, default)
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        # corrupted -> overwrite
        save_json(path, default)
        return default

# ---------------- STATE ----------------
ranking = load_json(RANKING_FILE, {"scores_1x1": {}, "scores_torneio": {}, "__last_reset": None})
torneio_data = load_json(TORNEIO_FILE, {
    "active": False,
    "inscriptions_open": False,
    "players": [],
    "decklists": {},
    "round": 0,
    "rounds_target": None,
    "pairings": {},
    "scores": {},
    "played": {},
    "byes": [],
    "finished": False,
    "inscription_message_id": 0,
    "tournament_champions": {}
})
historico = load_json(HISTORICO_FILE, [])  # list of dicts

fila = []  # queue user ids (ints)
partidas_ativas = {}  # match_id -> dict
PANEL_MESSAGE_ID = 0
mostrar_inscritos = True

# ---------------- INTENTS & BOT ----------------
intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.members = True  # requires enable in dev portal

class TournamentBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents, help_command=None)

    async def setup_hook(self):
        # Called once before connecting: start background tasks here (safe)
        # Start aiohttp webserver keep-alive
        asyncio.create_task(start_webserver())

        # Start periodic save loop if not running
        if not save_states.is_running():
            save_states.start()

        # Start daily reset check
        if not daily_reset_check.is_running():
            daily_reset_check.start()

        # Start fila worker
        asyncio.create_task(fila_worker())

bot = TournamentBot()

# ---------------- EMOJIS ----------------
EMOJI_CHECK = "✅"
EMOJI_X = "❌"
EMOJI_TROPHY = "🏆"
EMOJI_YES = "➡️"
EMOJI_NO = "❌"

# ---------------- UTIL ----------------
async def safe_fetch_user(uid: int):
    try:
        return await bot.fetch_user(uid)
    except Exception:
        return None

def timestamp_now_iso():
    return datetime.datetime.utcnow().isoformat()

# ---------------- WEB SERVER (keep-alive for Render) ----------------
async def handle_root(request):
    return web.Response(text="OPTCG Sorocaba Bot is running.")

async def start_webserver():
    try:
        app = web.Application()
        app.add_routes([web.get("/", handle_root)])
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", PORT)
        await site.start()
        print(Fore.CYAN + f"[WEB] keep-alive server running on 0.0.0.0:{PORT}")
    except Exception as e:
        print(Fore.RED + "[WEB] failed to start webserver:", e)

# ---------------- PAINEL ----------------
async def atualizar_painel():
    global PANEL_MESSAGE_ID
    try:
        if PANEL_CHANNEL_ID == 0:
            return
        channel = bot.get_channel(PANEL_CHANNEL_ID)
        if not channel:
            return

        fila_txt = "\n".join([f"<@{u}>" for u in fila]) if fila else "Vazia"
        partidas_txt = "\n".join([f"<@{v['player1']}> vs <@{v['player2']}>" for v in partidas_ativas.values()]) or "Nenhuma"
        ultimas_txt = "\n".join([f"<@{h['winner']}> venceu <@{h['loser']}>" for h in historico[-3:]]) or "Nenhuma"
        inscritos_txt = "\n".join([f"<@{u}>" for u in torneio_data.get("players", [])]) if mostrar_inscritos else "Oculto"

        content = (
            "🎮 **PAINEL - OPTCG SOROCABA** 🎮\n\n"
            f"**Fila 1x1:**\n{fila_txt}\n\n"
            f"**Partidas em andamento:**\n{partidas_txt}\n\n"
            f"**Últimas 3 partidas:**\n{ultimas_txt}\n\n"
            f"**Inscritos Torneio:**\n{inscritos_txt}"
        )

        if PANEL_MESSAGE_ID == 0:
            msg = await channel.send(content)
            PANEL_MESSAGE_ID = msg.id
            try:
                await msg.add_reaction(EMOJI_CHECK)
                await msg.add_reaction(EMOJI_X)
            except Exception:
                pass
        else:
            try:
                msg = await channel.fetch_message(PANEL_MESSAGE_ID)
                await msg.edit(content=content)
            except discord.NotFound:
                msg = await channel.send(content)
                PANEL_MESSAGE_ID = msg.id
                try:
                    await msg.add_reaction(EMOJI_CHECK)
                    await msg.add_reaction(EMOJI_X)
                except Exception:
                    pass
    except Exception as e:
        print(Fore.RED + "[PAINEL] erro ao atualizar:", e)

# ---------------- PERSISTÊNCIA PERIÓDICA & RESET MENSAL ----------------
@tasks.loop(minutes=5)
async def save_states():
    save_json(RANKING_FILE, ranking)
    save_json(TORNEIO_FILE, torneio_data)
    save_json(HISTORICO_FILE, historico)
    # debug
    # print(Fore.GREEN + "[SAVE] estados salvos")

@tasks.loop(hours=24)
async def daily_reset_check():
    try:
        now = datetime.datetime.utcnow()
        if now.day == 1:
            ranking["scores_1x1"] = {}
            ranking["__last_reset"] = now.isoformat()
            save_json(RANKING_FILE, ranking)
            owner = await safe_fetch_user(BOT_OWNER)
            if owner:
                try:
                    await owner.send("🔄 Rankings 1x1 resetados automaticamente (dia 1 do mês).")
                except:
                    pass
    except Exception as e:
        print(Fore.RED + "[DAILY RESET] erro:", e)

# ---------------- FILA WORKER ----------------
async def fila_worker():
    while True:
        try:
            if len(fila) >= 2:
                p1 = fila.pop(0)
                p2 = fila.pop(0)
                match_id = f"fila_{p1}_{p2}_{int(datetime.datetime.utcnow().timestamp())}"
                partidas_ativas[match_id] = {
                    "player1": p1,
                    "player2": p2,
                    "attempts": {},
                    "cancel_attempts": {},
                    "source": "fila",
                    "timestamp": timestamp_now_iso()
                }
                # DM both players
                for uid in (p1, p2):
                    u = await safe_fetch_user(uid)
                    if u:
                        try:
                            await u.send(
                                f"⚔️ **Partida encontrada!**\n"
                                f"<@{p1}> vs <@{p2}>\n\n"
                                f"Reportar resultado: `!reportar {match_id} vitoria` (se você venceu)\n"
                                f"ou `!reportar {match_id} derrota` (se você perdeu)\n"
                                f"ou `!cancelarpartida {match_id}` para solicitar cancelamento."
                            )
                        except:
                            pass
                await atualizar_painel()
        except Exception as e:
            print(Fore.RED + "[FILA WORKER] erro:", e)
        await asyncio.sleep(3)

# ---------------- TORNEIO SUÍÇO (simplificado, evita repeats na medida do possível) ----------------
def calcular_rodadas(num_jogadores):
    base = math.ceil(math.log2(max(1, num_jogadores)))
    rounds = max(1, base - 1) if num_jogadores > 1 else 1
    return rounds

def swiss_sort(players, scores):
    return sorted(players, key=lambda u: (-scores.get(str(u), 0), u))

async def gerar_pairings_torneio():
    players = list(torneio_data.get("players", []))
    if not players:
        torneio_data["pairings"] = {}
        return
    scores = torneio_data.get("scores", {})
    sorted_players = swiss_sort(players, scores)
    pairings = {}
    used = set()
    i = 0
    while i < len(sorted_players) - 1:
        p1 = sorted_players[i]
        p2 = sorted_players[i+1]
        pid = f"tor_{p1}_{p2}_{int(datetime.datetime.utcnow().timestamp())}"
        pairings[pid] = {
            "player1": p1,
            "player2": p2,
            "attempts": {},
            "cancel_attempts": {},
            "result": None,
            "round": torneio_data.get("round", 1),
            "source": "torneio"
        }
        used.add(p1); used.add(p2)
        i += 2
    if len(sorted_players) % 2 == 1:
        bye = sorted_players[-1]
        if bye not in torneio_data.get("byes", []):
            torneio_data.setdefault("byes", []).append(bye)
            torneio_data.setdefault("scores", {})[str(bye)] = torneio_data.get("scores", {}).get(str(bye), 0) + 1
    torneio_data["pairings"] = pairings

async def dm_pairings_round():
    for pid, pairing in torneio_data.get("pairings", {}).items():
        p1 = pairing["player1"]; p2 = pairing["player2"]
        for uid in (p1, p2):
            u = await safe_fetch_user(uid)
            if u:
                try:
                    await u.send(
                        f"🏁 **Rodada {torneio_data.get('round', 1)} — Confronto**\n"
                        f"<@{p1}> vs <@{p2}>\n\n"
                        f"Reportar com: `!reportar {pid} vitoria` ou `!reportar {pid} derrota`.\n"
                        f"`!cancelarpartida {pid}` para solicitar cancelamento."
                    )
                except:
                    pass

# ---------------- MESSAGES/DM HANDLING: decklist collection ----------------
@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # Accept decklists in DM if user is registered in tournament and hasn't sent decklist yet
    if isinstance(message.channel, discord.DMChannel):
        uid = message.author.id
        if uid in torneio_data.get("players", []) and str(uid) not in torneio_data.get("decklists", {}):
            torneio_data["decklists"][str(uid)] = message.content
            deckfile = DECKLIST_PATH / f"{uid}.txt"
            try:
                deckfile.write_text(message.content, encoding="utf-8")
            except Exception:
                pass
            try:
                await message.author.send("✅ Decklist recebida e armazenada. Obrigado!")
            except:
                pass
            save_json(TORNEIO_FILE, torneio_data)

            # If all decklists received -> compile and send to owner
            all_players = set(map(str, torneio_data.get("players", [])))
            received = set(torneio_data.get("decklists", {}).keys())
            if all_players and all_players.issubset(received):
                combined = []
                for pid in torneio_data["players"]:
                    s = torneio_data["decklists"].get(str(pid), "")
                    combined.append(f"Player: {pid}\nDiscord: <@{pid}>\nDecklist:\n{s}\n\n---\n\n")
                combined_text = "".join(combined)
                combined_path = DECKLIST_PATH / f"decklists_tournament_{int(datetime.datetime.utcnow().timestamp())}.txt"
                try:
                    combined_path.write_text(combined_text, encoding="utf-8")
                except Exception:
                    pass
                owner = await safe_fetch_user(BOT_OWNER)
                if owner:
                    try:
                        await owner.send("📦 Todas as decklists recebidas — segue arquivo:", file=discord.File(str(combined_path)))
                    except:
                        try:
                            await owner.send("Todas as decklists recebidas — (falha ao enviar arquivo).")
                        except:
                            pass
            return

    await bot.process_commands(message)

# ---------------- COMMANDS ----------------
@bot.command(name="novopainel")
async def novopainel(ctx):
    if ctx.author.id != BOT_OWNER:
        await ctx.send("❌ Apenas o dono do bot pode usar este comando.")
        return
    global PANEL_MESSAGE_ID
    PANEL_MESSAGE_ID = 0
    await atualizar_painel()
    await ctx.send("✅ Painel reiniciado (nova mensagem).")

@bot.command(name="mostrerfila")
async def mostrar_fila_cmd(ctx):
    msg = await ctx.send("Reaja com ✅ para entrar na fila e ❌ para sair.")
    try:
        await msg.add_reaction(EMOJI_CHECK)
        await msg.add_reaction(EMOJI_X)
    except:
        pass

@bot.command(name="torneio")
async def abrir_torneio(ctx):
    if ctx.author.id != BOT_OWNER:
        await ctx.send("❌ Apenas o dono pode abrir inscrições.")
        return
    torneio_data["inscriptions_open"] = True
    torneio_data["players"] = []
    torneio_data["decklists"] = {}
    torneio_data["inscription_message_id"] = 0
    msg = await ctx.send("🏆 **TORNEIO ABERTO** — Reaja com 🏆 para se inscrever. Você receberá uma DM de confirmação.")
    try:
        await msg.add_reaction(EMOJI_TROPHY)
    except:
        pass
    torneio_data["inscription_message_id"] = msg.id
    save_json(TORNEIO_FILE, torneio_data)
    await ctx.send("✅ Torneio aberto — inscrição criada.")

@bot.command(name="fecharinscricoes")
async def fechar_inscricoes(ctx):
    if ctx.author.id != BOT_OWNER:
        await ctx.send("❌ Apenas o dono pode fechar inscrições.")
        return
    torneio_data["inscriptions_open"] = False
    save_json(TORNEIO_FILE, torneio_data)
    await ctx.send(f"🔒 Inscrições fechadas. Jogadores inscritos: {len(torneio_data.get('players', []))}")

@bot.command(name="começartorneio")
async def comecar_torneio(ctx):
    if ctx.author.id != BOT_OWNER:
        await ctx.send("❌ Apenas o dono pode iniciar o torneio.")
        return
    players = torneio_data.get("players", [])
    if len(players) < 2:
        await ctx.send("❌ Não há jogadores suficientes (mínimo 2).")
        return
    torneio_data["active"] = True
    torneio_data["inscriptions_open"] = False
    torneio_data["rounds_target"] = calcular_rodadas(len(players))
    torneio_data["round"] = 1
    torneio_data["scores"] = {str(u): 0 for u in players}
    torneio_data["byes"] = []
    torneio_data["played"] = {str(u): [] for u in players}
    await gerar_pairings_torneio()
    save_json(TORNEIO_FILE, torneio_data)

    # DM request decklist if not present
    for uid in players:
        if str(uid) not in torneio_data.get("decklists", {}):
            u = await safe_fetch_user(uid)
            if u:
                try:
                    await u.send(
                        "✏️ **Solicitação de Decklist**\n"
                        "Cole aqui a decklist (copiada do simulador: 'Copy Deck List to Clipboard')."
                    )
                except:
                    pass

    await dm_pairings_round()
    await ctx.send(f"🏁 Torneio iniciado com {len(players)} jogadores — rodadas: {torneio_data['rounds_target']}.")
    await atualizar_painel()

@bot.command(name="statustorneio")
async def status_torneio(ctx):
    if not torneio_data.get("active"):
        await ctx.send("❌ Nenhum torneio ativo.")
        return
    txt = f"🏆 **RODADA {torneio_data.get('round')}/{torneio_data.get('rounds_target')}** 🏆\n\n**Confrontos:**\n"
    for pid, p in torneio_data.get("pairings", {}).items():
        res = p.get("result") or "Pendente"
        txt += f"{pid}: <@{p['player1']}> vs <@{p['player2']}> — {res}\n"
    if torneio_data.get("byes"):
        txt += "\n**Byes:** " + ", ".join([f"<@{u}>" for u in torneio_data["byes"]]) + "\n"
    await ctx.send(txt)

@bot.command(name="proximarodada")
async def avancar_rodada(ctx):
    if ctx.author.id != BOT_OWNER:
        await ctx.send("❌ Apenas o dono pode avançar rodadas.")
        return
    if not torneio_data.get("active"):
        await ctx.send("❌ Nenhum torneio ativo.")
        return
    if torneio_data.get("round", 0) >= torneio_data.get("rounds_target", 0):
        # finalize
        torneio_data["active"] = False
        torneio_data["finished"] = True
        scores = torneio_data.get("scores", {})
        if scores:
            champion_id, champ_score = max(scores.items(), key=lambda kv: kv[1])
            torneio_data.setdefault("tournament_champions", {})
            torneio_data["tournament_champions"][str(champion_id)] = torneio_data["tournament_champions"].get(str(champion_id), 0) + 1
            ranking["scores_torneio"][str(champion_id)] = ranking["scores_torneio"].get(str(champion_id), 0) + 1
            ch = bot.get_channel(PANEL_CHANNEL_ID)
            if ch:
                await ch.send(f"🏆 Torneio finalizado! Campeão: <@{champion_id}> com {champ_score} pontos. Parabéns!")
            owner = await safe_fetch_user(BOT_OWNER)
            if owner:
                try:
                    await owner.send(f"🏆 Torneio finalizado! Campeão: <@{champion_id}> — {champ_score} pts.")
                except:
                    pass
        save_json(RANKING_FILE, ranking)
        save_json(TORNEIO_FILE, torneio_data)
        await atualizar_painel()
        return

    # advance round
    torneio_data["round"] += 1
    torneio_data["byes"] = []
    await gerar_pairings_torneio()
    save_json(TORNEIO_FILE, torneio_data)
    await dm_pairings_round()
    await ctx.send(f"➡️ Avançado para rodada {torneio_data['round']} — pairings enviados por DM.")
    await atualizar_painel()

@bot.command(name="resetartorneio")
async def reset_torneio_cmd(ctx):
    if ctx.author.id != BOT_OWNER:
        await ctx.send("❌ Apenas o dono pode resetar o torneio.")
        return
    torneio_data.update({
        "active": False,
        "inscriptions_open": False,
        "players": [],
        "decklists": {},
        "round": 0,
        "rounds_target": None,
        "pairings": {},
        "scores": {},
        "played": {},
        "byes": [],
        "finished": False,
        "inscription_message_id": 0
    })
    save_json(TORNEIO_FILE, torneio_data)
    await ctx.send("✅ Torneio resetado (sem registrar campeão).")
    await atualizar_painel()

@bot.command(name="resetranking")
async def reset_ranking_cmd(ctx, scope: str = "1x1"):
    if ctx.author.id != BOT_OWNER:
        await ctx.send("❌ Apenas o dono pode resetar rankings.")
        return
    if scope.lower() in ("1x1", "1x", "fila"):
        ranking["scores_1x1"] = {}
        ranking["__last_reset"] = datetime.datetime.utcnow().isoformat()
        save_json(RANKING_FILE, ranking)
        await ctx.send("🔄 Ranking 1x1 resetado manualmente.")
    elif scope.lower() in ("torneio", "t"):
        ranking["scores_torneio"] = {}
        save_json(RANKING_FILE, ranking)
        await ctx.send("🔄 Ranking de torneios resetado manualmente.")
    else:
        await ctx.send("Uso: `!resetranking 1x1` ou `!resetranking torneio`")

@bot.command(name="verranking")
async def ver_ranking_cmd(ctx):
    user = ctx.author
    try:
        s_1x1 = sorted(ranking.get("scores_1x1", {}).items(), key=lambda kv: kv[1], reverse=True)
        lines = ["🏅 **Ranking 1x1** 🏅\n"]
        for i, (uid, pts) in enumerate(s_1x1[:20], 1):
            lines.append(f"{i}. <@{uid}> — {pts} vitórias")
        if not s_1x1:
            lines.append("Nenhuma partida registrada ainda.")
        await user.send("\n".join(lines))

        ask_msg = await user.send("Deseja visualizar também o ranking de torneios? Reaja com ➡️ para sim ou ❌ para não.")
        try:
            await ask_msg.add_reaction(EMOJI_YES)
            await ask_msg.add_reaction(EMOJI_X)
        except:
            pass

        def check(reaction, usr):
            return usr.id == user.id and reaction.message.id == ask_msg.id and str(reaction.emoji) in (EMOJI_YES, EMOJI_X)

        try:
            reaction, usr = await bot.wait_for("reaction_add", check=check, timeout=60)
            if str(reaction.emoji) == EMOJI_YES:
                s_t = sorted(ranking.get("scores_torneio", {}).items(), key=lambda kv: kv[1], reverse=True)
                lines2 = ["🏆 **Ranking de Torneios (campeões)** 🏆\n"]
                for i, (uid, wins) in enumerate(s_t[:20], 1):
                    lines2.append(f"{i}. <@{uid}> — {wins} campeonatos")
                if not s_t:
                    lines2.append("Nenhum campeão registrado ainda.")
                await user.send("\n".join(lines2))
            else:
                await user.send("👍 Ok, não exibirei o ranking de torneios.")
        except asyncio.TimeoutError:
            await user.send("⌛ Tempo esgotado. Não será exibido ranking de torneios.")
    except Exception as e:
        await ctx.send("❌ Erro ao enviar ranking via DM.")
        print(Fore.RED + "[RANKING] erro:", e)

@bot.command(name="ff")
async def ff_cmd(ctx):
    uid = ctx.author.id
    if uid not in torneio_data.get("players", []):
        await ctx.send("❌ Você não está inscrito neste torneio.")
        return
    try:
        torneio_data["players"].remove(uid)
    except ValueError:
        pass
    # award opponent points for current pairings
    for pid, p in list(torneio_data.get("pairings", {}).items()):
        if p.get("player1") == uid or p.get("player2") == uid:
            other = p.get("player2") if p.get("player1") == uid else p.get("player1")
            torneio_data.setdefault("scores", {})[str(other)] = torneio_data.get("scores", {}).get(str(other), 0) + 1
            p["result"] = f"Vitória por abandono — <@{other}>"
            torneio_data["pairings"].pop(pid, None)
    torneio_data.setdefault("byes", []).append(uid)
    save_json(TORNEIO_FILE, torneio_data)
    await ctx.send("⚠️ Você abandonou o torneio. Próximos adversários receberam ponto (bye).")
    await atualizar_painel()

# ---------------- REPORTAR RESULTADOS (confirmação dupla) ----------------
@bot.command(name="reportar")
async def reportar_cmd(ctx, match_id: str, resultado: str):
    resultado = resultado.lower()
    if resultado not in ("vitoria", "derrota", "empate"):
        await ctx.send("⚠️ Resultado inválido. Use: vitoria / derrota / empate")
        return

    partida = partidas_ativas.get(match_id) or torneio_data.get("pairings", {}).get(match_id)
    if not partida:
        await ctx.send("❌ Partida não encontrada.")
        return

    uid = ctx.author.id
    if uid not in (partida.get("player1"), partida.get("player2")):
        await ctx.send("❌ Você não está nesta partida.")
        return

    partida.setdefault("attempts", {})[str(uid)] = resultado
    # persist to proper store
    if match_id in partidas_ativas:
        partidas_ativas[match_id] = partida
    else:
        torneio_data["pairings"][match_id] = partida
        save_json(TORNEIO_FILE, torneio_data)

    opponent = partida["player2"] if uid == partida["player1"] else partida["player1"]
    opp_res = partida.get("attempts", {}).get(str(opponent))
    if opp_res:
        # both reported
        my_res = partida["attempts"][str(uid)]
        if my_res == opp_res:
            # they agree
            if my_res == "vitoria":
                winner = uid
                loser = opponent
            elif my_res == "derrota":
                winner = opponent
                loser = uid
            else:
                winner = None
                loser = None  # tie

            ts = timestamp_now_iso()
            if winner:
                historico.append({"winner": winner, "loser": loser, "timestamp": ts, "match_id": match_id, "source": partida.get("source", "fila")})
                if partida.get("source") == "fila":
                    ranking.setdefault("scores_1x1", {})[str(winner)] = ranking.get("scores_1x1", {}).get(str(winner), 0) + 1
                else:
                    torneio_data.setdefault("scores", {})[str(winner)] = torneio_data.get("scores", {}).get(str(winner), 0) + 1
            else:
                historico.append({"winner": None, "loser": None, "timestamp": ts, "match_id": match_id, "source": partida.get("source", "fila"), "tie": True})

            # cleanup
            partidas_ativas.pop(match_id, None)
            if match_id in torneio_data.get("pairings", {}):
                torneio_data["pairings"].pop(match_id, None)

            save_json(RANKING_FILE, ranking)
            save_json(HISTORICO_FILE, historico)
            save_json(TORNEIO_FILE, torneio_data)

            # notify both
            u1 = await safe_fetch_user(partida["player1"])
            u2 = await safe_fetch_user(partida["player2"])
            notify = f"✅ Resultado confirmado: {'Empate' if winner is None else f'<@{winner}> venceu <@{loser}>'} (match {match_id})"
            for u in (u1, u2):
                if u:
                    try:
                        await u.send(notify)
                    except:
                        pass
            await atualizar_painel()
            await ctx.send("✅ Resultado confirmado (ambos concordaram).")
        else:
            # disagreement -> notify both
            u1 = await safe_fetch_user(partida["player1"])
            u2 = await safe_fetch_user(partida["player2"])
            for u in (u1, u2):
                if u:
                    try:
                        await u.send("⚠️ Relatórios divergentes. Conversem e reenviem o mesmo resultado.")
                    except:
                        pass
            await ctx.send("⚠️ Relatórios divergentes. Ambos devem reportar o mesmo resultado.")
    else:
        await ctx.send("✅ Seu resultado foi registrado. Aguardando confirmação do adversário.")

# ---------------- CANCELAMENTO DE PARTIDA (pedido + confirmação) ----------------
@bot.command(name="cancelarpartida")
async def cancelar_partida_cmd(ctx, match_id: str):
    partida = partidas_ativas.get(match_id) or torneio_data.get("pairings", {}).get(match_id)
    if not partida:
        await ctx.send("❌ Partida não encontrada.")
        return
    uid = ctx.author.id
    if uid not in (partida.get("player1"), partida.get("player2")):
        await ctx.send("❌ Você não participa desta partida.")
        return

    confirm_msg = await ctx.send(f"⚠️ Tem certeza que deseja solicitar cancelamento de {match_id}? Reaja com {EMOJI_YES} para confirmar ou {EMOJI_NO} para cancelar.")
    try:
        await confirm_msg.add_reaction(EMOJI_YES)
        await confirm_msg.add_reaction(EMOJI_NO)
    except:
        pass

    def check(reaction, user): return user.id == uid and reaction.message.id == confirm_msg.id and str(reaction.emoji) in (EMOJI_YES, EMOJI_NO)
    try:
        reaction, user = await bot.wait_for("reaction_add", check=check, timeout=30)
        if str(reaction.emoji) == EMOJI_NO:
            await ctx.send("✋ Cancelamento abortado.")
            return
    except asyncio.TimeoutError:
        await ctx.send("⌛ Tempo esgotado. Pedido abortado.")
        return

    # register cancel attempt
    partida.setdefault("cancel_attempts", {})[str(uid)] = True
    opponent = partida["player2"] if uid == partida["player1"] else partida["player1"]

    # ask opponent via DM
    op_user = await safe_fetch_user(opponent)
    if op_user:
        try:
            msg = await op_user.send(f"⚠️ <@{uid}> solicitou cancelar a partida {match_id}. Reaja com {EMOJI_YES} para confirmar ou {EMOJI_NO} para negar.")
            await msg.add_reaction(EMOJI_YES); await msg.add_reaction(EMOJI_NO)
        except:
            pass

    def check_op(reaction, user): return user.id == opponent and str(reaction.emoji) in (EMOJI_YES, EMOJI_NO)
    try:
        reaction, user = await bot.wait_for("reaction_add", check=check_op, timeout=60)
        if str(reaction.emoji) == EMOJI_YES:
            partidas_ativas.pop(match_id, None)
            if match_id in torneio_data.get("pairings", {}):
                torneio_data["pairings"].pop(match_id, None)
            save_json(TORNEIO_FILE, torneio_data)
            await ctx.send("✅ Partida cancelada por acordo de ambos os jogadores.")
            p1u = await safe_fetch_user(partida["player1"]); p2u = await safe_fetch_user(partida["player2"])
            for u in (p1u, p2u):
                if u:
                    try:
                        await u.send(f"✅ A partida {match_id} foi cancelada por acordo de ambos.")
                    except:
                        pass
            await atualizar_painel()
        else:
            await ctx.send("❌ O adversário negou o cancelamento. A partida permanece ativa.")
    except asyncio.TimeoutError:
        await ctx.send("⌛ Tempo esgotado aguardando resposta do adversário.")

# ---------------- HELP ----------------
@bot.command(name="ajuda")
async def ajuda_cmd(ctx):
    help_text = (
        "🎮 **Comandos OPTTCG** 🎮\n\n"
        "`!mostrerfila` — Mostra mensagem para entrar/sair da fila 1x1\n"
        "`Reaja no painel com ✅ para entrar / ❌ para sair` — Entrar/saír da fila\n"
        "`!reportar <match_id> <vitoria|derrota|empate>` — Reportar resultado (confirmação mútua)\n"
        "`!cancelarpartida <match_id>` — Solicitar cancelamento (confirmação do adversário)\n"
        "`!verranking` — Recebe ranking 1x1 via DM (pergunta sobre ranking de torneios)\n"
        "`!torneio` (admin) — Abre inscrições (reaja 🏆 para entrar)\n"
        "`!fecharinscricoes` (admin)\n"
        "`!começartorneio` (admin)\n"
        "`!statustorneio` (admin)\n"
        "`!proximarodada` (admin)\n"
        "`!resetartorneio` (admin)\n"
        "`!resetranking <1x1|torneio>` (admin)\n"
        "`!ff` — Abandonar torneio (player)\n"
    )
    await ctx.send(help_text)

# ---------------- REACTION HANDLER (panel, inscription) ----------------
@bot.event
async def on_reaction_add(reaction, user):
    if user.bot:
        return
    try:
        # panel reactions
        if reaction.message.id == PANEL_MESSAGE_ID:
            if str(reaction.emoji) == EMOJI_CHECK:
                if user.id not in fila:
                    fila.append(user.id)
                    try: await user.send("✅ Você entrou na fila 1x1. Aguarde emparelhamento.")
                    except: pass
                    await atualizar_painel()
            elif str(reaction.emoji) == EMOJI_X:
                if user.id in fila:
                    fila.remove(user.id)
                    try: await user.send("❌ Você saiu da fila 1x1.")
                    except: pass
                    await atualizar_painel()
            try: await reaction.remove(user)
            except: pass

        # inscription reactions
        if torneio_data.get("inscription_message_id") and reaction.message.id == torneio_data.get("inscription_message_id"):
            if str(reaction.emoji) == EMOJI_TROPHY and torneio_data.get("inscriptions_open"):
                if user.id not in torneio_data.get("players", []):
                    torneio_data["players"].append(user.id)
                    torneio_data["decklists"].pop(str(user.id), None)
                    save_json(TORNEIO_FILE, torneio_data)
                    try:
                        await user.send("✅ Inscrição recebida! Aguarde instruções por DM.")
                    except: pass
                    await atualizar_painel()
                try: await reaction.remove(user)
                except: pass
    except Exception:
        pass

# ---------------- STARTUP / READY ----------------
@bot.event
async def on_ready():
    # save loop started in setup_hook, but ensure panel update
    await atualizar_painel()
    print(Fore.GREEN + f"[READY] {bot.user} (id: {bot.user.id})")

# ---------------- ENTRY POINT ----------------
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print(Fore.RED + "❌ DISCORD_TOKEN não definido nas variáveis de ambiente. Configure e reinicie.")
    else:
        try:
            bot.run(DISCORD_TOKEN)
        except Exception as e:
            print(Fore.RED + "❌ Erro ao iniciar o bot:", e)
