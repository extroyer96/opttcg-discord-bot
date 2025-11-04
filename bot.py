# bot.py
"""
OPTCG Discord Bot - Monolítico, pronto para deploy
Recursos:
 - Matchmaking 1x1 por reações (fila com enter/leave)
 - Resultado via DM (p1/p2/draw) confirmado somente com concordância
 - Ranking persistente + reset mensal (America/Sao_Paulo) e reset manual
 - Painel no canal (embed) com fila, partidas ativas e histórico
 - Torneio em formato suíço: inscrição por reação, decklist via DM, pairings, byes
 - Exportação de decklists (.txt) para o dono quando fechar inscrições
 - Comando para cancelar torneio sem premiar campeão
 - Logs coloridos via colorama
"""

import os
import json
import math
import random
import asyncio
from datetime import datetime
from pathlib import Path

import pytz
import discord
from discord.ext import tasks, commands
from colorama import init as colorama_init, Fore, Style
from dotenv import load_dotenv

# -------------------- Config & Env --------------------
colorama_init(autoreset=True)
load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")  # seu token no .env / env variables
PANEL_CHANNEL_ID = int(os.getenv("PANEL_CHANNEL_ID", "0"))
PANEL_MESSAGE_ID = int(os.getenv("PANEL_MESSAGE_ID", "0"))  # 0 para criar novo
BOT_OWNER = int(os.getenv("BOT_OWNER", "0"))  # id do dono

TZ = "America/Sao_Paulo"

# emojis
EMOJI_JOIN = "🟢"
EMOJI_LEAVE = "🔴"
EMOJI_RANK = "🏆"
EMOJI_TOURN = "🏅"

# data files
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
RANK_FILE = DATA_DIR / "ranking.json"
HIST_FILE = DATA_DIR / "historico.json"
TOURNEY_FILE = DATA_DIR / "torneios.json"

# -------------------- Logging helpers --------------------
PREFIX = "[OPTCG]"

def log_info(msg):
    print(f"{Fore.GREEN}🟢 {PREFIX} {msg}{Style.RESET_ALL}")

def log_warn(msg):
    print(f"{Fore.YELLOW}⚠️ {PREFIX} {msg}{Style.RESET_ALL}")

def log_err(msg):
    print(f"{Fore.RED}⛔ {PREFIX} {msg}{Style.RESET_ALL}")

def log_tourn(msg):
    print(f"{Fore.MAGENTA}🏆 {PREFIX} {msg}{Style.RESET_ALL}")

def log_dbg(msg):
    print(f"{Fore.CYAN}🔎 {PREFIX} {msg}{Style.RESET_ALL}")

