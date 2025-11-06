# bot.py — OPTCG Sorocaba (completo, atualizado)
# - Painel com Embed + mostrar/ocultar inscritos
# - Report via reação em DM (1️⃣ = player1, 2️⃣ = player2, ➖ = empate)
# - !cancelarpartida (sem match_id) -> confirmações via DM com reações
# - setup_hook() usado para iniciar tasks (discord.py 2.x compatível)
# - keep-alive aiohttp server para Render Web Service
# Requer: discord.py>=2.4.0, aiohttp, colorama, python-dotenv (opcional)

import os
import json
import math
import asyncio
import datetime
from pathlib import Path

import discord
from discord.ext import commands, tasks
from aiohttp import web
from colorama import init as colorama_init, Fore

# Optional .env loading for local testing
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

# ---------------- STORAGE ----------------
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
    "inscription_message_id": 0
})
historico = load_json(HISTORICO_FILE, [])

fila = []  # list of user ids
partidas_ativas = {}  # match_id -> {player1, player2, attempts, cancel_attempts, source, timestamp}
PANEL_MESSAGE_ID = 0
mostrar_inscritos = True

# ---------------- INTENTS & BOT ----------------
intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.members = True  # ensure enabled in dev portal

class TournamentBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents, help_command=None)

    async def setup_hook(self):
        # start background services safely
        asyncio.create_task(start_webserver())
        if not save_states.is_running():
            save_states.start()
        if not daily_reset_check.is_running():
            daily_reset_check.start()
        asyncio.create_task(fila_worker())

bot = TournamentBot()

# ---------------- EMOJIS ----------------
EMOJI_CHECK = "✅"
EMOJI_X = "❌"
EMOJI_TROPHY = "🏆"
EMOJI_SHOW = "👁️"
EMOJI_HIDE = "🙈"
EMOJI_ONE = "1️⃣"
EMOJI_TWO = "2️⃣"
EMOJI_TIE = "➖"
EMOJI_YES = "➡️"
EMOJI_NO = "❌"

# ---------------- UTIL ----------------
async def safe_fetch_user(uid: int):
    try:
        return await bot.fetch_user(uid)
    except Exception:
        return None

def now_iso():
    return datetime.datetime.utcnow().isoformat()

# ---------------- WEB SERVER (keep-alive) ----------------
async def _handle_root(request):
    return web.Response(text="OPTCG Sorocaba Bot — running")

async def start_webserver():
    try:
        app = web.Application()
        app.add_routes([web.get("/", _handle_root)])
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", PORT)
        await site.start()
        print(Fore.CYAN + f"[WEB] keep-alive server listening on 0.0.0.0:{PORT}")
    except Exception as e:
        print(Fore.RED + f"[WEB] failed to start: {e}")

# ---------------- PANEL (Embed prettier) ----------------
def build_panel_embed():
    embed = discord.Embed(title="🎮 OPTCG Sorocaba — Painel Geral 🎮",
                          description="Painel de filas, partidas e torneios",
                          color=0x1abc9c,
                          timestamp=datetime.datetime.utcnow())
    # Fila
    if fila:
        fila_text = "\n".join([f"• <@{u}>" for u in fila])
    else:
        fila_text = "Vazia"
    embed.add_field(name="🟢 Fila 1x1", value=fila_text, inline=False)

    # Partidas em andamento
    if partidas_ativas:
        part_lines = []
        for mid, p in list(partidas_ativas.items())[:12]:
            part_lines.append(f"• <@{p['player1']}> vs <@{p['player2']}>")
        partidas_text = "\n".join(part_lines)
    else:
        partidas_text = "Nenhuma"
    embed.add_field(name="⚔️ Partidas em andamento", value=partidas_text, inline=False)

    # Últimas 3 partidas
    if historico:
        last = historico[-3:]
        last_lines = []
        for h in last:
            if h.get("tie"):
                last_lines.append(f"• Empate — {h.get('match_id', '')}")
            else:
                last_lines.append(f"• <@{h['winner']}> venceu <@{h['loser']}>")
        ult_text = "\n".join(last_lines)
    else:
        ult_text = "Nenhuma"
    embed.add_field(name="🕘 Últimas 3 partidas", value=ult_text, inline=False)

    # Inscritos (may be hidden)
    if mostrar_inscritos and torneio_data.get("players"):
        ins_lines = [f"• <@{u}>" for u in torneio_data.get("players", [])[:30]]
        inscritos_text = "\n".join(ins_lines)
    else:
        inscritos_text = "Oculto" if not mostrar_inscritos else "Nenhum inscrito"
    embed.add_field(name="🏆 Inscritos Torneio", value=inscritos_text, inline=False)

    embed.set_footer(text="Reaja: ✅ entrar | ❌ sair | 👁️ mostrar inscritos | 🙈 ocultar inscritos")
    return embed

