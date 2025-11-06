# bot.py — OPTCG Sorocaba — Versão final integrada
# Requisitos: discord.py>=2.4.0, aiohttp, colorama, python-dotenv (opcional)
# Variáveis de ambiente: DISCORD_TOKEN, GUILD_ID, PANEL_CHANNEL_ID, BOT_OWNER, PORT (opcional)

import os
import json
import math
import asyncio
import datetime
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands, tasks
from aiohttp import web
from colorama import init as colorama_init, Fore

# load .env optionally
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
    "decklists": {},       # str(uid) -> text
    "deck_confirmed": {},  # str(uid) -> bool
    "round": 0,
    "rounds_target": None,
    "pairings": {},        # match_id -> pairing
    "scores": {},
    "played": {},
    "byes": [],
    "finished": False,
    "inscription_message_id": 0
})
historico = load_json(HISTORICO_FILE, [])

fila = []  # list of user ids
partidas_ativas = {}  # match_id -> dict
poll_message_map = {}  # message_id -> (match_id, user_id) to track DM polls
PANEL_MESSAGE_ID = 0
mostrar_inscritos = True

# ---------------- INTENTS & BOT ----------------
intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.members = True  # enable in dev portal

class TournamentBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents, help_command=None)

    async def setup_hook(self):
        # start tasks safely
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
EMOJI_RANK = "📊"
EMOJI_ONE = "1️⃣"
EMOJI_TWO = "2️⃣"
EMOJI_TIE = "➖"
EMOJI_YES = "➡️"
EMOJI_NO = "❌"
EMOJI_CONFIRM = "✅"
EMOJI_DENY = "❌"

# ---------------- UTIL ----------------
async def safe_fetch_user(uid: int) -> Optional[discord.User]:
    try:
        return await bot.fetch_user(uid)
    except Exception:
        return None

def now_iso():
    return datetime.datetime.utcnow().isoformat()

# ---------------- WEB SERVER (keep-alive for Render) ----------------
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

# ---------------- PANEL (Embed style 1) ----------------
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
    embed.add_field(name="🟦 Fila 1x1", value=fila_text, inline=False)

    # Partidas
    if partidas_ativas:
        part_lines = []
        for mid, p in list(partidas_ativas.items())[:12]:
            part_lines.append(f"• <@{p['player1']}> vs <@{p['player2']}>")
        partidas_text = "\n".join(part_lines)
    else:
        partidas_text = "Nenhuma"
    embed.add_field(name="🟥 Partidas em andamento", value=partidas_text, inline=False)

    # Últimas 3 partidas
    if historico:
        last = historico[-3:]
        last_lines = []
        for h in last:
            if h.get("tie"):
                last_lines.append(f"• Empate — {h.get('match_id','')}")
            elif h.get("winner"):
                last_lines.append(f"• <@{h['winner']}> venceu <@{h['loser']}>")
        ult_text = "\n".join(last_lines)
    else:
        ult_text = "Nenhuma"
    embed.add_field(name="🟩 Últimas 3 partidas", value=ult_text, inline=False)

    # Inscritos (toggle)
    if mostrar_inscritos and torneio_data.get("players"):
        ins_lines = [f"• <@{u}>" for u in torneio_data.get("players", [])[:30]]
        inscritos_text = "\n".join(ins_lines)
    else:
        inscritos_text = "Oculto" if not mostrar_inscritos else "Nenhum inscrito"
    embed.add_field(name="🏆 Inscritos Torneio", value=inscritos_text, inline=False)

    embed.set_footer(text="Reaja: ✅ entrar | ❌ sair | 👁️ mostrar | 🙈 ocultar | 📊 ranking")
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
            try:
                await msg.add_reaction(EMOJI_CHECK)
                await msg.add_reaction(EMOJI_X)
                await msg.add_reaction(EMOJI_SHOW)
                await msg.add_reaction(EMOJI_HIDE)
                await msg.add_reaction(EMOJI_RANK)
            except:
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
                    await msg.add_reaction(EMOJI_RANK)
                except:
                    pass
    except Exception as e:
        print(Fore.RED + f"[PAINEL] erro ao atualizar: {e}")