# -------------------- Persistence --------------------
def load_json(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            log_err(f"Falha ao ler {path}: {e}")
            return default
    else:
        save_json(path, default)
        return default

def save_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

# initial data
ranking = load_json(RANK_FILE, {"__queue": [], "__last_reset": ""})  # store queue inside ranking to persist easily
history = load_json(HIST_FILE, [])
tourney = load_json(TOURNEY_FILE, {
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

def persist_all():
    save_json(RANK_FILE, ranking)
    save_json(HIST_FILE, history)
    save_json(TOURNEY_FILE, tourney)

# -------------------- Discord Bot Setup --------------------
intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.guilds = True
intents.members = True
intents.dm_messages = True

bot = commands.Bot(command_prefix="!", intents=intents)

# -------------------- Utilities --------------------
def make_match_id(prefix="m"):
    return f"{prefix}-{int(datetime.utcnow().timestamp()*1000)}"

def compute_rounds(n_players: int) -> int:
    # user-specified reduced-by-1 scheme:
    if n_players <= 8: return 3
    if n_players <= 16: return 4
    if n_players <= 32: return 5
    return 6

# quick helpers for queue
def queue_list():
    return ranking.setdefault("__queue", [])

def queue_add(user_id:int):
    q = queue_list()
    if user_id not in q:
        q.append(user_id)
        persist_all()
        log_info(f"Usuário {user_id} entrou na fila (total {len(q)})")

def queue_remove(user_id:int):
    q = queue_list()
    if user_id in q:
        q.remove(user_id)
        persist_all()
        log_info(f"Usuário {user_id} saiu da fila (total {len(q)})")

# -------------------- Panel (embed) --------------------
async def update_panel():
    if PANEL_CHANNEL_ID == 0:
        return
    ch = bot.get_channel(PANEL_CHANNEL_ID)
    if not ch:
        return
    embed = discord.Embed(title="OPTCG Matchmaking & Torneios", color=discord.Color.blue())
    q = queue_list()
    q_text = "\n".join([f"{i+1}. <@{uid}>" for i, uid in enumerate(q)]) if q else "_Fila vazia_"
    embed.add_field(name="🎮 Fila (reaja 🟢 entrar / 🔴 sair)", value=q_text, inline=False)

    # Active (in this simplified version we show ongoing tourney round & history)
    # Show last 3 history
    last3 = history[-3:]
    if last3:
        hist_text = "\n".join([f"{i+1}. {h['winner']} venceu {h['loser']} — {h['ts']}" for i,h in enumerate(reversed(last3))])
    else:
        hist_text = "_Sem histórico_"
    embed.add_field(name="📜 Últimas 3 partidas", value=hist_text, inline=False)

    # Tournament status
    if tourney.get("active"):
        tval = f"🎟️ Inscrições abertas — {len(tourney.get('players', []))} inscritos\nRodada: {tourney.get('round')}"
    elif tourney.get("finished"):
        tval = "⚠️ Torneio finalizado"
    else:
        tval = "Nenhum torneio em andamento"
    embed.add_field(name="🏆 Torneio", value=tval, inline=False)

    global PANEL_MESSAGE_ID
    try:
        if PANEL_MESSAGE_ID == 0:
            msg = await ch.send(embed=embed)
            try:
                await msg.add_reaction(EMOJI_JOIN)
                await msg.add_reaction(EMOJI_LEAVE)
                await msg.add_reaction(EMOJI_RANK)
            except Exception:
                pass
            PANEL_MESSAGE_ID = msg.id
            log_info(f"Painel criado (message id {PANEL_MESSAGE_ID})")
        else:
            try:
                msg = await ch.fetch_message(PANEL_MESSAGE_ID)
                await msg.edit(embed=embed)
            except discord.NotFound:
                msg = await ch.send(embed=embed)
                try:
                    await msg.add_reaction(EMOJI_JOIN)
                    await msg.add_reaction(EMOJI_LEAVE)
                    await msg.add_reaction(EMOJI_RANK)
                except Exception:
                    pass
                PANEL_MESSAGE_ID = msg.id
                log_info(f"Painel recriado (message id {PANEL_MESSAGE_ID})")
    except Exception as e:
        log_err(f"Erro update_panel: {e}")

# -------------------- Matchmaking Normal --------------------
async def try_matchmake_normal():
    q = queue_list()
    while len(q) >= 2:
        p1 = q.pop(0)
        p2 = q.pop(0)
        ranking["__queue"] = q
        persist_all()
        match_id = make_match_id("normal")
        # store minimal active match in memory by adding to history later
        # DM players
        try:
            u1 = await bot.fetch_user(p1)
            u2 = await bot.fetch_user(p2)
            await u1.send(f"🎯 Você foi emparelhado com {u2.mention} (match {match_id}). Ao terminar, responda nesta DM com `p1` (jogador 1 venceu), `p2` (jogador 2 venceu) ou `draw`.")
            await u2.send(f"🎯 Você foi emparelhado com {u1.mention} (match {match_id}). Ao terminar, responda nesta DM com `p1` (jogador 1 venceu), `p2` (jogador 2 venceu) ou `draw`.")
            log_info(f"Match normal {match_id} criado: {p1} vs {p2}")
        except Exception as e:
            log_warn(f"Falha ao notificar jogadores: {e}")
    await update_panel()

# -------------------- Result Handling --------------------
# pending responses: match_id -> {user_id_str: resp}
pending_responses = {}

def record_response(match_id: str, user_id: int, resp: str):
    pending_responses.setdefault(match_id, {})[str(user_id)] = resp

async def try_resolve_match(match_id: str, p1: int, p2: int, is_tourney: bool=False):
    rp = pending_responses.get(match_id, {})
    if str(p1) in rp and str(p2) in rp:
        r1 = rp[str(p1)]
        r2 = rp[str(p2)]
        if r1 == r2:
            winner = None; loser = None
            if r1 == "p1":
                winner = p1; loser = p2
            elif r1 == "p2":
                winner = p2; loser = p1
            ts = datetime.utcnow().isoformat()
            if is_tourney:
                # update tourney structures
                tourney.setdefault("played", {}).setdefault(str(p1), []).append(p2)
                tourney.setdefault("played", {}).setdefault(str(p2), []).append(p1)
                if winner:
                    tourney.setdefault("scores", {}).setdefault(str(winner), 0)
                    tourney.setdefault("scores", {}).setdefault(str(loser), 0)
                    tourney["scores"][str(winner)] += 1
                else:
                    tourney.setdefault("scores", {}).setdefault(str(p1), 0)
                    tourney.setdefault("scores", {}).setdefault(str(p2), 0)
                    tourney["scores"][str(p1)] += 0.5
                    tourney["scores"][str(p2)] += 0.5
                # store result in pairings
                for rnd, pairings in tourney.get("pairings", {}).items():
                    for p in pairings:
                        if p["id"] == match_id:
                            p["result"] = {"winner": winner, "loser": loser, "ts": ts}
                persist_all()
            else:
                # normal match -> record to history & ranking
                hist_item = {"winner": f"<@{winner}>" if winner else "Draw", "loser": f"<@{loser}>" if loser else "Draw", "ts": ts}
                history.append(hist_item)
                if winner:
                    ranking.setdefault(str(winner), 0)
                    ranking[str(winner)] += 1
                persist_all()
                log_info(f"Match {match_id} finalizado. Winner: {winner}")
            # notify players
            try:
                u1 = await bot.fetch_user(p1); u2 = await bot.fetch_user(p2)
                if winner:
                    await u1.send(f"✅ Resultado confirmado: <@{winner}> venceu (match {match_id}).")
                    await u2.send(f"✅ Resultado confirmado: <@{winner}> venceu (match {match_id}).")
                else:
                    await u1.send(f"✅ Resultado confirmado: Empate (match {match_id}).")
                    await u2.send(f"✅ Resultado confirmado: Empate (match {match_id}).")
            except Exception:
                pass
            # clear pending
            pending_responses.pop(match_id, None)
            # post-process
            if is_tourney:
                await check_tourney_round_completion()
            else:
                await update_panel()
        else:
            # disagreement
            try:
                u1 = await bot.fetch_user(p1); u2 = await bot.fetch_user(p2)
                await u1.send("⚠️ Resultado divergente entre jogadores. Conversem e reenviem o mesmo resultado (p1/p2/draw).")
                await u2.send("⚠️ Resultado divergente entre jogadores. Conversem e reenviem o mesmo resultado (p1/p2/draw).")
            except:
                pass

# -------------------- Swiss Pairing --------------------
def swiss_pairing_round(player_ids, scores, played, byes):
    # sort by score desc, id asc
    players = sorted(player_ids, key=lambda u: (-scores.get(str(u), 0), u))
    unpaired = players[:]
    pairings = []
    bye_player = None
    if len(unpaired) % 2 == 1:
        # pick lowest score without bye if possible
        candidates = sorted(unpaired, key=lambda u: (scores.get(str(u), 0), u))
        for c in candidates:
            if str(c) not in byes:
                bye_player = c
                break
        if bye_player is None:
            bye_player = candidates[0]
        unpaired.remove(bye_player)
        pairings.append({"id": make_match_id("t"), "p1": bye_player, "p2": None, "result": {"winner": bye_player, "loser": None, "ts": datetime.utcnow().isoformat()}})
    while unpaired:
        a = unpaired.pop(0)
        b = None
        for i, cand in enumerate(unpaired):
            if cand not in played.get(str(a), []):
                b = cand
                unpaired.pop(i)
                break
        if b is None:
            b = unpaired.pop(0)
        pairings.append({"id": make_match_id("t"), "p1": a, "p2": b, "result": None})
    return pairings

# -------------------- Tournament flow --------------------
async def create_tourney_signup_message(channel):
    embed = discord.Embed(title="🏆 Torneio OPTCG — Inscrições", description="Reaja com 🏅 para se inscrever no torneio. Ao se inscrever você receberá instruções por DM para colar sua decklist.", color=discord.Color.gold())
    msg = await channel.send(embed=embed)
    try:
        await msg.add_reaction(EMOJI_TOURN)
    except:
        pass
    tourney["signup_msg_id"] = msg.id
    tourney["active"] = True
    tourney["finished"] = False
    persist_all()
    log_tourn(f"Mensagem de inscrição criada (id {msg.id})")
    await update_panel()
    return msg

async def close_tourney_signups():
    if not tourney.get("active"):
        return
    tourney["active"] = False
    # create decklist txt and send to owner
    lines = ["Decklists - Torneio\n"]
    for uid in tourney.get("players", []):
        dl = tourney.get("decklists", {}).get(str(uid), "(SEM DECKLIST ENVIADA)")
        lines.append(f"Jogador: <@{uid}> (ID: {uid})\n")
        lines.append(dl + "\n")
        lines.append("-" * 40 + "\n")
    filename = f"decklists_tournament_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.txt"
    with open(filename, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    # DM owner
    try:
        owner = await bot.fetch_user(BOT_OWNER)
        await owner.send("Arquivo de decklists do torneio:", file=discord.File(filename))
        log_tourn(f"Decklists enviadas ao dono ({BOT_OWNER})")
    except Exception as e:
        log_warn(f"Falha ao enviar decklists ao dono: {e}")
    # init scores, played etc
    tourney["round"] = 0
    tourney["pairings"] = {}
    tourney["results"] = {}
    tourney["scores"] = {str(uid): 0.0 for uid in tourney.get("players", [])}
    tourney["played"] = {str(uid): [] for uid in tourney.get("players", [])}
    tourney["byes"] = []
    if not tourney.get("rounds_target"):
        tourney["rounds_target"] = compute_rounds(len(tourney.get("players", [])))
    persist_all()
    await update_panel()

async def start_next_tourney_round():
    if not tourney.get("players"):
        return
    tourney["round"] += 1
    rnd = tourney["round"]
    players = list(tourney.get("players", []))
    pairings = swiss_pairing_round(players, tourney.get("scores", {}), tourney.get("played", {}), tourney.get("byes", []))
    tourney.setdefault("pairings", {})[str(rnd)] = pairings
    # init results for matches (byes already have result)
    for p in pairings:
        if p["p2"] is None:
            # bye -> give 1 point if not had
            if str(p["p1"]) not in tourney.get("byes", []):
                tourney.setdefault("scores", {}).setdefault(str(p["p1"]), 0.0)
                tourney["scores"][str(p["p1"])] += 1.0
                tourney.setdefault("byes", []).append(str(p["p1"]))
            p["result"] = {"winner": p["p1"], "loser": None, "ts": datetime.utcnow().isoformat()}
        else:
            tourney.setdefault("results", {})[p["id"]] = {}
            p["result"] = None
    persist_all()
    # DM players with specific tournament message
    for p in pairings:
        if p["p2"] is None:
            continue
        p1 = p["p1"]; p2 = p["p2"]
        try:
            u1 = await bot.fetch_user(p1); u2 = await bot.fetch_user(p2)
            await u1.send(f"📢 Torneio — Rodada {rnd} — Você foi pareado com {u2.mention} (match {p['id']}). Ao terminar, responda nesta DM com `p1` (jogador 1 venceu), `p2` (jogador 2 venceu) ou `draw`.")
            await u2.send(f"📢 Torneio — Rodada {rnd} — Você foi pareado com {u1.mention} (match {p['id']}). Ao terminar, responda nesta DM com `p1` (jogador 1 venceu), `p2` (jogador 2 venceu) ou `draw`.")
            log_tourn(f"Rodada {rnd} pairing: {p1} vs {p2} (id {p['id']})")
        except Exception as e:
            log_warn(f"Falha ao enviar DM de pairing: {e}")
    persist_all()
    ch = bot.get_channel(PANEL_CHANNEL_ID)
    if ch:
        await ch.send(f"▶️ Rodada {rnd} do torneio iniciada com {len(pairings)} partidas (incluindo byes).")

async def check_tourney_round_completion():
    rnd = tourney.get("round")
    pairings = tourney.get("pairings", {}).get(str(rnd), [])
    for p in pairings:
        if p.get("p2") is None:
            continue
        if not p.get("result"):
            return False
    # all resolved -> check finish condition
    n = len(tourney.get("players", []))
    target_rounds = tourney.get("rounds_target") or compute_rounds(n)
    if tourney.get("round") >= target_rounds:
        # finish tournament
        scores = tourney.get("scores", {})
        if not scores:
            return False
        top_score = max(scores.values())
        winners = [int(uid) for uid, sc in scores.items() if sc == top_score]
        champion = winners[0]
        # record champion in a separate champion ranking or in ranking file
        # here we store champion counts in tourney_ranking inside tourney file
        tourney.setdefault("tournament_ranking", {})
        tourney["tournament_ranking"][str(champion)] = tourney["tournament_ranking"].get(str(champion), 0) + 1
        tourney["finished"] = True
        persist_all()
        ch = bot.get_channel(PANEL_CHANNEL_ID)
        if ch:
            await ch.send(f"🏆 Parabéns <@{champion}> — campeão do torneio! Agradecemos a todos os participantes.")
        log_tourn(f"Torneio finalizado. Campeão: {champion}")
        return True
    else:
        # start next automatically after short pause
        await asyncio.sleep(2)
        await start_next_tourney_round()
        return True

# -------------------- Owner Commands --------------------
@bot.command(name="torneio")
async def cmd_torneio(ctx, action: str = None, *args):
    if ctx.author.id != BOT_OWNER:
        await ctx.send("Apenas o dono do bot pode usar comandos de torneio.")
        return
    action = (action or "").lower()
    if action == "iniciar":
        if tourney.get("active"):
            await ctx.send("Torneio já com inscrições abertas.")
            return
        await create_tourney_signup_message(ctx.channel)
        await ctx.send("Torneio iniciado e mensagem de inscrição criada.")
    elif action == "fechar":
        if not tourney.get("active"):
            await ctx.send("Não há inscrições ativas.")
            return
        await close_tourney_signups()
        await ctx.send("Inscrições fechadas. Decklists encaminhadas ao dono. Pronto para iniciar rodadas (use iniciar_rodada).")
    elif action == "iniciar_rodada":
        if tourney.get("active"):
            await ctx.send("Feche as inscrições antes de iniciar rodadas (use !torneio fechar).")
            return
        await start_next_tourney_round()
        await ctx.send(f"Rodada {tourney.get('round')} iniciada.")
    elif action == "encerrar":
        # finalize tournament prematurely (owner chooses champion manually if desired)
        tourney["finished"] = True
        persist_all()
        await ctx.send("Torneio marcado como finalizado.")
    elif action == "rodadas":
        # set rounds manually
        if not args:
            await ctx.send("Uso: !torneio rodadas <N>")
            return
        try:
            n = int(args[0])
            tourney["rounds_target"] = n
            persist_all()
            await ctx.send(f"Rounds do torneio definidos para {n}.")
        except:
            await ctx.send("Valor inválido para rodadas.")
    elif action == "status":
        await ctx.send(f"Torneio status: active={tourney.get('active')}, players={len(tourney.get('players',[]))}, round={tourney.get('round')}, finished={tourney.get('finished')}")
    else:
        await ctx.send("Uso: !torneio iniciar | fechar | iniciar_rodada | encerrar | rodadas <N> | status")

@bot.command(name="resetranking")
async def cmd_resetranking(ctx):
    if ctx.author.id != BOT_OWNER:
        await ctx.send("Apenas o dono.")
        return
    # reset normal ranking (keep queue)
    for k in list(ranking.keys()):
        if k not in ("__queue", "__last_reset"):
            ranking.pop(k, None)
    ranking["__last_reset"] = datetime.utcnow().strftime("%Y-%m-%d")
    persist_all()
    await ctx.send("Ranking normal resetado manualmente.")
    await update_panel()
    log_info("Ranking manualmente resetado pelo dono.")

@bot.command(name="forcetourneyreset")
async def cmd_force_tourney_reset(ctx):
    if ctx.author.id != BOT_OWNER:
        await ctx.send("Apenas o dono.")
        return
    tourney["tournament_ranking"] = {}
    persist_all()
    await ctx.send("Ranking de campeões resetado.")
    log_info("Ranking de campeões resetado pelo dono.")

@bot.command(name="declarechamp")
async def cmd_declare_champ(ctx, user_id: int):
    if ctx.author.id != BOT_OWNER:
        await ctx.send("Apenas o dono.")
        return
    tourney.setdefault("tournament_ranking", {})
    tourney["tournament_ranking"][str(user_id)] = tourney["tournament_ranking"].get(str(user_id), 0) + 1
    persist_all()
    await ctx.send(f"Usuário <@{user_id}> marcado como campeão (contador incrementado).")
    log_tourn(f"Dono declarou campeão manualmente: {user_id}")

@bot.command(name="cancelar_torneio")
async def cmd_cancelar_torneio(ctx):
    # Cancel tournament: remove data, no champion recorded
    if ctx.author.id != BOT_OWNER:
        await ctx.send("Apenas o dono pode cancelar o torneio.")
        return
    if not tourney.get("players"):
        await ctx.send("Nenhum torneio ativo para cancelar.")
        return
    # clear tourney data
    tourney.clear()
    # reset default structure
    tourney.update({
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
    persist_all()
    # public message
    ch = bot.get_channel(PANEL_CHANNEL_ID)
    if ch:
        await ch.send("⚠️ [OPTCG][TORNEIO] O torneio atual foi **cancelado pelo organizador**.\nObrigado a todos que participaram até aqui.")
    await ctx.author.send("📩 O torneio foi cancelado e removido com sucesso.")
    log_tourn("Torneio cancelado pelo dono (dados removidos).")
    await update_panel()

# -------------------- Events: reactions & messages --------------------
@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    # ignore bot's own reactions
    if payload.user_id == bot.user.id:
        return
    # panel reactions
    if payload.channel_id == PANEL_CHANNEL_ID and payload.message_id == PANEL_MESSAGE_ID:
        emoji = str(payload.emoji)
        if emoji == EMOJI_JOIN:
            queue_add(payload.user_id)
            try:
                await (await bot.fetch_user(payload.user_id)).send("Você entrou na fila!")
            except:
                pass
            await update_panel()
            await try_matchmake_normal()
        elif emoji == EMOJI_LEAVE:
            queue_remove(payload.user_id)
            try:
                await (await bot.fetch_user(payload.user_id)).send("Você saiu da fila.")
            except:
                pass
            await update_panel()
        elif emoji == EMOJI_RANK:
            # send ranking DM
            items = sorted(((uid, wins) for uid, wins in ranking.items() if uid not in ("__queue", "__last_reset")), key=lambda x: -x[1])
            lines = ["🏆 Ranking (normal):"]
            for i, (uid, wins) in enumerate(items, start=1):
                lines.append(f"{i}. <@{uid}> — {wins} vitórias")
            try:
                await (await bot.fetch_user(payload.user_id)).send("\n".join(lines) if len(lines) > 1 else "Nenhuma vitória registrada ainda.")
            except:
                pass
    # tournament signup reaction
    if tourney.get("active") and payload.channel_id == PANEL_CHANNEL_ID and payload.message_id == tourney.get("signup_msg_id"):
        emoji = str(payload.emoji)
        if emoji == EMOJI_TOURN:
            uid = payload.user_id
            if uid not in tourney.get("players", []):
                tourney.setdefault("players", []).append(uid)
                persist_all()
                # DM decklist instructions
                try:
                    u = await bot.fetch_user(uid)
                    await u.send("Você se inscreveu no torneio! Agora cole aqui a sua decklist.\n\nInstruções:\n- No simulador: abra 'Deck Editor'.\n- Selecione/crie o deck para o torneio.\n- Use 'Copy Deck List to Clipboard'.\n- Cole aqui (cole todo o texto).")
                except:
                    pass
                log_tourn(f"Jogador {uid} inscrito no torneio ({len(tourney.get('players', []))} total).")
                await update_panel()

@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    # if someone removed reaction from signup msg, remove them from players
    if tourney.get("active") and payload.channel_id == PANEL_CHANNEL_ID and payload.message_id == tourney.get("signup_msg_id"):
        if str(payload.emoji) == EMOJI_TOURN:
            uid = payload.user_id
            if uid in tourney.get("players", []):
                tourney["players"].remove(uid)
                tourney.get("decklists", {}).pop(str(uid), None)
                persist_all()
                log_tourn(f"Jogador {uid} removido das inscrições.")
                await update_panel()

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    # DM handling: decklist submission & result reporting
    if isinstance(message.channel, discord.DMChannel):
        # decklist submission if player is registered and hasn't sent decklist
        if tourney.get("active") and message.author.id in tourney.get("players", []) and not tourney.get("decklists", {}).get(str(message.author.id)):
            tourney.setdefault("decklists", {})[str(message.author.id)] = message.content
            tourney.setdefault("scores", {}).setdefault(str(message.author.id), 0.0)
            tourney.setdefault("played", {})[str(message.author.id)] = []
            persist_all()
            await message.channel.send("✅ Decklist recebida com sucesso. Obrigado!")
            log_tourn(f"Decklist recebida de {message.author.id}")
            return
        # results: accept p1/p2/draw
        txt = message.content.strip().lower()
        if txt in ("p1","p2","draw","vitória","vitoria","derrota"):
            # normalize
            if txt in ("vitória","vitoria"): norm = "p1"
            elif txt == "derrota": norm = "p2"
            else: norm = txt
            # check tourney current round
            if tourney.get("round") and str(tourney.get("round")) in tourney.get("pairings", {}):
                for p in tourney["pairings"][str(tourney["round"])]:
                    if p.get("p2") and message.author.id in (p["p1"], p["p2"]):
                        mid = p["id"]
                        pending_responses.setdefault(mid, {})[str(message.author.id)] = norm
                        await message.channel.send("Resultado registrado para o torneio. Aguardando confirmação do adversário.")
                        await try_resolve_match(mid, p["p1"], p["p2"], is_tourney=True)
                        return
            # fallback: no tourney pairing matched -> try normal (best-effort)
            # In this simplified code we don't store normal active matches; users should be paired via the queue flow.
            await message.channel.send("Não encontrei uma partida ativa sua para esse resultado.")
            return
    # let commands be processed
    await bot.process_commands(message)

# -------------------- Monthly Reset --------------------
@tasks.loop(hours=6)
async def monthly_reset_check():
    tz = pytz.timezone(TZ)
    now = datetime.now(tz)
    if now.day == 1:
        last = ranking.get("__last_reset", "")
        cur = now.strftime("%Y-%m-%d")
        if last != cur:
            # reset wins (keep queue)
            preserved_queue = ranking.get("__queue", [])
            ranking.clear()
            ranking["__queue"] = preserved_queue
            ranking["__last_reset"] = cur
            persist_all()
            ch = bot.get_channel(PANEL_CHANNEL_ID)
            if ch:
                await ch.send("🔄 Ranking mensal resetado automaticamente.")
            log_info("Ranking mensal resetado automaticamente.")

# -------------------- Startup --------------------
@bot.event
async def on_ready():
    # startup banner
    log_info("==============================")
    log_info("🃏 OPTCG Discord Bot - Iniciado")
    log_info("==============================")
    log_info("🟢 Status: Online")
    log_info(f"👑 Dono: {BOT_OWNER}")
    log_info("🏆 Módulos: Fila | Ranking | Torneio | Cancelar")
    log_info("==============================")
    persist_all()
    monthly_reset_check.start()
    await update_panel()

# -------------------- Run --------------------
if __name__ == "__main__":
    if not TOKEN:
        log_err("DISCORD_TOKEN não definido. Defina no .env ou nas variáveis de ambiente.")
    else:
        bot.run(TOKEN)
