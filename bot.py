# bot.py — OPTCG Sorocaba — Versão final (Painel estilo A: azul + dourado)
# Requisitos:
# discord.py==2.4.0
# aiohttp==3.8.5
# python-dotenv==1.0.1
# colorama==0.4.6
# pytz==2024.1

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

# optional dotenv
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
    "deck_confirmed": {},
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
historico = load_json(HISTORICO_FILE, [])

fila = []
partidas_ativas = {}
poll_message_map = {}
PANEL_MESSAGE_ID = 0
mostrar_inscritos = True

# ---------------- INTENTS & BOT ----------------
intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.members = True

class TournamentBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents, help_command=None)

    async def setup_hook(self):
        # start webserver and tasks
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

# ---------------- WEB SERVER (keepalive) ----------------
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
        print(Fore.CYAN + f"[WEB] listening on 0.0.0.0:{PORT}")
    except Exception as e:
        print(Fore.RED + f"[WEB] failed: {e}")

# ---------------- PANEL (Embed style A: azul + dourado) ----------------
def build_panel_embed():
    # colors: blue and gold accents
    embed = discord.Embed(title="🎮 OPTCG Sorocaba — Painel Geral 🎮",
                          description="Painel interativo — fila, partidas e torneio",
                          color=0x1e90ff,
                          timestamp=datetime.datetime.utcnow())
    # header field with gold accent via emoji
    embed.add_field(name="🏴‍☠️ Status geral", value=f"**Torneio ativo:** {torneio_data.get('active')}\n**Rodada:** {torneio_data.get('round')}/{torneio_data.get('rounds_target') or '-'}", inline=False)

    # Fila
    if fila:
        fila_text = "\n".join([f"• <@{u}>" for u in fila[:30]])
    else:
        fila_text = "Vazia"
    embed.add_field(name="🟦 Fila 1x1", value=fila_text, inline=False)

    # Partidas
    if partidas_ativas:
        lines = []
        for mid, p in list(partidas_ativas.items())[:12]:
            lines.append(f"• <@{p['player1']}> vs <@{p['player2']}>")
        partidas_text = "\n".join(lines)
    else:
        partidas_text = "Nenhuma"
    embed.add_field(name="🟥 Partidas em andamento", value=partidas_text, inline=False)

    # Ultimas 3
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

    # Inscritos
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
    if PANEL_CHANNEL_ID == 0:
        return
    ch = bot.get_channel(PANEL_CHANNEL_ID)
    if not ch:
        return
    embed = build_panel_embed()
    try:
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
        print(Fore.RED + f"[PAINEL] erro: {e}")