# ---------------- PERSIST / PERIODIC TASKS ----------------
@tasks.loop(minutes=5)
async def save_states():
    save_json(RANKING_FILE, ranking)
    save_json(TORNEIO_FILE, torneio_data)
    save_json(HISTORICO_FILE, historico)

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
        print(Fore.RED + "[DAILY] error:", e)

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
                    "timestamp": now_iso(),
                    "polls": []  # list of (user_id, message_id)
                }
                await send_result_poll(match_id, partidas_ativas[match_id])
                await atualizar_painel()
        except Exception as e:
            print(Fore.RED + f"[FILA WORKER] erro: {e}")
        await asyncio.sleep(3)

# ---------------- TORNEIO SUÍÇO ----------------
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
            "source": "torneio",
            "polls": []
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
        # send short DM then poll
        for uid in (p1, p2):
            u = await safe_fetch_user(uid)
            if u:
                try:
                    await u.send(f"🏁 Rodada {torneio_data.get('round',1)} — Confronto: <@{p1}> vs <@{p2}>\nReportar resultado reagindo à mensagem que eu enviei (1️⃣=player1, 2️⃣=player2, ➖=empate).")
                except:
                    pass
        await send_result_poll(pid, pairing)

# ---------------- SEND RESULT POLL ----------------
async def send_result_poll(match_id: str, partida: dict):
    p1 = partida["player1"]; p2 = partida["player2"]
    content = (
        f"⚔️ Partida: <@{p1}> vs <@{p2}>\n\n"
        f"Quem venceu? Reaja:\n"
        f"{EMOJI_ONE} — <@{p1}>\n"
        f"{EMOJI_TWO} — <@{p2}>\n"
        f"{EMOJI_TIE} — Empate\n\n"
        "Resultado só será confirmado se ambos reagirem na mesma opção."
    )
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
            # track poll message
            partida.setdefault("polls", []).append((uid, msg.id))
            poll_message_map[msg.id] = (match_id, uid)
        except Exception:
            pass

# ---------------- ON_MESSAGE (decklist capture + confirmation) ----------------
async def validate_decklist_text(text: str) -> bool:
    """
    Parse decklist lines like '4xOP13-113' or '4 x OP13-113' or '4x OP13-113'
    Sum numbers before 'x' and return True if total == 51.
    """
    total = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # find number before 'x'
        # allow formats: '4xID', '4 xID', '4 x ID', etc.
        parts = line.split('x', 1)
        if len(parts) < 2:
            parts = line.split('X', 1)
            if len(parts) < 2:
                # try space-separated first token
                tok = line.split()[0]
                try:
                    n = int(tok)
                    total += n
                    continue
                except:
                    # invalid line — consider as 0
                    continue
        try:
            n = int(parts[0].strip())
            total += n
        except:
            # attempt to extract leading digits
            s = parts[0].strip()
            digits = ''
            for ch in s:
                if ch.isdigit():
                    digits += ch
                else:
                    break
            try:
                if digits:
                    total += int(digits)
            except:
                pass
    return total == 51

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # DM decklist capture + confirmation flow
    if isinstance(message.channel, discord.DMChannel):
        uid = message.author.id
        if uid in torneio_data.get("players", []):
            # if no decklist or not confirmed, accept new decklist text
            # store but ask confirmation
            text = message.content.strip()
            if not text:
                return
            # validate count
            ok = await validate_decklist_text(text)
            if not ok:
                try:
                    await message.author.send("❌ Decklist inválida: a soma das quantidades não resulta em 51 cartas. Por favor envie novamente no formato `NxCARDID` (ex: `4xOP13-113`).")
                except:
                    pass
                return
            # save decklist draft and ask confirm
            torneio_data.setdefault("decklists", {})[str(uid)] = text
            torneio_data.setdefault("deck_confirmed", {})[str(uid)] = False
            save_json(TORNEIO_FILE, torneio_data)
            try:
                confirm_msg = await message.author.send("✅ Decklist recebida. Confirma esta decklist? Reaja com ✅ para confirmar ou ❌ para reenviar.")
                try:
                    await confirm_msg.add_reaction(EMOJI_CONFIRM)
                    await confirm_msg.add_reaction(EMOJI_DENY)
                except:
                    pass
                # track confirm message
                poll_message_map[confirm_msg.id] = ("deck_confirm", uid)
            except:
                pass
            return

    await bot.process_commands(message)