async def atualizar_painel():
    global PANEL_MESSAGE_ID
    try:
        if PANEL_CHANNEL_ID == 0:
            return
        ch = bot.get_channel(PANEL_CHANNEL_ID)
        if not ch:
            return
        embed = build_panel_embed()
        if PANEL_MESSAGE_ID == 0:
            msg = await ch.send(embed=embed)
            PANEL_MESSAGE_ID = msg.id
            # add reactions: enter, leave, show, hide
            try:
                await msg.add_reaction(EMOJI_CHECK)
                await msg.add_reaction(EMOJI_X)
                await msg.add_reaction(EMOJI_SHOW)
                await msg.add_reaction(EMOJI_HIDE)
            except Exception:
                pass
        else:
            try:
                msg = await ch.fetch_message(PANEL_MESSAGE_ID)
                await msg.edit(embed=embed)
            except discord.NotFound:
                msg = await ch.send(embed=embed)
                PANEL_MESSAGE_ID = msg.id
                try:
                    await msg.add_reaction(EMOJI_CHECK)
                    await msg.add_reaction(EMOJI_X)
                    await msg.add_reaction(EMOJI_SHOW)
                    await msg.add_reaction(EMOJI_HIDE)
                except Exception:
                    pass
    except Exception as e:
        print(Fore.RED + f"[PAINEL] erro ao atualizar: {e}")

# ---------------- PERSIST / PERIODIC TASKS ----------------
@tasks.loop(minutes=5)
async def save_states():
    save_json(RANKING_FILE, ranking)
    save_json(TORNEIO_FILE, torneio_data)
    save_json(HISTORICO_FILE, historico)
    # print(Fore.GREEN + "[SAVE] states saved")

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
                    await owner.send("🔄 Rankings 1x1 resetados automaticamente (dia 1).")
                except:
                    pass
    except Exception as e:
        print(Fore.RED + "[DAILY] error:" , e)

# ---------------- FILA WORKER (matchmaking 1x1) ----------------
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
                    "attempts": {},  # str(uid) -> emoji choice (EMOJI_ONE/EMOJI_TWO/EMOJI_TIE)
                    "cancel_attempts": {},
                    "source": "fila",
                    "timestamp": now_iso()
                }
                # DM both players with reaction poll
                await send_result_poll(match_id, partidas_ativas[match_id])
                await atualizar_painel()
        except Exception as e:
            print(Fore.RED + "[FILA WORKER] erro:", e)
        await asyncio.sleep(3)

# ---------------- TORNEIO SUÍÇO (simplified swiss pairing) ----------------
def calcular_rodadas(n):
    base = math.ceil(math.log2(max(1, n)))
    return max(1, base - 1) if n > 1 else 1

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
                        f"🏁 Rodada {torneio_data.get('round',1)} — Confronto:\n<@{p1}> vs <@{p2}>\n"
                        f"Reporte o resultado reagindo à mensagem que eu enviei (1️⃣ = {p1} venceu; 2️⃣ = {p2} venceu; ➖ = empate)."
                    )
                except:
                    pass
    # send poll for each pairing as well
    for pid, pairing in list(torneio_data.get("pairings", {}).items()):
        await send_result_poll(pid, pairing)

