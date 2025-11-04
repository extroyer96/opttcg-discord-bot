# bot.py
"""
OPTCG Discord Bot - single-file ready for Replit/Render
Color logs via colorama.
"""

import os, json, random, asyncio, math
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
import pytz
import discord
from discord.ext import tasks, commands
from colorama import init as colorama_init, Fore, Style

colorama_init(autoreset=True)

EMOJI_JOIN = "🟢"
EMOJI_LEAVE = "🔴"
EMOJI_RANK = "🏆"
EMOJI_TOURN = "🏅"

PREFIX_LOG = "[OPTCG]"

def lg_info(msg): print(f"{Fore.GREEN}🟢 {PREFIX_LOG}[INFO] {msg}{Style.RESET_ALL}")
def lg_warn(msg): print(f"{Fore.YELLOW}⚠️ {PREFIX_LOG}[WARN] {msg}{Style.RESET_ALL}")
def lg_err(msg):  print(f"{Fore.RED}⛔ {PREFIX_LOG}[ERROR] {msg}{Style.RESET_ALL}")
def lg_tourn(msg):print(f"{Fore.MAGENTA}🏆 {PREFIX_LOG}[TOURNEY] {msg}{Style.RESET_ALL}")

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
PANEL_CHANNEL_ID = int(os.getenv("PANEL_CHANNEL_ID","0"))
PANEL_MESSAGE_ID = int(os.getenv("PANEL_MESSAGE_ID","0"))
BOT_OWNER = int(os.getenv("BOT_OWNER","0"))

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
RANKING_FILE = DATA_DIR / "ranking.json"
HISTORY_FILE = DATA_DIR / "historico.json"
TOURNEY_FILE = DATA_DIR / "torneios.json"

def load_json(p, default):
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            lg_err(f"Falha lendo {p}: {e}")
            return default
    else:
        save_json(p, default)
        return default

def save_json(p, obj):
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")

ranking = load_json(RANKING_FILE, {"__queue": [], "__last_reset": ""})
history = load_json(HISTORY_FILE, [])
tourney = load_json(TOURNEY_FILE, {"active":False,"players":[],"decklists":{},"round":0,"pairings":{},"results":{},"scores":{},"played":{},"byes":[],"finished":False,"rounds_target":None})

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.members = True
intents.dm_messages = True

bot = commands.Bot(command_prefix="!", intents=intents)

def persist_all():
    save_json(RANKING_FILE, ranking)
    save_json(HISTORY_FILE, history)
    save_json(TOURNEY_FILE, tourney)

def make_match_id(prefix="m"):
    return f"{prefix}-{int(datetime.now(timezone.utc).timestamp()*1000)}"

def compute_rounds(n):
    if n <= 8: return 3
    if n <= 16: return 4
    if n <= 32: return 5
    return 6

async def update_panel():
    if PANEL_CHANNEL_ID == 0: return
    ch = bot.get_channel(PANEL_CHANNEL_ID)
    if not ch: return
    embed = discord.Embed(title="OPTCG Matchmaking & Torneios", color=discord.Color.blue())
    q = ranking.get("__queue", [])
    qtext = "\\n".join([f"{i+1}. <@{u}>" for i,u in enumerate(q)]) if q else "_Fila vazia_"
    embed.add_field(name="🎮 Fila (reaja 🟢 / 🔴)", value=qtext, inline=False)
    embed.set_footer(text="Use reações para interagir — 🟢 entrar, 🔴 sair, 🏆 ranking")
    global PANEL_MESSAGE_ID
    try:
        if PANEL_MESSAGE_ID == 0:
            msg = await ch.send(embed=embed)
            try:
                await msg.add_reaction(EMOJI_JOIN); await msg.add_reaction(EMOJI_LEAVE); await msg.add_reaction(EMOJI_RANK)
            except: pass
            PANEL_MESSAGE_ID = msg.id
            lg_info(f"Painel criado (id {msg.id})")
        else:
            try:
                msg = await ch.fetch_message(PANEL_MESSAGE_ID)
                await msg.edit(embed=embed)
            except discord.NotFound:
                msg = await ch.send(embed=embed)
                try:
                    await msg.add_reaction(EMOJI_JOIN); await msg.add_reaction(EMOJI_LEAVE); await msg.add_reaction(EMOJI_RANK)
                except: pass
                PANEL_MESSAGE_ID = msg.id
    except Exception as e:
        lg_err(f"Erro update_panel: {e}")

def queue_list():
    return ranking.setdefault("__queue", [])

def queue_add(uid):
    q = queue_list()
    if uid not in q:
        q.append(uid); persist_all(); lg_info(f"{uid} entrou na fila ({len(q)})")
    return q.index(uid)+1 if uid in q else -1