# ---------------- REACTION HANDLER (panel + inscription + polls + deck confirmations + ranking) ----------------
@bot.event
async def on_reaction_add(reaction, user):
    if user.bot:
        return
    try:
        # Panel reactions (enter/leave/show/hide/ranking)
        if reaction.message.id == PANEL_MESSAGE_ID:
            emoji = str(reaction.emoji)
            if emoji == EMOJI_CHECK:
                if user.id not in fila:
                    fila.append(user.id)
                    try: await user.send("✅ Você entrou na fila 1x1. Aguarde emparelhamento.")
                    except: pass
                    await atualizar_painel()
            elif emoji == EMOJI_X:
                if user.id in fila:
                    fila.remove(user.id)
                    try: await user.send("❌ Você saiu da fila 1x1.")
                    except: pass
                    await atualizar_painel()
            elif emoji == EMOJI_SHOW:
                global mostrar_inscritos
                mostrar_inscritos = True
                await atualizar_painel()
            elif emoji == EMOJI_HIDE:
                mostrar_inscritos = False
                await atualizar_painel()
            elif emoji == EMOJI_RANK:
                # trigger DM ranking same as !verranking
                await send_ranking_dm(user.id)
            try:
                await reaction.remove(user)
            except:
                pass
    except Exception:
        pass

    # Tournament inscription reaction
    try:
        if torneio_data.get("inscription_message_id") and reaction.message.id == torneio_data.get("inscription_message_id"):
            if str(reaction.emoji) == EMOJI_TROPHY and torneio_data.get("inscriptions_open"):
                if user.id not in torneio_data.get("players", []):
                    torneio_data["players"].append(user.id)
                    torneio_data["decklists"].pop(str(user.id), None)
                    torneio_data.setdefault("deck_confirmed", {})[str(user.id)] = False
                    save_json(TORNEIO_FILE, torneio_data)
                    try:
                        await user.send("✅ Inscrição recebida! Você receberá uma DM solicitando sua decklist quando o torneio for iniciado.")
                    except:
                        pass
                    await atualizar_painel()
                try:
                    await reaction.remove(user)
                except:
                    pass
    except Exception:
        pass

    # Decklist confirmation reaction handling
    try:
        mid = reaction.message.id
        if mid in poll_message_map:
            key = poll_message_map[mid]
            # deck confirm messages stored with ("deck_confirm", uid)
            if isinstance(key, tuple) and key[0] == "deck_confirm":
                _, uid = key
                if user.id != uid:
                    # ignore reactions by others
                    try: await reaction.remove(user)
                    except: pass
                    return
                emoji = str(reaction.emoji)
                if emoji == EMOJI_CONFIRM:
                    torneio_data.setdefault("deck_confirmed", {})[str(uid)] = True
                    save_json(TORNEIO_FILE, torneio_data)
                    try:
                        await user.send("✅ Deck confirmado! Aguarde os demais jogadores.")
                    except:
                        pass
                elif emoji == EMOJI_DENY:
                    torneio_data.setdefault("deck_confirmed", {})[str(uid)] = False
                    torneio_data.setdefault("decklists", {}).pop(str(uid), None)
                    save_json(TORNEIO_FILE, torneio_data)
                    try:
                        await user.send("✏️ Ok, por favor envie novamente a sua decklist (cole aqui).")
                    except:
                        pass
                try:
                    await reaction.remove(user)
                except:
                    pass
                # check if all confirmed now and auto-start if so (only if admin hasn't chosen otherwise)
                await check_all_decks_confirmed_and_maybe_start()
                return
    except Exception as e:
        print(Fore.RED + f"[DECK CONFIRM] {e}")

    # Poll reactions (1/2/➖) — match result
    try:
        emoji = str(reaction.emoji)
        if emoji in (EMOJI_ONE, EMOJI_TWO, EMOJI_TIE):
            msg_id = reaction.message.id
            # we mapped poll messages in poll_message_map
            if msg_id in poll_message_map:
                match_id, uid = poll_message_map[msg_id]
                # find the match (in partidas_ativas or torneio pairings)
                if match_id in partidas_ativas:
                    p = partidas_ativas[match_id]
                    # ensure the reacting user is the owner of the DM poll (uid)
                    if user.id != uid:
                        try: await reaction.remove(user)
                        except: pass
                        return
                    p.setdefault("attempts", {})[str(user.id)] = emoji
                    partidas_ativas[match_id] = p
                    await check_and_process_match_result(match_id, p)
                elif match_id in torneio_data.get("pairings", {}):
                    p = torneio_data["pairings"][match_id]
                    if user.id != uid:
                        try: await reaction.remove(user)
                        except: pass
                        return
                    p.setdefault("attempts", {})[str(user.id)] = emoji
                    torneio_data["pairings"][match_id] = p
                    await check_and_process_torneio_result(match_id, p)
                try:
                    await reaction.remove(user)
                except:
                    pass
    except Exception as e:
        print(Fore.RED + f"[POLL REACT] {e}")