# ---------------- SEND RESULT POLL (DM) ----------------
async def send_result_poll(match_id: str, partida: dict):
    """Sends a DM to both players with reactions 1/2/➖ to report result.
       When both players react same choice -> confirm result; if disagree -> notify both."""
    p1 = partida["player1"]; p2 = partida["player2"]
    # Compose message content showing mentions and instructions
    content = (
        f"⚔️ Partida: <@{p1}> vs <@{p2}>\n\n"
        f"Quem venceu? Reaja:\n"
        f"{EMOJI_ONE} — <@{p1}>\n"
        f"{EMOJI_TWO} — <@{p2}>\n"
        f"{EMOJI_TIE} — Empate\n\n"
        f"Observação: Resultado só será confirmado se ambos reagirem a mesma opção."
    )
    # send message and add reactions for both players
    sent_messages = []
    for uid in (p1, p2):
        u = await safe_fetch_user(uid)
        if not u:
            continue
        try:
            msg = await u.send(content)
            try:
                await msg.add_reaction(EMOJI_ONE)
                await msg.add_reaction(EMOJI_TWO)
                await msg.add_reaction(EMOJI_TIE)
            except:
                pass
            sent_messages.append((uid, msg.id))
        except Exception:
            pass
    # store the poll message ids to track optionally (not strictly required)
    partida.setdefault("polls", []).extend(sent_messages)
    return

# ---------------- ON_MESSAGE (decklist capture) ----------------
@bot.event
async def on_message(message):
    if message.author.bot:
        return
    # capture decklist in DM when user is registered
    if isinstance(message.channel, discord.DMChannel):
        uid = message.author.id
        if uid in torneio_data.get("players", []) and str(uid) not in torneio_data.get("decklists", {}):
            torneio_data["decklists"][str(uid)] = message.content
            try:
                (DECKLIST_PATH / f"{uid}.txt").write_text(message.content, encoding="utf-8")
            except:
                pass
            try:
                await message.author.send("✅ Decklist recebida e armazenada.")
            except:
                pass
            save_json(TORNEIO_FILE, torneio_data)
            # If all decklists collected -> compile and send to owner
            all_players = set(map(str, torneio_data.get("players", [])))
            received = set(torneio_data.get("decklists", {}).keys())
            if all_players and all_players.issubset(received):
                combined_list = []
                for pid in torneio_data["players"]:
                    s = torneio_data["decklists"].get(str(pid), "")
                    combined_list.append(f"Player: {pid}\nDiscord: <@{pid}>\nDecklist:\n{s}\n\n---\n\n")
                combined_text = "".join(combined_list)
                combined_path = DECKLIST_PATH / f"decklists_{int(datetime.datetime.utcnow().timestamp())}.txt"
                try:
                    combined_path.write_text(combined_text, encoding="utf-8")
                except:
                    pass
                owner = await safe_fetch_user(BOT_OWNER)
                if owner:
                    try:
                        await owner.send("📦 Todas as decklists recebidas — arquivo em anexo:", file=discord.File(str(combined_path)))
                    except:
                        try:
                            await owner.send("Todas as decklists recebidas — (falha ao enviar arquivo).")
                        except:
                            pass
            return
    await bot.process_commands(message)