# ---------------- PERSIST / TASKS ----------------
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
                    "polls": []
                }
                await send_result_poll(match_id, partidas_ativas[match_id])
                await atualizar_painel()
        except Exception as e:
            print(Fore.RED + f"[FILA WORKER] {e}")
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
        p1 = sorted_players[i]; p2 = sorted_players[i+1]
        pid = f"tor_{p1}_{p2}_{int(datetime.datetime.utcnow().timestamp())}"
        pairings[pid] = {
            "player1": p1, "player2": p2, "attempts": {}, "cancel_attempts": {}, "result": None,
            "round": torneio_data.get("round", 1), "source": "torneio", "polls": []
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
                    await u.send(f"🏁 Rodada {torneio_data.get('round',1)} — Confronto: <@{p1}> vs <@{p2}>\nReportar resultado reagindo (1️⃣/2️⃣/➖).")
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
            partida.setdefault("polls", []).append((uid, msg.id))
            poll_message_map[msg.id] = (match_id, uid)
        except:
            pass

# ---------------- DECKLIST VALIDATION & DM HANDLING ----------------
async def validate_decklist_text(text: str) -> (bool, int):
    total = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split('x', 1)
        if len(parts) < 2:
            parts = line.split('X', 1)
            if len(parts) < 2:
                # try first token
                tok = line.split()[0]
                try:
                    n = int(tok)
                    total += n
                    continue
                except:
                    return False, total
        try:
            n = int(parts[0].strip())
            total += n
        except:
            # try leading digits
            digits = ''
            for ch in parts[0]:
                if ch.isdigit():
                    digits += ch
                else:
                    break
            if digits:
                total += int(digits)
            else:
                return False, total
    return (total == 51), total

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # DM flow for decklist
    if isinstance(message.channel, discord.DMChannel):
        uid = message.author.id
        if uid in torneio_data.get("players", []):
            deck_text = message.content.strip()
            ok, total = await validate_decklist_text(deck_text)
            if not ok:
                try:
                    await message.author.send(f"⚠️ Deck inválido: total encontrado = {total}. O deck precisa ter exatamente 51 cartas. Envie novamente no formato `4xOP13-113` por linha.")
                except:
                    pass
                return
            # ask confirmation via reaction
            try:
                confirm_msg = await message.author.send("📋 Decklist recebida. Confirma esta decklist? Reaja ✅ para confirmar ou ❌ para reenviar.")
                await confirm_msg.add_reaction(EMOJI_CONFIRM)
                await confirm_msg.add_reaction(EMOJI_DENY)
                poll_message_map[confirm_msg.id] = ("deck_confirm", uid)
            except:
                pass
            # store draft
            torneio_data.setdefault("decklists", {})[str(uid)] = deck_text
            torneio_data.setdefault("deck_confirmed", {})[str(uid)] = False
            save_json(TORNEIO_FILE, torneio_data)
            return

    await bot.process_commands(message)

# ---------------- REACTIONS HANDLER ----------------
@bot.event
async def on_reaction_add(reaction, user):
    if user.bot:
        return
    # Panel reactions
    try:
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
                await send_ranking_dm(user.id)
            try:
                await reaction.remove(user)
            except:
                pass
    except Exception:
        pass

    # inscription reaction
    try:
        if torneio_data.get("inscription_message_id") and reaction.message.id == torneio_data.get("inscription_message_id"):
            if str(reaction.emoji) == EMOJI_TROPHY and torneio_data.get("inscriptions_open"):
                if user.id not in torneio_data.get("players", []):
                    torneio_data["players"].append(user.id)
                    torneio_data["decklists"].pop(str(user.id), None)
                    torneio_data.setdefault("deck_confirmed", {})[str(user.id)] = False
                    save_json(TORNEIO_FILE, torneio_data)
                    try: await user.send("✅ Inscrição recebida! Quando o admin solicitar decklists, você será avisado por DM.")
                    except: pass
                    await atualizar_painel()
                try:
                    await reaction.remove(user)
                except:
                    pass
    except Exception:
        pass

    # deck confirm reaction
    try:
        mid = reaction.message.id
        if mid in poll_message_map:
            key = poll_message_map[mid]
            if isinstance(key, tuple) and key[0] == "deck_confirm":
                _, uid = key
                if user.id != uid:
                    try: await reaction.remove(user)
                    except: pass
                    return
                emoji = str(reaction.emoji)
                if emoji == EMOJI_CONFIRM:
                    torneio_data.setdefault("deck_confirmed", {})[str(uid)] = True
                    save_json(TORNEIO_FILE, torneio_data)
                    try: await user.send("✅ Decklist confirmada. Aguarde os demais jogadores.")
                    except: pass
                elif emoji == EMOJI_DENY:
                    torneio_data.setdefault("deck_confirmed", {})[str(uid)] = False
                    torneio_data.setdefault("decklists", {}).pop(str(uid), None)
                    save_json(TORNEIO_FILE, torneio_data)
                    try: await user.send("🔁 Ok. Envie novamente sua decklist no formato correto.")
                    except: pass
                try: await reaction.remove(user)
                except: pass
                await check_all_decks_confirmed_and_maybe_start()
                return
    except Exception as e:
        print(Fore.RED + f"[DECK CONFIRM] {e}")

    # poll reaction (match result)
    try:
        emoji = str(reaction.emoji)
        if emoji in (EMOJI_ONE, EMOJI_TWO, EMOJI_TIE):
            msg_id = reaction.message.id
            if msg_id in poll_message_map:
                match_id, uid = poll_message_map[msg_id]
                if match_id in partidas_ativas:
                    p = partidas_ativas[match_id]
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
                try: await reaction.remove(user)
                except: pass
    except Exception as e:
        print(Fore.RED + f"[POLL REACT] {e}")

# ---------------- PROCESS RESULT ----------------
async def check_and_process_match_result(match_id: str, partida: dict):
    try:
        attempts = partida.get("attempts", {})
        p1, p2 = partida["player1"], partida["player2"]
        if str(p1) in attempts and str(p2) in attempts:
            c1 = attempts.get(str(p1)); c2 = attempts.get(str(p2))
            if c1 == c2:
                await finalize_match_result(match_id, partida, c1)
            else:
                u1 = await safe_fetch_user(p1); u2 = await safe_fetch_user(p2)
                for u in (u1, u2):
                    if u:
                        try: await u.send("⚠️ Relatórios divergentes. Conversem e reagam novamente na mesma opção.")
                        except: pass
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
                        try: await u.send("⚠️ Relatórios divergentes. Conversem e reagam novamente na mesma opção.")
                        except: pass
    except Exception as e:
        print(Fore.RED + f"[CHECK TORNEIO] {e}")

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
    if not torneio_data.get("inscriptions_open") and torneio_data.get("players") and not torneio_data.get("active"):
        players = torneio_data.get("players", [])
        confirmed_map = torneio_data.get("deck_confirmed", {})
        all_confirmed = True
        for uid in players:
            if str(uid) not in torneio_data.get("decklists", {}) or not confirmed_map.get(str(uid), False):
                all_confirmed = False
                break
        if all_confirmed:
            # create combined decklist file and send to owner
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
                    await owner.send("📦 Todas as decklists recebidas — arquivo em anexo:", file=discord.File(str(combined_path)))
                except:
                    try:
                        await owner.send("Todas as decklists recebidas — (falha ao enviar arquivo).")
                    except:
                        pass
            # start tournament
            torneio_data["active"] = True
            torneio_data["rounds_target"] = calcular_rodadas(len(players))
            torneio_data["round"] = 1
            torneio_data["scores"] = {str(u): 0 for u in players}
            torneio_data["byes"] = []
            torneio_data["played"] = {str(u): [] for u in players}
            await gerar_pairings_torneio()
            save_json(TORNEIO_FILE, torneio_data)
            await dm_pairings_round()
            ch = bot.get_channel(PANEL_CHANNEL_ID)
            if ch:
                try: await ch.send(f"🏁 Torneio iniciado automaticamente — rodadas: {torneio_data['rounds_target']}.")
                except: pass
            await atualizar_painel()

# ---------------- COMMANDS ----------------
@bot.command(name="novopainel")
async def cmd_novopainel(ctx):
    if ctx.author.id != BOT_OWNER:
        try:
            await ctx.message.delete()
        except:
            pass
        await ctx.send("❌ Apenas o dono do bot pode usar este comando.", delete_after=5)
        return
    global PANEL_MESSAGE_ID
    ch = bot.get_channel(PANEL_CHANNEL_ID)
    if not ch:
        try: await ctx.message.delete()
        except: pass
        await ctx.send("❌ Canal do painel não encontrado.", delete_after=5)
        return
    # delete only bot messages in channel (limit recent 200)
    async for msg in ch.history(limit=200):
        if msg.author == bot.user:
            try: await msg.delete()
            except: pass
    PANEL_MESSAGE_ID = 0
    await atualizar_painel()
    try:
        await ctx.send("✅ Painel recriado com sucesso.", delete_after=5)
        await ctx.message.delete()
    except:
        pass

@bot.command(name="torneio")
async def cmd_torneio_open(ctx):
    if ctx.author.id != BOT_OWNER:
        try: await ctx.message.delete()
        except: pass
        await ctx.send("❌ Apenas o dono pode abrir inscrições.", delete_after=5)
        return
    torneio_data["inscriptions_open"] = True
    torneio_data["players"] = []
    torneio_data["decklists"] = {}
    torneio_data["deck_confirmed"] = {}
    torneio_data["inscription_message_id"] = 0
    msg = await ctx.send("🏆 **TORNEIO ABERTO** — Reaja com 🏆 para se inscrever. Você receberá DM solicitando decklist quando o admin iniciar.")
    try: await msg.add_reaction(EMOJI_TROPHY)
    except: pass
    torneio_data["inscription_message_id"] = msg.id
    save_json(TORNEIO_FILE, torneio_data)
    await atualizar_painel()
    try: await ctx.message.delete()
    except: pass

@bot.command(name="fecharinscricoes")
async def cmd_fecharinscricoes(ctx):
    if ctx.author.id != BOT_OWNER:
        try: await ctx.message.delete()
        except: pass
        await ctx.send("❌ Apenas o dono pode fechar inscrições.", delete_after=5)
        return
    torneio_data["inscriptions_open"] = False
    save_json(TORNEIO_FILE, torneio_data)
    await ctx.send(f"🔒 Inscrições fechadas. Jogadores inscritos: {len(torneio_data.get('players', []))}", delete_after=8)
    await atualizar_painel()
    try: await ctx.message.delete()
    except: pass

@bot.command(name="começartorneio")
async def cmd_comecar_torneio(ctx):
    if ctx.author.id != BOT_OWNER:
        try: await ctx.message.delete()
        except: pass
        await ctx.send("❌ Apenas o dono pode iniciar o processo de decklists.", delete_after=5)
        return
    players = torneio_data.get("players", [])
    if len(players) < 2:
        try: await ctx.message.delete()
        except: pass
        await ctx.send("❌ Jogadores insuficientes (mínimo 2).", delete_after=5)
        return
    torneio_data["inscriptions_open"] = False
    save_json(TORNEIO_FILE, torneio_data)
    for uid in players:
        u = await safe_fetch_user(uid)
        if not u:
            continue
        if str(uid) in torneio_data.get("decklists", {}) and torneio_data.get("deck_confirmed", {}).get(str(uid), False):
            try: await u.send("🔔 Você já confirmou sua decklist. Aguarde os demais jogadores.")
            except: pass
        elif str(uid) in torneio_data.get("decklists", {}):
            try:
                msg = await u.send("✅ Decklist já recebida. Confirma esta decklist? Reaja ✅ para confirmar ou ❌ para reenviar.")
                await msg.add_reaction(EMOJI_CONFIRM); await msg.add_reaction(EMOJI_DENY)
                poll_message_map[msg.id] = ("deck_confirm", uid)
            except: pass
        else:
            try:
                await u.send("✏️ Envie sua decklist aqui (formato ex: `4xOP13-113`) — o bot validará se totaliza 51 cartas e pedirá confirmação.")
            except: pass
    await ctx.send("📨 Solicitações de decklist enviadas por DM. O torneio só iniciará quando todos confirmarem, ou o admin pode forçar.", delete_after=8)
    await atualizar_painel()
    try: await ctx.message.delete()
    except: pass

@bot.command(name="removerjogador")
async def cmd_remover_jogador(ctx, member: discord.Member):
    if ctx.author.id != BOT_OWNER:
        try: await ctx.message.delete()
        except: pass
        await ctx.send("❌ Apenas o dono pode remover jogadores.", delete_after=5)
        return
    uid = member.id
    if uid in torneio_data.get("players", []):
        torneio_data["players"].remove(uid)
        torneio_data.get("decklists", {}).pop(str(uid), None)
        torneio_data.get("deck_confirmed", {}).pop(str(uid), None)
        save_json(TORNEIO_FILE, torneio_data)
        await ctx.send(f"✅ Jogador <@{uid}> removido do torneio.", delete_after=6)
        await atualizar_painel()
    else:
        await ctx.send("❌ Jogador não está inscrito.", delete_after=5)
    try: await ctx.message.delete()
    except: pass

@bot.command(name="forçarrodada")
async def cmd_forcar_rodada(ctx):
    if ctx.author.id != BOT_OWNER:
        try: await ctx.message.delete()
        except: pass
        await ctx.send("❌ Apenas o dono pode forçar o início.", delete_after=5)
        return
    players = torneio_data.get("players", [])
    if not players:
        await ctx.send("❌ Nenhum inscrito.", delete_after=5)
        try: await ctx.message.delete()
        except: pass
        return
    torneio_data["active"] = True
    torneio_data["rounds_target"] = calcular_rodadas(len(players))
    torneio_data["round"] = torneio_data.get("round", 1)
    torneio_data["scores"] = {str(u): 0 for u in players}
    torneio_data["byes"] = []
    torneio_data["played"] = {str(u): [] for u in players}
    await gerar_pairings_torneio()
    save_json(TORNEIO_FILE, torneio_data)
    await dm_pairings_round()
    await ctx.send("⚠️ Início forçado: rodada iniciada apesar de decklists pendentes.", delete_after=8)
    await atualizar_painel()
    try: await ctx.message.delete()
    except: pass

@bot.command(name="cancelartorneio")
async def cmd_cancelar_torneio(ctx):
    if ctx.author.id != BOT_OWNER:
        try: await ctx.message.delete()
        except: pass
        await ctx.send("❌ Apenas o dono pode cancelar o torneio.", delete_after=5)
        return
    torneio_data.update({
        "active": False, "inscriptions_open": False, "players": [], "decklists": {},
        "deck_confirmed": {}, "round": 0, "rounds_target": None, "pairings": {},
        "scores": {}, "played": {}, "byes": [], "finished": False, "inscription_message_id": 0
    })
    save_json(TORNEIO_FILE, torneio_data)
    await ctx.send("✅ Torneio cancelado e resetado (nenhum campeão registrado).", delete_after=8)
    await atualizar_painel()
    try: await ctx.message.delete()
    except: pass

@bot.command(name="encerrar")
async def cmd_encerrar(ctx):
    if ctx.author.id != BOT_OWNER:
        try: await ctx.message.delete()
        except: pass
        await ctx.send("❌ Apenas o dono pode encerrar o torneio.", delete_after=5)
        return
    if not torneio_data.get("active"):
        try: await ctx.message.delete()
        except: pass
        await ctx.send("❌ Nenhum torneio ativo.", delete_after=5)
        return
    scores = torneio_data.get("scores", {})
    if not scores:
        try: await ctx.message.delete()
        except: pass
        await ctx.send("❌ Nenhum resultado registrado.", delete_after=5)
        return
    champ_id, champ_score = max(scores.items(), key=lambda kv: kv[1])
    torneio_data.setdefault("tournament_champions", {})[str(champ_id)] = torneio_data.get("tournament_champions", {}).get(str(champ_id), 0) + 1
    ranking.setdefault("scores_torneio", {})[str(champ_id)] = ranking.get("scores_torneio", {}).get(str(champ_id), 0) + 1
    torneio_data["active"] = False
    torneio_data["finished"] = True
    save_json(RANKING_FILE, ranking)
    save_json(TORNEIO_FILE, torneio_data)
    ch = bot.get_channel(PANEL_CHANNEL_ID)
    if ch:
        await ch.send(f"🏆 Torneio encerrado pelo admin. Campeão: <@{champ_id}> com {champ_score} pontos. Parabéns!")
    owner = await safe_fetch_user(BOT_OWNER)
    if owner:
        try: await owner.send(f"🏆 Torneio encerrado. Campeão: <@{champ_id}> — {champ_score} pts.")
        except: pass
    await atualizar_painel()
    try: await ctx.message.delete()
    except: pass

@bot.command(name="proximarodada")
async def cmd_proxima_rodada(ctx):
    if ctx.author.id != BOT_OWNER:
        try: await ctx.message.delete()
        except: pass
        await ctx.send("❌ Apenas o dono pode avançar rodadas.", delete_after=5)
        return
    if not torneio_data.get("active"):
        try: await ctx.message.delete()
        except: pass
        await ctx.send("❌ Nenhum torneio ativo.", delete_after=5)
        return
    if torneio_data.get("round", 0) >= torneio_data.get("rounds_target", 0):
        torneio_data["active"] = False
        torneio_data["finished"] = True
        scores = torneio_data.get("scores", {})
        if scores:
            champion_id, champ_score = max(scores.items(), key=lambda kv: kv[1])
            torneio_data.setdefault("tournament_champions", {})[str(champion_id)] = torneio_data.get("tournament_champions", {}).get(str(champion_id), 0) + 1
            ranking.setdefault("scores_torneio", {})[str(champion_id)] = ranking.get("scores_torneio", {}).get(str(champion_id), 0) + 1
            ch = bot.get_channel(PANEL_CHANNEL_ID)
            if ch:
                await ch.send(f"🏆 Torneio finalizado! Campeão: <@{champion_id}> com {champ_score} pontos. Parabéns!")
            owner = await safe_fetch_user(BOT_OWNER)
            if owner:
                try: await owner.send(f"🏆 Torneio finalizado! Campeão: <@{champion_id}> — {champ_score} pts.")
                except: pass
        save_json(RANKING_FILE, ranking)
        save_json(TORNEIO_FILE, torneio_data)
        await atualizar_painel()
        try: await ctx.message.delete()
        except: pass
        return
    torneio_data["round"] += 1
    torneio_data["byes"] = []
    await gerar_pairings_torneio()
    save_json(TORNEIO_FILE, torneio_data)
    await dm_pairings_round()
    await ctx.send(f"➡️ Avançado para rodada {torneio_data['round']} — pairings enviados por DM.", delete_after=8)
    await atualizar_painel()
    try: await ctx.message.delete()
    except: pass

@bot.command(name="resetartorneio")
async def cmd_reset_torneio(ctx):
    if ctx.author.id != BOT_OWNER:
        try: await ctx.message.delete()
        except: pass
        await ctx.send("❌ Apenas o dono pode resetar o torneio.", delete_after=5)
        return
    torneio_data.update({
        "active": False, "inscriptions_open": False, "players": [], "decklists": {},
        "deck_confirmed": {}, "round": 0, "rounds_target": None, "pairings": {},
        "results": {}, "scores": {}, "played": {}, "byes": [], "finished": False, "inscription_message_id": 0
    })
    save_json(TORNEIO_FILE, torneio_data)
    await ctx.send("✅ Torneio resetado (sem registrar campeão).", delete_after=6)
    await atualizar_painel()
    try: await ctx.message.delete()
    except: pass

@bot.command(name="resetranking")
async def cmd_reset_ranking(ctx, scope: str = "1x1"):
    if ctx.author.id != BOT_OWNER:
        try: await ctx.message.delete()
        except: pass
        await ctx.send("❌ Apenas o dono pode resetar rankings.", delete_after=5)
        return
    if scope.lower() in ("1x1", "fila", "1x"):
        ranking["scores_1x1"] = {}
        ranking["__last_reset"] = datetime.datetime.utcnow().isoformat()
        save_json(RANKING_FILE, ranking)
        await ctx.send("🔄 Ranking 1x1 resetado manualmente.", delete_after=6)
    else:
        await ctx.send("Uso: `!resetranking 1x1`", delete_after=6)
    try: await ctx.message.delete()
    except: pass

@bot.command(name="torneiorankreset")
async def cmd_reset_torneio_ranking(ctx):
    if ctx.author.id != BOT_OWNER:
        try: await ctx.message.delete()
        except: pass
        await ctx.send("❌ Apenas o dono pode resetar ranking de torneio.", delete_after=5)
        return
    ranking["scores_torneio"] = {}
    save_json(RANKING_FILE, ranking)
    await ctx.send("🔄 Ranking de torneios resetado manualmente.", delete_after=6)
    try: await ctx.message.delete()
    except: pass

@bot.command(name="verranking")
async def cmd_verranking(ctx):
    # send ranking via DM
    await send_ranking_dm(ctx.author.id)
    try: await ctx.message.delete()
    except: pass

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
    await ctx.send(help_text, delete_after=15)
    try: await ctx.message.delete()
    except: pass

# ---------------- RANKING DM FLOW ----------------
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
        await ask_msg.add_reaction(EMOJI_YES); await ask_msg.add_reaction(EMOJI_NO)
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
        print(Fore.RED + f"[RANK DM] {e}")

# ---------------- CANCELAR PARTIDA (sem match_id) ----------------
@bot.command(name="cancelarpartida")
async def cmd_cancelar_partida(ctx):
    uid = ctx.author.id
    found_mid = None; found_part = None
    for mid, p in partidas_ativas.items():
        if uid in (p.get("player1"), p.get("player2")):
            found_mid = mid; found_part = p; break
    if not found_part:
        for mid, p in torneio_data.get("pairings", {}).items():
            if uid in (p.get("player1"), p.get("player2")):
                found_mid = mid; found_part = p; break
    if not found_part:
        await ctx.send("❌ Você não está em nenhuma partida ativa.", delete_after=6)
        try: await ctx.message.delete()
        except: pass
        return
    confirm_msg = await ctx.send(f"⚠️ Tem certeza que deseja solicitar cancelamento da sua partida atual? Reaja {EMOJI_YES} para confirmar ou {EMOJI_NO} para cancelar.")
    try: await confirm_msg.add_reaction(EMOJI_YES); await confirm_msg.add_reaction(EMOJI_NO)
    except: pass
    def check_self(reaction, user): return user.id == uid and reaction.message.id == confirm_msg.id and str(reaction.emoji) in (EMOJI_YES, EMOJI_NO)
    try:
        reaction, user = await bot.wait_for("reaction_add", check=check_self, timeout=30)
        if str(reaction.emoji) == EMOJI_NO:
            await ctx.send("✋ Pedido de cancelamento abortado.", delete_after=6)
            try: await ctx.message.delete()
            except: pass
            return
    except asyncio.TimeoutError:
        await ctx.send("⌛ Tempo esgotado. Pedido abortado.", delete_after=6)
        try: await ctx.message.delete()
        except: pass
        return
    partida = found_part
    opponent = partida["player2"] if uid == partida["player1"] else partida["player1"]
    partida.setdefault("cancel_attempts", {})[str(uid)] = True
    op_user = await safe_fetch_user(opponent)
    if not op_user:
        await ctx.send("❌ Não foi possível contatar o adversário via DM.", delete_after=6)
        try: await ctx.message.delete()
        except: pass
        return
    try:
        dm = await op_user.send(f"⚠️ <@{uid}> solicitou cancelar a partida. Reaja com {EMOJI_YES} para confirmar o cancelamento, ou {EMOJI_NO} para negar.")
        await dm.add_reaction(EMOJI_YES); await dm.add_reaction(EMOJI_NO)
        poll_message_map[dm.id] = ("cancel_ack", (found_mid, uid))
    except:
        await ctx.send("❌ Falha ao enviar DM ao adversário.", delete_after=6)
        try: await ctx.message.delete()
        except: pass
        return
    def check_op(reaction, user): return user.id == opponent and reaction.message.id == dm.id and str(reaction.emoji) in (EMOJI_YES, EMOJI_NO)
    try:
        reaction, user = await bot.wait_for("reaction_add", check=check_op, timeout=60)
        if str(reaction.emoji) == EMOJI_YES:
            partidas_ativas.pop(found_mid, None)
            if found_mid in torneio_data.get("pairings", {}):
                torneio_data["pairings"].pop(found_mid, None)
            save_json(TORNEIO_FILE, torneio_data)
            await ctx.send("✅ Partida cancelada por acordo entre os jogadores.", delete_after=6)
            p1u = await safe_fetch_user(partida["player1"]); p2u = await safe_fetch_user(partida["player2"])
            for u in (p1u, p2u):
                if u:
                    try: await u.send("✅ Partida cancelada por acordo entre os jogadores.")
                    except: pass
            await atualizar_painel()
        else:
            await ctx.send("❌ O adversário negou o cancelamento. Partida segue ativa.", delete_after=6)
    except asyncio.TimeoutError:
        await ctx.send("⌛ Tempo esgotado aguardando resposta do adversário.", delete_after=6)
    try: await ctx.message.delete()
    except: pass

# ---------------- STAT / HELP ----------------
@bot.command(name="statustorneio")
async def cmd_statustorneio(ctx):
    if not torneio_data.get("active"):
        await ctx.send("❌ Nenhum torneio ativo.", delete_after=6)
        try: await ctx.message.delete()
        except: pass
        return
    txt = f"🏆 RODADA {torneio_data.get('round')}/{torneio_data.get('rounds_target')} 🏆\n\nConfrontos:\n"
    for pid, p in torneio_data.get("pairings", {}).items():
        txt += f"{pid}: <@{p['player1']}> vs <@{p['player2']}> — {p.get('result') or 'Pendente'}\n"
    if torneio_data.get("byes"):
        txt += "\nByes: " + ", ".join([f"<@{u}>" for u in torneio_data["byes"]]) + "\n"
    await ctx.send(txt, delete_after=20)
    try: await ctx.message.delete()
    except: pass

# ---------------- ON_READY ----------------
@bot.event
async def on_ready():
    await atualizar_painel()
    print(Fore.GREEN + f"[READY] {bot.user} (id: {bot.user.id})")

# ---------------- AUTO-DELETE: apagar apenas a mensagem do usuário ao usar comando ----------------
@bot.event
async def on_command(ctx):
    # don't delete in DM
    try:
        if isinstance(ctx.channel, discord.DMChannel):
            return
    except:
        pass
    try:
        # small delay to let the command execute then delete
        await asyncio.sleep(2)
        await ctx.message.delete()
    except:
        pass

# ---------------- ON_MESSAGE handled earlier, ensure commands processed ----------------
# (already defined above)

# ---------------- ENTRY POINT ----------------

# ---------------- TOP CUT AUTOMÁTICO + Challonge (Top8 → Semis → Final + 3º lugar) ----------------
CHALLONGE_USERNAME = os.getenv("CHALLONGE_USERNAME", "") or os.getenv("CHALLONGE_USER", "")
CHALLONGE_API_KEY = os.getenv("CHALLONGE_API_KEY", "")

def challonge_auth():
    return (CHALLONGE_USERNAME, CHALLONGE_API_KEY)

def challonge_create_tournament(name):
    if not CHALLONGE_USERNAME or not CHALLONGE_API_KEY:
        print("[CHALLONGE] credenciais faltando, pulando criação.")
        return None
    url = "https://api.challonge.com/v1/tournaments.json"
    payload = {"tournament": {"name": name, "tournament_type": "single elimination", "open_signup": False}}
    try:
        r = requests.post(url, auth=challonge_auth(), json=payload, timeout=15)
        if r.status_code in (200,201):
            return r.json().get("tournament", {})
        else:
            print("[CHALLONGE] create error:", r.status_code, r.text)
            return None
    except Exception as e:
        print("[CHALLONGE] create exception:", e)
        return None

def challonge_create_participant(tournament_id_or_slug, display_name):
    if not CHALLONGE_USERNAME or not CHALLONGE_API_KEY:
        return None
    url = f"https://api.challonge.com/v1/tournaments/{tournament_id_or_slug}/participants.json"
    payload = {"participant": {"name": display_name}}
    try:
        r = requests.post(url, auth=challonge_auth(), json=payload, timeout=15)
        if r.status_code in (200,201):
            return r.json().get("participant", {})
        else:
            print("[CHALLONGE] participant error:", r.status_code, r.text)
            return None
    except Exception as e:
        print("[CHALLONGE] participant exception:", e)
        return None

def challonge_start_tournament(tournament_id_or_slug):
    if not CHALLONGE_USERNAME or not CHALLONGE_API_KEY:
        return None
    url = f"https://api.challonge.com/v1/tournaments/{tournament_id_or_slug}/start.json"
    try:
        r = requests.post(url, auth=challonge_auth(), timeout=15)
        if r.status_code in (200,201):
            return r.json()
        else:
            print("[CHALLONGE] start error:", r.status_code, r.text)
            return None
    except Exception as e:
        print("[CHALLONGE] start exception:", e)
        return None

def challonge_get_participants(tournament_id_or_slug):
    if not CHALLONGE_USERNAME or not CHALLONGE_API_KEY:
        return []
    url = f"https://api.challonge.com/v1/tournaments/{tournament_id_or_slug}/participants.json"
    try:
        r = requests.get(url, auth=challonge_auth(), timeout=15)
        if r.status_code == 200:
            return [p.get("participant", {}) for p in r.json()]
        else:
            print("[CHALLONGE] get participants error:", r.status_code, r.text)
            return []
    except Exception as e:
        print("[CHALLONGE] get participants exception:", e)
        return []

def challonge_get_matches(tournament_id_or_slug):
    if not CHALLONGE_USERNAME or not CHALLONGE_API_KEY:
        return []
    url = f"https://api.challonge.com/v1/tournaments/{tournament_id_or_slug}/matches.json"
    try:
        r = requests.get(url, auth=challonge_auth(), timeout=15)
        if r.status_code == 200:
            return [m.get("match", {}) for m in r.json()]
        else:
            print("[CHALLONGE] get matches error:", r.status_code, r.text)
            return []
    except Exception as e:
        print("[CHALLONGE] get matches exception:", e)
        return []

def challonge_update_match_result_by_player_names(tournament_id_or_slug, player1_name, player2_name, winner_name):
    parts = challonge_get_participants(tournament_id_or_slug)
    name_to_pid = {p.get("name"): p.get("id") for p in parts}
    p1_id = name_to_pid.get(player1_name)
    p2_id = name_to_pid.get(player2_name)
    if not p1_id or not p2_id:
        print("[CHALLONGE] participant ids not found for update:", player1_name, player2_name)
        return False
    matches = challonge_get_matches(tournament_id_or_slug)
    target = None
    for m in matches:
        if (m.get("player1_id") == p1_id and m.get("player2_id") == p2_id) or (m.get("player1_id") == p2_id and m.get("player2_id") == p1_id):
            target = m
            break
    if not target:
        print("[CHALLONGE] match not found to update for", player1_name, player2_name)
        return False
    match_id = target.get("id")
    winner_pid = name_to_pid.get(winner_name)
    if not match_id or not winner_pid:
        print("[CHALLONGE] missing match id or winner id")
        return False
    url = f"https://api.challonge.com/v1/tournaments/{tournament_id_or_slug}/matches/{match_id}.json"
    payload = {"match": {"winner_id": winner_pid}}
    try:
        r = requests.put(url, auth=challonge_auth(), json=payload, timeout=15)
        if r.status_code in (200,201):
            return True
        else:
            print("[CHALLONGE] update match error:", r.status_code, r.text)
            return False
    except Exception as e:
        print("[CHALLONGE] update match exception:", e)
        return False

def format_participant_displayname(uid):
    try:
        u = bot.get_user(uid)
        if u:
            return f"{u.name}#{u.discriminator} ({uid})"
    except:
        pass
    return str(uid)

def get_top8_from_scores():
    scores = torneio_data.get('scores', {})
    if not scores:
        return []
    sorted_players = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top_players = [int(uid) for uid, _ in sorted_players[:8]]
    return top_players

async def create_topcut_and_challonge(top_players):
    tsname = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M")
    challonge_info = None
    if CHALLONGE_USERNAME and CHALLONGE_API_KEY:
        name = f"OPTCG-Sorocaba-{tsname}"
        challonge_info = challonge_create_tournament(name)
        if challonge_info:
            slug_or_id = challonge_info.get("id") or challonge_info.get("url") or challonge_info.get("full_challonge_url") or challonge_info.get("web_path") or challonge_info.get("slug")
            # add participants
            for uid in top_players:
                display = format_participant_displayname(uid)
                challonge_create_participant(slug_or_id, display)
                await asyncio.sleep(0.4)
            challonge_start_tournament(slug_or_id)
            torneio_data.setdefault('topcut', {})['challonge'] = {'slug': slug_or_id, 'created_at': now_iso(), 'url': challonge_info.get('full_challonge_url') or challonge_info.get('url')}
            save_json(TORNEIO_FILE, torneio_data)
    # create local matches (1v8,2v7,3v6,4v5)
    pairs = [(0,7),(1,6),(2,5),(3,4)]
    created = []
    ts = int(datetime.datetime.utcnow().timestamp())
    for idx, (a,b) in enumerate(pairs):
        if a >= len(top_players) or b >= len(top_players):
            continue
        p1 = top_players[a]; p2 = top_players[b]
        match_id = f"top_{p1}_{p2}_{ts}_{idx}"
        partidas_ativas[match_id] = {
            "player1": p1,
            "player2": p2,
            "attempts": {},
            "cancel_attempts": {},
            "source": "topcut",
            "timestamp": now_iso(),
            "polls": []
        }
        await send_result_poll(match_id, partidas_ativas[match_id])
        created.append(match_id)
    torneio_data.setdefault('topcut', {})['players'] = top_players
    torneio_data['topcut']['matches'] = created
    torneio_data['topcut']['started'] = True
    torneio_data['topcut'].setdefault('winners', [])
    save_json(TORNEIO_FILE, torneio_data)
    await atualizar_painel()
    return challonge_info

@bot.command(name="iniciartopcut")
async def cmd_iniciar_topcut(ctx):
    if ctx.author.id != BOT_OWNER:
        try: await ctx.message.delete()
        except: pass
        await ctx.send("❌ Apenas o dono pode iniciar o Top Cut.", delete_after=6)
        return
    players = torneio_data.get("players", [])
    if len(players) < 32:
        try: await ctx.message.delete()
        except: pass
        await ctx.send("❌ Top Cut requer pelo menos 32 inscritos.", delete_after=8)
        return
    # ensure swiss finished
    if torneio_data.get("rounds_target") and torneio_data.get("round",0) < torneio_data.get("rounds_target",0):
        try: await ctx.message.delete()
        except: pass
        await ctx.send("❌ O suíço ainda não terminou. Finalize as rondas ou use !forçarrodada.", delete_after=8)
        return
    top8 = get_top8_from_scores()
    if not top8 or len(top8) < 8:
        try: await ctx.message.delete()
        except: pass
        await ctx.send("❌ Falha ao calcular Top 8 (poucos jogadores com pontuação).", delete_after=8)
        return
    info = await create_topcut_and_challonge(top8)
    ch = bot.get_channel(PANEL_CHANNEL_ID)
    if ch:
        txt = f"🏆 Top Cut iniciado (Top 8): " + ", ".join([f"<@{u}>" for u in top8])
        if info:
            txt += f"\\n🔗 Challonge: {info.get('full_challonge_url') or info.get('url') or info.get('web_path') or info.get('id')}"
        await ch.send(txt)
    try: await ctx.message.delete()
    except: pass

@bot.command(name="statustopcut")
async def cmd_statustopcut(ctx):
    tc = torneio_data.get("topcut", {})
    if not tc or not tc.get("started"):
        await ctx.send("❌ Top Cut não iniciado.", delete_after=6)
        try: await ctx.message.delete()
        except: pass
        return
    lines = ["🏆 **Top Cut — Status** 🏆", "\\n**Top 8:**"]
    for u in tc.get("players", []):
        lines.append(f"• <@{u}>")
    lines.append("\\n**Confrontos:**")
    for mid in tc.get("matches", []):
        p = partidas_ativas.get(mid)
        if p:
            lines.append(f"• {mid}: <@{p['player1']}> vs <@{p['player2']}>")
        else:
            lines.append(f"• {mid}: finalizada")
    await ctx.send("\\n".join(lines), delete_after=30)
    try: await ctx.message.delete()
    except: pass

async def notify_challonge_of_result_for_match(match_id, winner_uid, loser_uid):
    tc = torneio_data.get("topcut", {})
    chall_info = tc.get("challonge")
    if not chall_info:
        return False
    slug = chall_info.get("slug")
    if not slug:
        return False
    winner_name = format_participant_displayname(winner_uid)
    loser_name = format_participant_displayname(loser_uid)
    ok = challonge_update_match_result_by_player_names(slug, winner_name, loser_name, winner_name)
    if not ok:
        print("[CHALLONGE] failed to update result for", match_id)
    return ok

# Wrap existing finalize_match_result if present to add topcut syncing
try:
    _orig_finalize_match_local = finalize_match_result
except NameError:
    _orig_finalize_match_local = None

async def _finalize_and_sync(match_id, partida, emoji_choice):
    # call original finalizer if available
    if _orig_finalize_match_local:
        await _orig_finalize_match_local(match_id, partida, emoji_choice)
    # if this match is part of topcut, sync and progress bracket
    try:
        if partida.get("source", "").startswith("topcut"):
            if emoji_choice == EMOJI_ONE:
                winner = partida["player1"]; loser = partida["player2"]
            elif emoji_choice == EMOJI_TWO:
                winner = partida["player2"]; loser = partida["player1"]
            else:
                winner = None; loser = None
            if winner:
                tc = torneio_data.setdefault("topcut", {})
                tc.setdefault("winners", []).append(winner)
                save_json(TORNEIO_FILE, torneio_data)
                # attempt to sync with challonge
                await notify_challonge_of_result_for_match(match_id, winner, loser)
                # check if quarterfinals done
                matches = tc.get("matches", [])
                remaining = [m for m in matches if m in partidas_ativas]
                if not remaining and matches:
                    # create semifinals from winners (order as appended)
                    winners = tc.get("winners", [])
                    if len(winners) >= 4 and not tc.get("semifinals"):
                        semipairs = [(0,3),(1,2)]
                        sem_matches = []
                        ts = int(datetime.datetime.utcnow().timestamp())
                        for idx,(a,b) in enumerate(semipairs):
                            if a >= len(winners) or b >= len(winners):
                                continue
                            p1 = winners[a]; p2 = winners[b]
                            mid = f"sem_{p1}_{p2}_{ts}_{idx}"
                            partidas_ativas[mid] = {"player1": p1, "player2": p2, "attempts": {}, "cancel_attempts": {}, "source": "topcut_semifinal", "timestamp": now_iso(), "polls": []}
                            await send_result_poll(mid, partidas_ativas[mid])
                            sem_matches.append(mid)
                        tc['semifinals'] = sem_matches
                        tc['winners_semis'] = []
                        tc['losers_semis'] = []
                        save_json(TORNEIO_FILE, torneio_data)
                        await atualizar_painel()
                # handle semifinal -> final progression (track winners by hooking into finalize flow further if needed)
    except Exception as e:
        print("[TOPCUT SYNC ERROR]", e)

# Replace global finalizer
globals()['finalize_match_result'] = _finalize_and_sync
# -----------------------------------------------------------------------------------------------


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print(Fore.RED + "❌ DISCORD_TOKEN não definido nas variáveis de ambiente.")
    else:
        try:
            bot.run(DISCORD_TOKEN)
        except Exception as e:
            print(Fore.RED + "❌ Erro ao iniciar o bot:", e)