# ---------------- CHECK & PROCESS RESULT ----------------
async def check_and_process_match_result(match_id: str, partida: dict):
    try:
        attempts = partida.get("attempts", {})
        p1, p2 = partida["player1"], partida["player2"]
        if str(p1) in attempts and str(p2) in attempts:
            c1 = attempts.get(str(p1)); c2 = attempts.get(str(p2))
            if c1 == c2:
                await finalize_match_result(match_id, partida, c1)
            else:
                # disagreement
                u1 = await safe_fetch_user(p1); u2 = await safe_fetch_user(p2)
                for u in (u1, u2):
                    if u:
                        try:
                            await u.send("⚠️ Relatórios divergentes — conversem e reaja novamente na mensagem de DM.")
                        except:
                            pass
    except Exception as e:
        print(Fore.RED + f"[CHECK MATCH] {e}")

async def check_and_process_torneio_result(match_id: str, partida: dict):
    try:
        attempts = partida.get("attempts", {})
        p1, p2 = partida["player1"], partida["player2"]
        if str(p1) in attempts and str(p2) in attempts:
            c1 = attempts.get(str(p1)); c2 = attempts.get(str(p2))
            if c1 == c2:
                await finalize_torneio_result(match_id, partida, c1)
            else:
                u1 = await safe_fetch_user(p1); u2 = await safe_fetch_user(p2)
                for u in (u1, u2):
                    if u:
                        try:
                            await u.send("⚠️ Relatórios divergentes — conversem e reaja novamente na mensagem de DM.")
                        except:
                            pass
    except Exception as e:
        print(Fore.RED + f"[CHECK TORNEIO] {e}")

# ---------------- FINALIZE RESULT ----------------
async def finalize_match_result(match_id: str, partida: dict, emoji_choice: str):
    try:
        p1 = partida["player1"]; p2 = partida["player2"]
        if emoji_choice == EMOJI_ONE:
            winner, loser = p1, p2
        elif emoji_choice == EMOJI_TWO:
            winner, loser = p2, p1
        else:
            winner, loser = None, None
        ts = now_iso()
        if winner:
            historico.append({"winner": winner, "loser": loser, "timestamp": ts, "match_id": match_id, "source": partida.get("source","fila")})
            ranking.setdefault("scores_1x1", {})[str(winner)] = ranking.get("scores_1x1", {}).get(str(winner), 0) + 1
        else:
            historico.append({"winner": None, "loser": None, "timestamp": ts, "match_id": match_id, "source": partida.get("source","fila"), "tie": True})
        partidas_ativas.pop(match_id, None)
        save_json(RANKING_FILE, ranking)
        save_json(HISTORICO_FILE, historico)
        u1 = await safe_fetch_user(p1); u2 = await safe_fetch_user(p2)
        note = f"✅ Resultado confirmado: {'Empate' if winner is None else f'<@{winner}> venceu <@{loser}>'} (match {match_id})"
        for u in (u1, u2):
            if u:
                try: await u.send(note)
                except: pass
        await atualizar_painel()
    except Exception as e:
        print(Fore.RED + f"[FINALIZE MATCH] {e}")

async def finalize_torneio_result(match_id: str, partida: dict, emoji_choice: str):
    try:
        p1 = partida["player1"]; p2 = partida["player2"]
        if emoji_choice == EMOJI_ONE:
            winner, loser = p1, p2
        elif emoji_choice == EMOJI_TWO:
            winner, loser = p2, p1
        else:
            winner, loser = None, None
        ts = now_iso()
        if winner:
            historico.append({"winner": winner, "loser": loser, "timestamp": ts, "match_id": match_id, "source": "torneio"})
            torneio_data.setdefault("scores", {})[str(winner)] = torneio_data.get("scores", {}).get(str(winner), 0) + 1
        else:
            historico.append({"winner": None, "loser": None, "timestamp": ts, "match_id": match_id, "source": "torneio", "tie": True})
        # remove pairing to free for next round
        torneio_data.get("pairings", {}).pop(match_id, None)
        save_json(TORNEIO_FILE, torneio_data)
        save_json(HISTORICO_FILE, historico)
        u1 = await safe_fetch_user(p1); u2 = await safe_fetch_user(p2)
        note = f"✅ Resultado confirmado: {'Empate' if winner is None else f'<@{winner}> venceu <@{loser}>'} (match {match_id})"
        for u in (u1, u2):
            if u:
                try: await u.send(note)
                except: pass
        await atualizar_painel()
    except Exception as e:
        print(Fore.RED + f"[FINALIZE TORNEIO] {e}")