# ---------------- REACTION HANDLER (panel + inscription + DM polls) ----------------
@bot.event
async def on_reaction_add(reaction, user):
    if user.bot:
        return
    # Panel reactions
    try:
        if reaction.message.id == PANEL_MESSAGE_ID:
            emoji = str(reaction.emoji)
            # Enter queue
            if emoji == EMOJI_CHECK:
                if user.id not in fila:
                    fila.append(user.id)
                    try: await user.send("✅ Você entrou na fila 1x1. Aguarde emparelhamento.")
                    except: pass
                    await atualizar_painel()
            # Leave queue
            elif emoji == EMOJI_X:
                if user.id in fila:
                    fila.remove(user.id)
                    try: await user.send("❌ Você saiu da fila 1x1.")
                    except: pass
                    await atualizar_painel()
            # Show inscritos
            elif emoji == EMOJI_SHOW:
                global mostrar_inscritos
                mostrar_inscritos = True
                await atualizar_painel()
            # Hide inscritos
            elif emoji == EMOJI_HIDE:
                mostrar_inscritos = False
                await atualizar_painel()
            # remove reaction so user can click again
            try: await reaction.remove(user)
            except: pass
    except Exception:
        pass

    # Tournament inscription reaction
    try:
        if torneio_data.get("inscription_message_id") and reaction.message.id == torneio_data.get("inscription_message_id"):
            if str(reaction.emoji) == EMOJI_TROPHY and torneio_data.get("inscriptions_open"):
                if user.id not in torneio_data.get("players", []):
                    torneio_data["players"].append(user.id)
                    torneio_data["decklists"].pop(str(user.id), None)
                    save_json(TORNEIO_FILE, torneio_data)
                    try:
                        await user.send("✅ Você foi inscrito no torneio. Aguarde instruções por DM.")
                    except:
                        pass
                    await atualizar_painel()
                try: await reaction.remove(user)
                except: pass
    except Exception:
        pass

    # DM poll reactions: determine if reaction belongs to a poll message we sent earlier
    # We'll check if this user has an active match and if the reaction is one of EMOJI_ONE/EMOJI_TWO/EMOJI_TIE
    try:
        if str(reaction.emoji) in (EMOJI_ONE, EMOJI_TWO, EMOJI_TIE):
            # find the match where this user participates and is active
            for mid, p in partidas_ativas.items():
                if user.id in (p.get("player1"), p.get("player2")):
                    # only consider if poll was sent (we added polls in partida['polls'])
                    # register user's choice
                    p.setdefault("attempts", {})[str(user.id)] = str(reaction.emoji)
                    # save back
                    partidas_ativas[mid] = p
                    await check_and_process_match_result(mid, p)
                    break
            # also check tournament pairings
            for mid, p in list(torneio_data.get("pairings", {}).items()):
                if user.id in (p.get("player1"), p.get("player2")):
                    p.setdefault("attempts", {})[str(user.id)] = str(reaction.emoji)
                    torneio_data["pairings"][mid] = p
                    await check_and_process_torneio_result(mid, p)
                    break
    except Exception as e:
        print(Fore.RED + f"[REACTION POLL] erro: {e}")

# ---------------- CHECK & PROCESS RESULT (fila + torneio) ----------------
async def check_and_process_match_result(match_id: str, partida: dict):
    """Check attempts for fila matches"""
    try:
        attempts = partida.get("attempts", {})
        players = (partida["player1"], partida["player2"])
        if str(players[0]) in attempts and str(players[1]) in attempts:
            choice1 = attempts.get(str(players[0]))
            choice2 = attempts.get(str(players[1]))
            if choice1 == choice2:
                # agreement
                await finalize_match_result(match_id, partida, choice1)
            else:
                # disagreement -> notify both to talk and resubmit
                u1 = await safe_fetch_user(players[0])
                u2 = await safe_fetch_user(players[1])
                for u in (u1, u2):
                    if u:
                        try:
                            await u.send("⚠️ Detectamos relatórios divergentes. Conversem e reenviem o mesmo resultado (reaja novamente na mensagem de DM).")
                        except:
                            pass
    except Exception as e:
        print(Fore.RED + f"[CHECK MATCH] {e}")