def queue_remove(uid):
    q = queue_list()
    if uid in q:
        q.remove(uid); persist_all(); lg_info(f"{uid} saiu da fila ({len(q)})"); return True
    return False

async def try_matchmake():
    q = queue_list()
    while len(q) >= 2:
        p1 = q.pop(0); p2 = q.pop(0)
        ranking["__queue"] = q; persist_all()
        match_id = make_match_id("normal")
        try:
            u1 = await bot.fetch_user(p1); u2 = await bot.fetch_user(p2)
            await u1.send(f"🎯 Você foi pareado com {u2.mention} — match {match_id}. Responda nesta DM com p1/p2/draw quando terminar.")
            await u2.send(f"🎯 Você foi pareado com {u1.mention} — match {match_id}. Responda nesta DM com p1/p2/draw quando terminar.")
            lg_info(f"Match normal {match_id}: {p1} vs {p2}")
        except Exception as e:
            lg_warn(f"Falha ao notificar players: {e}")
    await update_panel()

pending = {}  # match_id -> {user_id: resp}

async def resolve_result_for(match_id, p1, p2, is_tourney=False):
    key = pending.get(match_id, {})
    if str(p1) in key and str(p2) in key:
        r1 = key[str(p1)]; r2 = key[str(p2)]
        if r1 == r2:
            winner=None; loser=None
            if r1=="p1": winner=p1; loser=p2
            elif r1=="p2": winner=p2; loser=p1
            ts = datetime.now(timezone.utc).isoformat()
            if is_tourney:
                tourney["results"][match_id] = {"winner": winner, "loser": loser, "ts": ts}
                if winner: tourney.setdefault("scores",{}).setdefault(str(winner),0); tourney["scores"][str(winner)]+=1
                else:
                    tourney.setdefault("scores",{}).setdefault(str(p1),0); tourney.setdefault("scores",{}).setdefault(str(p2),0)
                    tourney["scores"][str(p1)]+=0.5; tourney["scores"][str(p2)]+=0.5
                persist_all()
            else:
                history.append({"winner": f"<@{winner}>" if winner else "Draw", "loser": f"<@{loser}>" if loser else "Draw", "ts": ts})
                if winner:
                    ranking.setdefault(str(winner),0); ranking[str(winner)]+=1
                persist_all(); lg_info(f"Match {match_id} confirmado. Winner: {winner}")
            try:
                u1 = await bot.fetch_user(p1); u2 = await bot.fetch_user(p2)
                if winner:
                    await u1.send(f"✅ Resultado confirmado: <@{winner}> venceu (match {match_id}).")
                    await u2.send(f"✅ Resultado confirmado: <@{winner}> venceu (match {match_id}).")
                else:
                    await u1.send(f"✅ Resultado confirmado: Empate (match {match_id}).")
                    await u2.send(f"✅ Resultado confirmado: Empate (match {match_id}).")
            except: pass
            pending.pop(match_id, None)
            if is_tourney: await check_tourney_round_completion()
            else: await update_panel()
        else:
            try:
                u1 = await bot.fetch_user(p1); u2 = await bot.fetch_user(p2)
                await u1.send(\"⚠️ Resultado divergente. Conversem e reenviem.\"); await u2.send(\"⚠️ Resultado divergente. Conversem e reenviem.\")
            except: pass

def swiss_pairing(players, scores, played, byes):
    players_sorted = sorted(players, key=lambda u: (-scores.get(str(u),0), u))
    unpaired = players_sorted[:]
    pairings=[]
    bye=None
    if len(unpaired)%2==1:
        candidates = sorted(unpaired, key=lambda u: (scores.get(str(u),0), u))
        bye = None
        for c in candidates:
            if str(c) not in byes: bye=c; break
        if bye is None: bye=candidates[0]
        unpaired.remove(bye)
        pairings.append({\"id\": make_match_id(\"t\"), \"p1\": bye, \"p2\": None})
    while unpaired:
        a = unpaired.pop(0); b=None
        for i,c in enumerate(unpaired):
            if c not in played.get(str(a),[]): b=c; unpaired.pop(i); break
        if b is None: b = unpaired.pop(0)
        pairings.append({\"id\": make_match_id(\"t\"), \"p1\": a, \"p2\": b})
    return pairings

async def start_tourney_round():
    if not tourney.get(\"players\"): return
    tourney[\"round\"] += 1
    rnd = tourney[\"round\"]
    players = tourney[\"players\"]
    pairings = swiss_pairing(players, tourney.get(\"scores\",{}), tourney.get(\"played\",{}), tourney.get(\"byes\",[]))
    tourney.setdefault(\"pairings\", {})[str(rnd)] = pairings
    for p in pairings:
        if p[\"p2\"] is None:
            if str(p[\"p1\"]) not in tourney.get(\"byes\",[]): tourney.setdefault(\"scores\",{}).setdefault(str(p[\"p1\"]),0); tourney[\"scores\"][str(p[\"p1\"])] += 1; tourney.setdefault(\"byes\",[]).append(str(p[\"p1\"]))
            p[\"result\"] = {\"winner\": p[\"p1\"], \"loser\": None, \"ts\": datetime.now(timezone.utc).isoformat()}
        else:
            tourney.setdefault(\"results\", {})[p[\"id\"]] = {}
            p[\"result\"] = None
    persist_all()
    for p in pairings:
        if p[\"p2\"] is None: continue
        try:
            u1 = await bot.fetch_user(p[\"p1\"]); u2 = await bot.fetch_user(p[\"p2\"])
            await u1.send(f\"📢 Torneio Rodada {rnd}: você joga contra {u2.mention} (match {p['id']}). Responda com p1/p2/draw quando terminar.\")
            await u2.send(f\"📢 Torneio Rodada {rnd}: você joga contra {u1.mention} (match {p['id']}). Responda com p1/p2/draw quando terminar.\")
        except Exception as e:
            lg_warn(f\"Falha DM: {e}\")
    persist_all()

async def check_tourney_round_completion():
    rnd = tourney[\"round\"]
    pairings = tourney.get(\"pairings\", {}).get(str(rnd), [])
    for p in pairings:
        if p.get(\"p2\") is None: continue
        if not p.get(\"result\"): return False
    n = len(tourney.get(\"players\", []))
    target = tourney.get(\"rounds_target\") or compute_rounds(n)
    if tourney[\"round\"] >= target:
        scores = tourney.get(\"scores\", {})
        if not scores: return False
        top = max(scores.values())
        winners = [int(uid) for uid, sc in scores.items() if sc == top]
        champ = winners[0]
        tourney[\"finished\"] = True
        ranking.setdefault(str(champ),0); ranking[str(champ)] += 1
        persist_all()
        ch = bot.get_channel(PANEL_CHANNEL_ID)
        if ch: await ch.send(f\"🏆 Parabéns <@{champ}> — campeão do torneio! Obrigado a todos!\")
        return True
    else:
        await asyncio.sleep(2)
        await start_tourney_round()
        return True

@bot.event
async def on_ready():
    lg_info(f\"Bot online: {bot.user}\"); persist_all(); monthly_reset_task.start(); await update_panel()

@bot.event
async def on_raw_reaction_add(payload):
    if payload.user_id == bot.user.id: return
    emoji = str(payload.emoji)
    if payload.channel_id == PANEL_CHANNEL_ID and payload.message_id == PANEL_MESSAGE_ID:
        if emoji == EMOJI_JOIN:
            queue_add(payload.user_id); 
            try: await (await bot.fetch_user(payload.user_id)).send(\"Você entrou na fila!\") 
            except: pass
            await update_panel(); await try_matchmake()
        elif emoji == EMOJI_LEAVE:
            queue_remove(payload.user_id); await update_panel()
        elif emoji == EMOJI_RANK:
            items = sorted(((k,int(v)) for k,v in ranking.items() if k!='__queue'), key=lambda x:-x[1])
            text = \"\\n\".join([f\"{i+1}. <@{uid}> — {wins} vitórias\" for i,(uid,wins) in enumerate(items)])
            try: await (await bot.fetch_user(payload.user_id)).send(text if text else \"Nenhuma vitória\") 
            except: pass
    # tourney signup
    if tourney.get('active') and payload.channel_id == PANEL_CHANNEL_ID and payload.message_id == tourney.get('signup_msg_id') and emoji==EMOJI_TOURN:
        if payload.user_id not in tourney['players']:
            tourney['players'].append(payload.user_id); persist_all()
            try: await (await bot.fetch_user(payload.user_id)).send(\"Você se inscreveu! Agora cole sua decklist nesta DM.\"); lg_tourn(f\"Player {payload.user_id} inscrito\") 
            except: pass

@bot.event
async def on_raw_reaction_remove(payload):
    if tourney.get('active') and payload.channel_id == PANEL_CHANNEL_ID and payload.message_id == tourney.get('signup_msg_id') and str(payload.emoji)==EMOJI_TOURN:
        if payload.user_id in tourney['players']:
            tourney['players'].remove(payload.user_id); tourney['decklists'].pop(str(payload.user_id),None); persist_all(); lg_tourn(f\"Player {payload.user_id} retirado\"); await update_panel()

@bot.event
async def on_message(message):
    if message.author.bot: return
    if isinstance(message.channel, discord.DMChannel):
        if tourney.get('active') and message.author.id in tourney.get('players',[]) and not tourney['decklists'].get(str(message.author.id)):
            tourney['decklists'][str(message.author.id)] = message.content; tourney.setdefault('scores',{})[str(message.author.id)] = 0.0; tourney.setdefault('played',{})[str(message.author.id)] = []; persist_all(); await message.channel.send(\"Decklist recebida.\"); lg_tourn(f\"Decklist de {message.author.id}\"); return
        txt = message.content.strip().lower()
        if txt in (\"p1\",\"p2\",\"draw\",\"vitória\",\"vitoria\",\"derrota\",\"vitoria\"):
            norm = 'p1' if txt in (\"vitória\",\"vitoria\") else ('p2' if txt==\"derrota\" else txt)
            # simple resolution: find pending pairing containing user in current round
            rnd = tourney.get('round')
            if rnd and str(rnd) in tourney.get('pairings',{}):
                for p in tourney['pairings'][str(rnd)]:
                    if p.get('p2') and message.author.id in (p['p1'], p['p2']):
                        mid = p['id']
                        pending.setdefault(mid, {})[str(message.author.id)] = norm
                        await message.channel.send(\"Resultado registrado. Aguardando adversário.\")
                        await resolve_result_for(mid, p['p1'], p['p2'], is_tourney=True)
                        return
            await message.channel.send(\"Nenhuma partida ativa encontrada.\")
            return
    await bot.process_commands(message)

@bot.command(name='torneio')
async def cmd_torneio(ctx, action: str = None, *args):
    if ctx.author.id != BOT_OWNER:
        await ctx.send(\"Apenas o dono do bot pode usar este comando.\"); return
    act = (action or '').lower()
    if act == 'iniciar':
        tourney['active'] = True; tourney['signup_msg_id'] = None; tourney['players'] = []; persist_all()
        msg = await ctx.send(\"Torneio iniciado. Vou criar mensagem de inscrição.\"); signup_id = await create_tourney_signup(ctx.channel); tourney['signup_msg_id'] = signup_id; persist_all(); await ctx.send('Signup message created.')
    elif act == 'fechar':
        if not tourney.get('players'): await ctx.send('Nenhum inscrito.'); return
        tourney['rounds_target'] = tourney.get('rounds_target') or compute_rounds(len(tourney['players'])); persist_all(); await export_decklists_to_owner(ctx.author); await ctx.send(f\"Inscrições fechadas. Rodadas: {tourney['rounds_target']}\"); await start_tourney_round()
    elif act == 'rodadas':
        try: n = int(args[0]); tourney['rounds_target'] = n; persist_all(); await ctx.send(f\"Rodadas definidas para {n}\") 
        except: await ctx.send('Uso: !torneio rodadas <N>')
    elif act == 'encerrar':
        tourney['finished'] = True; persist_all(); await ctx.send('Torneio marcado como finalizado.')
    elif act == 'status':
        await ctx.send(f\"active={tourney['active']}, players={len(tourney.get('players',[]))}, round={tourney['round']}, finished={tourney['finished']}\")
    else:
        await ctx.send('Uso: !torneio iniciar|fechar|rodadas <N>|encerrar|status')

async def create_tourney_signup(channel):
    embed = discord.Embed(title='🏆 Torneio - Inscrições', description='Reaja com 🏅 para se inscrever. Você receberá DM para colar sua decklist.', color=discord.Color.gold())
    msg = await channel.send(embed=embed)
    try: await msg.add_reaction(EMOJI_TOURN)
    except: pass
    return msg.id

async def export_decklists_to_owner(owner):
    fn = f\"decklists_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt\"
    lines = []
    for uid in tourney.get('players', []):
        lines.append(f\"Jogador: <@{uid}> (ID: {uid})\\n\")
        lines.append(tourney.get('decklists', {}).get(str(uid), '(SEM DECKLIST)') + '\\n')
        lines.append('-'*40 + '\\n')
    with open(fn, 'w', encoding='utf-8') as f: f.write('\\n'.join(lines))
    try:
        await owner.send('Decklists do torneio:', file=discord.File(fn)); lg_tourn(f\"Decklists enviadas ao dono ({owner.id})\")
    except Exception as e:
        lg_warn(f\"Falha ao enviar decklists: {e}\")

@tasks.loop(hours=6)
async def monthly_reset_task():
    tz = pytz.timezone('America/Sao_Paulo'); now = datetime.now(tz)
    if now.day == 1:
        last = ranking.get('__last_reset')
        cur = now.strftime('%Y-%m-%d')
        if last != cur:
            ranking.clear(); ranking['__queue'] = []; ranking['__last_reset'] = cur; persist_all()
            ch = bot.get_channel(PANEL_CHANNEL_ID)
            if ch: await ch.send('🔄 Ranking mensal resetado automaticamente.')
            lg_info('Ranking resetado.')

if __name__ == '__main__':
    if not TOKEN:
        lg_err('DISCORD_TOKEN não definido. Defina no .env ou nas variáveis de ambiente.')
    else:
        bot.run(TOKEN)