# ---------------- CHECK ALL DECKS CONFIRMED & MAYBE START ----------------
async def check_all_decks_confirmed_and_maybe_start():
    # if tournament active but not started rounds: only start round1 when all confirmed
    if not torneio_data.get("inscriptions_open") and torneio_data.get("players") and not torneio_data.get("active"):
        players = torneio_data.get("players", [])
        confirmed_map = torneio_data.get("deck_confirmed", {})
        # if any player hasn't submitted decklist or not confirmed, do nothing
        all_confirmed = True
        for uid in players:
            if str(uid) not in torneio_data.get("decklists", {}) or not confirmed_map.get(str(uid), False):
                all_confirmed = False
                break
        if all_confirmed:
            # compile decklists to file and send to owner
            combined = []
            for uid in players:
                dl = torneio_data["decklists"].get(str(uid), "")
                combined.append(f"Player: {uid}\nDiscord: <@{uid}>\nDecklist:\n{dl}\n\n---\n\n")
            combined_text = "".join(combined)
            combined_path = DECKLIST_PATH / f"decklists_{int(datetime.datetime.utcnow().timestamp())}.txt"
            try:
                combined_path.write_text(combined_text, encoding="utf-8")
            except:
                pass
            owner = await safe_fetch_user(BOT_OWNER)
            if owner:
                try:
                    await owner.send("📦 Todas as decklists confirmadas — arquivo em anexo:", file=discord.File(str(combined_path)))
                except:
                    try:
                        await owner.send("Todas as decklists confirmadas — (falha ao enviar arquivo).")
                    except:
                        pass
            # start first round
            torneio_data["active"] = True
            torneio_data["rounds_target"] = calcular_rodadas(len(players))
            torneio_data["round"] = 1
            torneio_data["scores"] = {str(u): 0 for u in players}
            torneio_data["byes"] = []
            torneio_data["played"] = {str(u): [] for u in players}
            await gerar_pairings_torneio()
            save_json(TORNEIO_FILE, torneio_data)
            # DM pairings
            await dm_pairings_round()
            # announce in panel channel
            ch = bot.get_channel(PANEL_CHANNEL_ID)
            if ch:
                try:
                    await ch.send(f"🏁 Torneio iniciado automaticamente — rodadas: {torneio_data['rounds_target']}.")
                except:
                    pass
            await atualizar_painel()

# ---------------- COMMANDS: CANCEL/TORNEIO/ADMIN ----------------
@bot.command(name="torneio")
async def cmd_torneio_open(ctx):
    if ctx.author.id != BOT_OWNER:
        await ctx.send("❌ Apenas o dono pode abrir inscrições.")
        return
    torneio_data["inscriptions_open"] = True
    torneio_data["players"] = []
    torneio_data["decklists"] = {}
    torneio_data["deck_confirmed"] = {}
    torneio_data["inscription_message_id"] = 0
    msg = await ctx.send("🏆 **TORNEIO ABERTO** — Reaja com 🏆 para se inscrever. Você receberá DM solicitando decklist quando o admin iniciar.")
    try:
        await msg.add_reaction(EMOJI_TROPHY)
    except:
        pass
    torneio_data["inscription_message_id"] = msg.id
    save_json(TORNEIO_FILE, torneio_data)
    await ctx.send("✅ Torneio aberto — mensagem criada.")
    await atualizar_painel()

@bot.command(name="fecharinscricoes")
async def cmd_fecharinscricoes(ctx):
    if ctx.author.id != BOT_OWNER:
        await ctx.send("❌ Apenas o dono pode fechar inscrições.")
        return
    torneio_data["inscriptions_open"] = False
    save_json(TORNEIO_FILE, torneio_data)
    await ctx.send(f"🔒 Inscrições fechadas. Jogadores inscritos: {len(torneio_data.get('players', []))}")
    await atualizar_painel()