async def check_and_process_torneio_result(match_id: str, partida: dict):
    """Check attempts for tournament pairings"""
    try:
        attempts = partida.get("attempts", {})
        p1 = partida["player1"]; p2 = partida["player2"]
        if str(p1) in attempts and str(p2) in attempts:
            choice1 = attempts.get(str(p1))
            choice2 = attempts.get(str(p2))
            if choice1 == choice2:
                await finalize_torneio_result(match_id, partida, choice1)
            else:
                # notify disagreement
                u1 = await safe_fetch_user(p1); u2 = await safe_fetch_user(p2)
                for u in (u1, u2):
                    if u:
                        try:
                            await u.send("⚠️ Detectamos relatórios divergentes. Conversem e reenviem o mesmo resultado (reaja novamente na mensagem de DM).")
                        except:
                            pass
    except Exception as e:
        print(Fore.RED + f"[CHECK TORNEIO] {e}")

# ---------------- FINALIZE RESULT HANDLERS ----------------
async def finalize_match_result(match_id: str, partida: dict, choice_emoji: str):
    try:
        p1 = partida["player1"]; p2 = partida["player2"]
        if choice_emoji == EMOJI_ONE:
            winner, loser = p1, p2
        elif choice_emoji == EMOJI_TWO:
            winner, loser = p2, p1
        else:
            winner, loser = None, None  # tie

        ts = now_iso()
        if winner:
            historico.append({"winner": winner, "loser": loser, "timestamp": ts, "match_id": match_id, "source": partida.get("source", "fila")})
            ranking.setdefault("scores_1x1", {})[str(winner)] = ranking.get("scores_1x1", {}).get(str(winner), 0) + 1
        else:
            historico.append({"winner": None, "loser": None, "timestamp": ts, "match_id": match_id, "source": partida.get("source", "fila"), "tie": True})

        partidas_ativas.pop(match_id, None)
        save_json(RANKING_FILE, ranking)
        save_json(HISTORICO_FILE, historico)

        # notify players
        u1 = await safe_fetch_user(p1); u2 = await safe_fetch_user(p2)
        note = f"✅ Resultado confirmado: {'Empate' if winner is None else f'<@{winner}> venceu <@{loser}>'} (match {match_id})"
        for u in (u1, u2):
            if u:
                try:
                    await u.send(note)
                except:
                    pass
        await atualizar_painel()
    except Exception as e:
        print(Fore.RED + f"[FINALIZE MATCH] {e}")

async def finalize_torneio_result(match_id: str, partida: dict, choice_emoji: str):
    try:
        p1 = partida["player1"]; p2 = partida["player2"]
        if choice_emoji == EMOJI_ONE:
            winner, loser = p1, p2
        elif choice_emoji == EMOJI_TWO:
            winner, loser = p2, p1
        else:
            winner, loser = None, None

        ts = now_iso()
        if winner:
            historico.append({"winner": winner, "loser": loser, "timestamp": ts, "match_id": match_id, "source": "torneio"})
            torneio_data.setdefault("scores", {})[str(winner)] = torneio_data.get("scores", {}).get(str(winner), 0) + 1
        else:
            historico.append({"winner": None, "loser": None, "timestamp": ts, "match_id": match_id, "source": "torneio", "tie": True})

        # remove pairing
        torneio_data.get("pairings", {}).pop(match_id, None)
        save_json(TORNEIO_FILE, torneio_data)
        save_json(HISTORICO_FILE, historico)
        await atualizar_painel()

        # notify players
        u1 = await safe_fetch_user(p1); u2 = await safe_fetch_user(p2)
        note = f"✅ Resultado confirmado: {'Empate' if winner is None else f'<@{winner}> venceu <@{loser}>'} (match {match_id})"
        for u in (u1, u2):
            if u:
                try:
                    await u.send(note)
                except:
                    pass
    except Exception as e:
        print(Fore.RED + f"[FINALIZE TORNEIO] {e}")

# ---------------- CANCEL PARTIDA (no match_id) ----------------
@bot.command(name="cancelarpartida")
async def cancelar_partida_cmd(ctx):
    uid = ctx.author.id
    # find an active match for this user in partidas_ativas or torneio pairings
    found_mid = None
    found_part = None
    # search active matches
    for mid, p in partidas_ativas.items():
        if uid in (p.get("player1"), p.get("player2")):
            found_mid = mid; found_part = p; break
    # search tournament pairings if not found
    if not found_part:
        for mid, p in torneio_data.get("pairings", {}).items():
            if uid in (p.get("player1"), p.get("player2")):
                found_mid = mid; found_part = p; break
    if not found_part:
        await ctx.send("❌ Você não está em nenhuma partida ativa no momento.")
        return

    # Ask initiator to confirm (in channel)
    confirm_msg = await ctx.send(f"⚠️ Tem certeza que deseja solicitar cancelamento da sua partida atual? Reaja com {EMOJI_YES} para confirmar ou {EMOJI_NO} para cancelar.")
    try:
        await confirm_msg.add_reaction(EMOJI_YES)
        await confirm_msg.add_reaction(EMOJI_NO)
    except:
        pass

    def check_self(reaction, user):
        return user.id == uid and reaction.message.id == confirm_msg.id and str(reaction.emoji) in (EMOJI_YES, EMOJI_NO)

    try:
        reaction, user = await bot.wait_for("reaction_add", check=check_self, timeout=30)
        if str(reaction.emoji) == EMOJI_NO:
            await ctx.send("✋ Pedido de cancelamento abortado.")
            return
    except asyncio.TimeoutError:
        await ctx.send("⌛ Tempo esgotado. Pedido de cancelamento cancelado.")
        return

    # register attempt and DM opponent
    partida = found_part
    opponent = partida["player2"] if uid == partida["player1"] else partida["player1"]
    partida.setdefault("cancel_attempts", {})[str(uid)] = True

    op_user = await safe_fetch_user(opponent)
    if not op_user:
        await ctx.send("❌ Não foi possível contatar o adversário via DM. Cancelamento não processado.")
        return

    try:
        dm = await op_user.send(f"⚠️ <@{uid}> solicitou cancelar a partida. Reaja com {EMOJI_YES} para confirmar cancelamento, ou {EMOJI_NO} para negar.")
        try:
            await dm.add_reaction(EMOJI_YES); await dm.add_reaction(EMOJI_NO)
        except:
            pass
    except:
        await ctx.send("❌ Falha ao enviar DM para o adversário. Cancelamento não processado.")
        return

    def check_op(reaction, user):
        return user.id == opponent and reaction.message.id == dm.id and str(reaction.emoji) in (EMOJI_YES, EMOJI_NO)

    try:
        reaction, user = await bot.wait_for("reaction_add", check=check_op, timeout=60)
        if str(reaction.emoji) == EMOJI_YES:
            # cancel match
            partidas_ativas.pop(found_mid, None)
            if found_mid in torneio_data.get("pairings", {}):
                torneio_data["pairings"].pop(found_mid, None)
            save_json(TORNEIO_FILE, torneio_data)
            await ctx.send("✅ Partida cancelada por acordo entre os jogadores.")
            p1u = await safe_fetch_user(partida["player1"]); p2u = await safe_fetch_user(partida["player2"])
            for u in (p1u, p2u):
                if u:
                    try:
                        await u.send(f"✅ A partida foi cancelada por acordo entre os jogadores.")
                    except:
                        pass
            await atualizar_painel()
        else:
            await ctx.send("❌ O adversário negou o cancelamento. A partida continua.")
    except asyncio.TimeoutError:
        await ctx.send("⌛ Tempo esgotado aguardando resposta do adversário. Pedido expira.")

# ---------------- ABANDONAR TORNEIO (ff) ----------------
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
    # award points to opponents in current pairings
    for pid, p in list(torneio_data.get("pairings", {}).items()):
        if p.get("player1") == uid or p.get("player2") == uid:
            other = p.get("player2") if p.get("player1") == uid else p.get("player1")
            torneio_data.setdefault("scores", {})[str(other)] = torneio_data.get("scores", {}).get(str(other), 0) + 1
            p["result"] = f"Vitória por abandono — <@{other}>"
            torneio_data["pairings"].pop(pid, None)
    torneio_data.setdefault("byes", []).append(uid)
    save_json(TORNEIO_FILE, torneio_data)
    await ctx.send("⚠️ Você abandonou o torneio. Seus adversários receberam ponto (bye).")
    await atualizar_painel()