@bot.command(name="começartorneio")
async def cmd_comecar_torneio(ctx):
    if ctx.author.id != BOT_OWNER:
        await ctx.send("❌ Apenas o dono pode iniciar o processo de decklists.")
        return
    players = torneio_data.get("players", [])
    if len(players) < 2:
        await ctx.send("❌ Jogadores insuficientes (mínimo 2).")
        return
    # Close inscriptions and request decklists via DM for all players
    torneio_data["inscriptions_open"] = False
    torneio_data["decklists"] = torneio_data.get("decklists", {})  # keep any existing
    torneio_data["deck_confirmed"] = torneio_data.get("deck_confirmed", {})
    save_json(TORNEIO_FILE, torneio_data)
    # DM each player asking decklist or confirmation
    for uid in players:
        u = await safe_fetch_user(uid)
        if not u:
            continue
        # if player already submitted decklist and not confirmed, ask to confirm
        if str(uid) in torneio_data.get("decklists", {}) and torneio_data.get("deck_confirmed", {}).get(str(uid), False):
            # already confirmed
            try:
                await u.send("🔔 Você já confirmou sua decklist. Aguarde os demais jogadores.")
            except:
                pass
        elif str(uid) in torneio_data.get("decklists", {}):
            # ask to confirm
            try:
                msg = await u.send("✅ Decklist já recebida. Confirma esta decklist? Reaja com ✅ para confirmar ou ❌ para reenviar.")
                try:
                    await msg.add_reaction(EMOJI_CONFIRM)
                    await msg.add_reaction(EMOJI_DENY)
                except:
                    pass
                poll_message_map[msg.id] = ("deck_confirm", uid)
            except:
                pass
        else:
            # ask to send decklist
            try:
                await u.send("✏️ Por favor envie sua decklist aqui (formato ex: `4xOP13-113`). O bot validará se totaliza 51 cartas e pedirá confirmação.")
            except:
                pass
    await ctx.send("📨 Solicitações de decklist enviadas por DM. O torneio começará automaticamente quando todos confirmarem, ou o admin pode remover jogadores / forçar início.")
    await atualizar_painel()

@bot.command(name="removerjogador")
async def cmd_remover_jogador(ctx, member: discord.Member):
    if ctx.author.id != BOT_OWNER:
        await ctx.send("❌ Apenas o dono pode remover jogadores.")
        return
    uid = member.id
    if uid in torneio_data.get("players", []):
        torneio_data["players"].remove(uid)
        torneio_data.get("decklists", {}).pop(str(uid), None)
        torneio_data.get("deck_confirmed", {}).pop(str(uid), None)
        save_json(TORNEIO_FILE, torneio_data)
        await ctx.send(f"✅ Jogador <@{uid}> removido do torneio.")
        await atualizar_painel()
    else:
        await ctx.send("❌ Jogador não está inscrito.")

@bot.command(name="forçarrodada")
async def cmd_forcar_rodada(ctx):
    if ctx.author.id != BOT_OWNER:
        await ctx.send("❌ Apenas o dono pode forçar o início.")
        return
    players = torneio_data.get("players", [])
    if not players:
        await ctx.send("❌ Nenhum inscrito.")
        return
    # force start regardless of deck confirmations
    torneio_data["active"] = True
    torneio_data["rounds_target"] = calcular_rodadas(len(players))
    torneio_data["round"] = torneio_data.get("round", 1)
    torneio_data["scores"] = {str(u): 0 for u in players}
    torneio_data["byes"] = []
    torneio_data["played"] = {str(u): [] for u in players}
    await gerar_pairings_torneio()
    save_json(TORNEIO_FILE, torneio_data)
    await dm_pairings_round()
    await ctx.send("⚠️ Inicio forçado: rodada iniciada apesar de decklists pendentes.")
    await atualizar_painel()