# ---------------- COMANDOS TORNEIO & PAINEL ----------------
@bot.command(name="novopainel")
async def cmd_novopainel(ctx):
    if ctx.author.id != BOT_OWNER:
        await ctx.send("❌ Apenas o dono do bot pode usar este comando.")
        return
    global PANEL_MESSAGE_ID
    PANEL_MESSAGE_ID = 0
    await atualizar_painel()
    await ctx.send("✅ Painel reiniciado.")

@bot.command(name="torneio")
async def cmd_torneio_open(ctx):
    if ctx.author.id != BOT_OWNER:
        await ctx.send("❌ Apenas o dono pode abrir inscrições.")
        return
    torneio_data["inscriptions_open"] = True
    torneio_data["players"] = []
    torneio_data["decklists"] = {}
    torneio_data["inscription_message_id"] = 0
    msg = await ctx.send("🏆 **TORNEIO ABERTO** — Reaja com 🏆 para se inscrever. Você receberá DM de confirmação.")
    try:
        await msg.add_reaction(EMOJI_TROPHY)
    except:
        pass
    torneio_data["inscription_message_id"] = msg.id
    save_json(TORNEIO_FILE, torneio_data)
    await ctx.send("✅ Torneio aberto.")

@bot.command(name="fecharinscricoes")
async def cmd_fecharinscricoes(ctx):
    if ctx.author.id != BOT_OWNER:
        await ctx.send("❌ Apenas o dono pode fechar inscrições.")
        return
    torneio_data["inscriptions_open"] = False
    save_json(TORNEIO_FILE, torneio_data)
    await ctx.send("🔒 Inscricoes fechadas.")
    await atualizar_painel()

@bot.command(name="começartorneio")
async def cmd_comecar_torneio(ctx):
    if ctx.author.id != BOT_OWNER:
        await ctx.send("❌ Apenas o dono pode iniciar o torneio.")
        return
    players = torneio_data.get("players", [])
    if len(players) < 2:
        await ctx.send("❌ Jogadores insuficientes (minimo 2).")
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
    # request decklists
    for uid in players:
        if str(uid) not in torneio_data.get("decklists", {}):
            u = await safe_fetch_user(uid)
            if u:
                try:
                    await u.send(
                        "✏️ Envie sua decklist aqui (cole a partir do simulador: Copy Deck List to Clipboard)."
                    )
                except:
                    pass
    # send poll for pairings
    await dm_pairings_round()
    await ctx.send(f"🏁 Torneio iniciado com {len(players)} jogadores — rodadas: {torneio_data['rounds_target']}.")
    await atualizar_painel()

@bot.command(name="statustorneio")
async def cmd_statustorneio(ctx):
    if not torneio_data.get("active"):
        await ctx.send("❌ Nenhum torneio ativo.")
        return
    txt = f"🏆 RODADA {torneio_data.get('round')}/{torneio_data.get('rounds_target')} 🏆\n\nConfrontos:\n"
    for pid, p in torneio_data.get("pairings", {}).items():
        txt += f"{pid}: <@{p['player1']}> vs <@{p['player2']}> — {p.get('result') or 'Pendente'}\n"
    if torneio_data.get("byes"):
        txt += "\nByes: " + ", ".join([f"<@{u}>" for u in torneio_data["byes"]]) + "\n"
    await ctx.send(txt)

@bot.command(name="proximarodada")
async def cmd_proxima_rodada(ctx):
    if ctx.author.id != BOT_OWNER:
        await ctx.send("❌ Apenas o dono pode avançar rodadas.")
        return
    if not torneio_data.get("active"):
        await ctx.send("❌ Nenhum torneio ativo.")
        return
    if torneio_data.get("round", 0) >= torneio_data.get("rounds_target", 0):
        # finish
        torneio_data["active"] = False
        torneio_data["finished"] = True
        scores = torneio_data.get("scores", {})
        if scores:
            champ_id, champ_score = max(scores.items(), key=lambda kv: kv[1])
            torneio_data.setdefault("tournament_champions", {})[str(champ_id)] = torneio_data.get("tournament_champions", {}).get(str(champ_id), 0) + 1
            ranking.setdefault("scores_torneio", {})[str(champ_id)] = ranking.get("scores_torneio", {}).get(str(champ_id), 0) + 1
            ch = bot.get_channel(PANEL_CHANNEL_ID)
            if ch:
                await ch.send(f"🏆 Torneio finalizado! Campeão: <@{champ_id}> com {champ_score} pts. Parabéns!")
            owner = await safe_fetch_user(BOT_OWNER)
            if owner:
                try:
                    await owner.send(f"🏆 Torneio finalizado! Campeão: <@{champ_id}> — {champ_score} pts.")
                except:
                    pass
        save_json(RANKING_FILE, ranking)
        save_json(TORNEIO_FILE, torneio_data)
        await atualizar_painel()
        return
    # advance
    torneio_data["round"] += 1
    torneio_data["byes"] = []
    await gerar_pairings_torneio()
    save_json(TORNEIO_FILE, torneio_data)
    await dm_pairings_round()
    await ctx.send(f"➡️ Avançado para rodada {torneio_data['round']}.")
    await atualizar_painel()

@bot.command(name="resetartorneio")
async def cmd_reset_torneio(ctx):
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
    await ctx.send("✅ Torneio resetado.")
    await atualizar_painel()

@bot.command(name="resetranking")
async def cmd_reset_ranking(ctx, scope: str = "1x1"):
    if ctx.author.id != BOT_OWNER:
        await ctx.send("❌ Apenas o dono pode resetar rankings.")
        return
    if scope.lower() in ("1x1", "fila", "1x"):
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
async def cmd_ver_ranking(ctx):
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
            await ask_msg.add_reaction(EMOJI_NO)
        except:
            pass

        def check(reaction, usr):
            return usr.id == user.id and reaction.message.id == ask_msg.id and str(reaction.emoji) in (EMOJI_YES, EMOJI_NO)

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

@bot.command(name="ajuda")
async def cmd_ajuda(ctx):
    help_text = (
        "🎮 **Comandos OPTCG** 🎮\n\n"
        "`Reaja no painel com ✅ para entrar / ❌ para sair da fila 1x1`.\n"
        "`!cancelarpartida` — Solicitar cancelamento (confirmação via DM pelo adversário).\n"
        "`!reportar` removido — report é feito por reação nas DMs que o bot envia após emparelhamento.\n"
        "`!verranking` — Recebe ranking 1x1 via DM\n\n"
        "Admin:\n"
        "`!torneio` — Abrir inscrições\n"
        "`!fecharinscricoes` — Fechar inscrições\n"
        "`!começartorneio` — Iniciar torneio\n"
        "`!statustorneio` — Status do torneio\n"
        "`!proximarodada` — Avançar rodada\n"
        "`!resetartorneio` — Resetar torneio\n"
        "`!resetranking <1x1|torneio>` — Reset ranking\n"
        "`!novopainel` — Reiniciar painel\n"
    )
    await ctx.send(help_text)

# ---------------- ON_READY ----------------
@bot.event
async def on_ready():
    await atualizar_painel()
    print(Fore.GREEN + f"[READY] {bot.user} (id: {bot.user.id})")

# ---------------- START ----------------
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print(Fore.RED + "❌ DISCORD_TOKEN não definido nas variáveis de ambiente.")
    else:
        try:
            bot.run(DISCORD_TOKEN)
        except Exception as e:
            print(Fore.RED + "❌ Erro ao iniciar o bot:", e)