@bot.command(name="cancelartorneio")
async def cmd_cancelar_torneio(ctx):
    if ctx.author.id != BOT_OWNER:
        await ctx.send("❌ Apenas o dono pode cancelar o torneio.")
        return
    # reset torneio without champion
    torneio_data.update({
        "active": False,
        "inscriptions_open": False,
        "players": [],
        "decklists": {},
        "deck_confirmed": {},
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
    await ctx.send("✅ Torneio cancelado e resetado (nenhum campeão registrado).")
    await atualizar_painel()

@bot.command(name="encerrar")
async def cmd_encerrar(ctx):
    if ctx.author.id != BOT_OWNER:
        await ctx.send("❌ Apenas o dono pode encerrar o torneio.")
        return
    if not torneio_data.get("active"):
        await ctx.send("❌ Nenhum torneio ativo.")
        return
    # declare champion by current scores
    scores = torneio_data.get("scores", {})
    if not scores:
        await ctx.send("❌ Nenhum resultado registrado ainda.")
        return
    champ_id, champ_score = max(scores.items(), key=lambda kv: kv[1])
    # update champions ranking
    torneio_data.setdefault("tournament_champions", {})[str(champ_id)] = torneio_data.get("tournament_champions", {}).get(str(champ_id), 0) + 1
    ranking.setdefault("scores_torneio", {})[str(champ_id)] = ranking.get("scores_torneio", {}).get(str(champ_id), 0) + 1
    # finalize
    torneio_data["active"] = False
    torneio_data["finished"] = True
    save_json(RANKING_FILE, ranking)
    save_json(TORNEIO_FILE, torneio_data)
    ch = bot.get_channel(PANEL_CHANNEL_ID)
    if ch:
        await ch.send(f"🏆 Torneio encerrado pelo admin. Campeão: <@{champ_id}> com {champ_score} pontos. Parabéns!")
    owner = await safe_fetch_user(BOT_OWNER)
    if owner:
        try:
            await owner.send(f"🏆 Torneio encerrado. Campeão: <@{champ_id}> — {champ_score} pts.")
        except:
            pass
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
    else:
        await ctx.send("Uso: `!resetranking 1x1`")

@bot.command(name="torneiorankreset")
async def cmd_reset_torneio_ranking(ctx):
    if ctx.author.id != BOT_OWNER:
        await ctx.send("❌ Apenas o dono pode resetar ranking de torneio.")
        return
    ranking["scores_torneio"] = {}
    save_json(RANKING_FILE, ranking)
    await ctx.send("🔄 Ranking de torneios resetado manualmente.")

# ---------------- RANKING DM FLOW (also used by panel reaction) ----------------
async def send_ranking_dm(uid: int):
    user = await safe_fetch_user(uid)
    if not user:
        return
    try:
        s_1x1 = sorted(ranking.get("scores_1x1", {}).items(), key=lambda kv: kv[1], reverse=True)
        lines = ["🏅 **Ranking 1x1** 🏅\n"]
        for i, (u, pts) in enumerate(s_1x1[:20], 1):
            lines.append(f"{i}. <@{u}> — {pts} vitórias")
        if not s_1x1:
            lines.append("Nenhuma partida registrada ainda.")
        dm = await user.send("\n".join(lines))
        ask_msg = await user.send("Deseja visualizar também o ranking de torneios? Reaja com ➡️ para sim ou ❌ para não.")
        try:
            await ask_msg.add_reaction(EMOJI_YES)
            await ask_msg.add_reaction(EMOJI_NO)
        except:
            pass
        def check(reaction, usr):
            return usr.id == uid and reaction.message.id == ask_msg.id and str(reaction.emoji) in (EMOJI_YES, EMOJI_NO)
        try:
            reaction, usr = await bot.wait_for("reaction_add", check=check, timeout=60)
            if str(reaction.emoji) == EMOJI_YES:
                s_t = sorted(ranking.get("scores_torneio", {}).items(), key=lambda kv: kv[1], reverse=True)
                lines2 = ["🏆 **Ranking de Torneios (campeões)** 🏆\n"]
                for i, (u, wins) in enumerate(s_t[:20], 1):
                    lines2.append(f"{i}. <@{u}> — {wins} campeonatos")
                if not s_t:
                    lines2.append("Nenhum campeão registrado ainda.")
                await user.send("\n".join(lines2))
            else:
                await user.send("👍 Ok, não exibirei o ranking de torneios.")
        except asyncio.TimeoutError:
            await user.send("⌛ Tempo esgotado. Não será exibido ranking de torneios.")
    except Exception as e:
        print(Fore.RED + f"[RANK DM] erro: {e}")

# ---------------- CANCELAR PARTIDA (no match_id) ----------------
@bot.command(name="cancelarpartida")
async def cmd_cancelar_partida(ctx):
    uid = ctx.author.id
    # find match
    found_mid = None; found_part = None
    for mid, p in partidas_ativas.items():
        if uid in (p.get("player1"), p.get("player2")):
            found_mid = mid; found_part = p; break
    if not found_part:
        for mid, p in torneio_data.get("pairings", {}).items():
            if uid in (p.get("player1"), p.get("player2")):
                found_mid = mid; found_part = p; break
    if not found_part:
        await ctx.send("❌ Você não está em nenhuma partida ativa.")
        return
    # ask initiator in channel to confirm
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
        await ctx.send("⌛ Tempo esgotado. Pedido abortado.")
        return
    # DM opponent to confirm
    partida = found_part
    opponent = partida["player2"] if uid == partida["player1"] else partida["player1"]
    partida.setdefault("cancel_attempts", {})[str(uid)] = True
    op_user = await safe_fetch_user(opponent)
    if not op_user:
        await ctx.send("❌ Não foi possível contatar o adversário via DM.")
        return
    try:
        dm = await op_user.send(f"⚠️ <@{uid}> solicitou cancelar a partida. Reaja com {EMOJI_YES} para confirmar cancelamento, ou {EMOJI_NO} para negar.")
        try:
            await dm.add_reaction(EMOJI_YES); await dm.add_reaction(EMOJI_NO)
        except:
            pass
        poll_message_map[dm.id] = ("cancel_ack", (found_mid, uid))
    except:
        await ctx.send("❌ Falha ao enviar DM ao adversário.")
        return
    def check_op(reaction, user):
        return user.id == opponent and reaction.message.id == dm.id and str(reaction.emoji) in (EMOJI_YES, EMOJI_NO)
    try:
        reaction, user = await bot.wait_for("reaction_add", check=check_op, timeout=60)
        if str(reaction.emoji) == EMOJI_YES:
            partidas_ativas.pop(found_mid, None)
            if found_mid in torneio_data.get("pairings", {}):
                torneio_data["pairings"].pop(found_mid, None)
            save_json(TORNEIO_FILE, torneio_data)
            await ctx.send("✅ Partida cancelada por acordo entre os jogadores.")
            p1u = await safe_fetch_user(partida["player1"]); p2u = await safe_fetch_user(partida["player2"])
            for u in (p1u, p2u):
                if u:
                    try: await u.send("✅ Partida cancelada por acordo entre os jogadores.")
                    except: pass
            await atualizar_painel()
        else:
            await ctx.send("❌ O adversário negou o cancelamento. Partida segue ativa.")
    except asyncio.TimeoutError:
        await ctx.send("⌛ Tempo esgotado aguardando resposta do adversário.")

# ---------------- STAT, HELP, VER RANKING ----------------
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

@bot.command(name="verranking")
async def cmd_verranking(ctx):
    await send_ranking_dm(ctx.author.id)

@bot.command(name="ajuda")
async def cmd_ajuda(ctx):
    help_text = (
        "🎮 Comandos OPTTCG — Resumo\n\n"
        "Jogadores:\n"
        "• Reaja no painel com ✅ para entrar / ❌ para sair da fila 1x1\n"
        "• !cancelarpartida — solicita cancelamento da sua partida atual (confirmar via DM)\n"
        "• As partidas enviam DM com reações 1️⃣/2️⃣/➖ para reportar resultado\n\n"
        "Admin:\n"
        "• !torneio — abrir inscrições\n"
        "• !fecharinscricoes — fechar inscrições\n"
        "• !começartorneio — solicitar decklists por DM (não inicia até todos confirmarem)\n"
        "• !removerjogador @user — remove um inscrito\n"
        "• !forçarrodada — força iniciar torneio apesar de decks pendentes\n"
        "• !cancelartorneio — cancela torneio imediatamente\n"
        "• !encerrar — encerra torneio na rodada atual e declara campeão\n"
        "• !proximarodada — avança rodada (admin)\n"
        "• !resetranking 1x1 — reset manual ranking 1x1\n"
        "• !torneiorankreset — reset manual ranking torneio\n"
    )
    await ctx.send(help_text)

# ---------------- ON_READY ----------------
@bot.event
async def on_ready():
    await atualizar_painel()
    print(Fore.GREEN + f"[READY] {bot.user} (id: {bot.user.id})")

# ---------------- ENTRY POINT ----------------
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print(Fore.RED + "❌ DISCORD_TOKEN não definido nas variáveis de ambiente.")
    else:
        try:
            bot.run(DISCORD_TOKEN)
        except Exception as e:
            print(Fore.RED + "❌ Erro ao iniciar o bot:", e)
