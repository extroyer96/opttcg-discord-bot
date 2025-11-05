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

# ---------------- EMOJIS / CONSTANTES ----------------
EMOJI_CHECK = "✅"
EMOJI_X = "❌"
EMOJI_TROPHY = "🏆"
EMOJI_YES = "➡️"
EMOJI_NO = "❌"

# helper: safe fetch user
async def safe_fetch_user(uid):
    try:
        return await bot.fetch_user(uid)
    except Exception:
        return None

# ---------------- PAINEL (painel compacto: fila, partidas, ultimas 3, inscritos) ----------------
async def atualizar_painel():
    global PANEL_MESSAGE_ID
    channel = bot.get_channel(PANEL_CHANNEL_ID)
    if not channel:
        # channel not found or bot not in guild
        print("Painel: canal não encontrado (PANEL_CHANNEL_ID).")
        return

    fila_txt = "\n".join([f"<@{u}>" for u in fila]) if fila else "Vazia"
    partidas_txt = "\n".join([f"<@{v['player1']}> vs <@{v['player2']}>" for _, v in partidas_ativas.items()]) or "Nenhuma"
    ultimas_txt = "\n".join([f"<@{h['winner']}> venceu <@{h['loser']}>" for h in historico[-3:]]) or "Nenhuma"
    inscritos_txt = "\n".join([f"<@{u}>" for u in torneio_data.get("players", [])]) if mostrar_inscritos else "Oculto"

    content = (
        f"🎮 **PAINEL - OPTTCG** 🎮\n\n"
        f"**Fila 1x1:**\n{fila_txt}\n\n"
        f"**Partidas em andamento:**\n{partidas_txt}\n\n"
        f"**Últimas 3 partidas:**\n{ultimas_txt}\n\n"
        f"**Inscritos Torneio:**\n{inscritos_txt}"
    )

    try:
        if PANEL_MESSAGE_ID == 0:
            msg = await channel.send(content)
            PANEL_MESSAGE_ID = msg.id
            # add reactions for queue (user clicks once, bots removes reaction)
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
        print("Erro ao atualizar painel:", e)

# ---------------- SALVAMENTO PERIÓDICO E RESET MENSAL ----------------
@tasks.loop(minutes=5)
async def task_save_states():
    save_json(RANKING_FILE, ranking)
    save_json(TORNEIO_FILE, torneio_data)
    save_json(HISTORICO_FILE, historico)
    # debug log
    # print("Estados salvos.")

@tasks.loop(hours=24)
async def daily_reset_check():
    # runs once per day and checks if it's day 1 of a month for monthly ranking reset
    now = datetime.datetime.utcnow()
    if now.day == 1:
        # reset ranking_1x1 and update timestamp
        ranking["scores_1x1"] = {}
        ranking["__last_reset"] = now.isoformat()
        save_json(RANKING_FILE, ranking)
        # notify owner
        try:
            owner = await safe_fetch_user(BOT_OWNER)
            if owner:
                await owner.send("🔄 Rankings 1x1 foram resetados automaticamente (dia 1 do mês).")
        except Exception:
            pass

# ---------------- FILA 1x1: emparelhamento e notificações ----------------
async def fila_worker():
    while True:
        try:
            if len(fila) >= 2:
                p1 = fila.pop(0)
                p2 = fila.pop(0)
                match_id = f"fila_{p1}_{p2}_{int(datetime.datetime.now().timestamp())}"
                partidas_ativas[match_id] = {
                    "player1": p1,
                    "player2": p2,
                    "attempts": {},  # user_id -> "vitoria"/"derrota"/"empate"
                    "cancel_attempts": {},
                    "source": "fila",
                    "timestamp": datetime.datetime.utcnow().isoformat()
                }
                # DM both players
                for uid in (p1, p2):
                    u = await safe_fetch_user(uid)
                    if u:
                        try:
                            await u.send(
                                f"⚔️ **Partida encontrada!**\n"
                                f"{'<@'+str(p1)+'>'} vs {'<@'+str(p2)+'>'}\n\n"
                                f"Para reportar resultado, responda com comando:\n"
                                f"`!reportar {match_id} vitoria` (se você venceu)\n"
                                f"`!reportar {match_id} derrota` (se você perdeu)\n\n"
                                f"Se deseja cancelar a partida, use: `!cancelarpartida {match_id}` (pedido precisa ser confirmado pelo adversário)."
                            )
                        except Exception:
                            pass
                await atualizar_painel()
        except Exception as e:
            print("Erro no worker da fila:", e)
        await asyncio.sleep(3)

# ---------------- TORNEIO: emparelhamento suíço simples ----------------
def calcular_rodadas(num_jogadores):
    # decrease one round from classic formula as requested earlier (user asked to reduce pattern by 1 round)
    # We'll compute ceil(log2(n)) then subtract 1 but min 1.
    base = math.ceil(math.log2(max(1, num_jogadores)))
    rounds = max(1, base - 1) if num_jogadores > 1 else 1
    return rounds

def swiss_sort(players, scores):
    # returns players sorted by score desc, then by id
    return sorted(players, key=lambda u: (-scores.get(str(u), 0), u))

async def gerar_pairings_torneio():
    players = list(torneio_data["players"])
    if not players:
        torneio_data["pairings"] = {}
        return
    scores = torneio_data.get("scores", {})
    sorted_players = swiss_sort(players, scores)
    pairings = {}
    used = set()
    # naive swiss: pair sequentially after sorting; for more advanced swiss, you'd implement bracket that avoids repeat opponents
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
            "source": "torneio",
            "round": torneio_data.get("round", 1)
        }
        i += 2
    # odd player -> bye
    if len(sorted_players) % 2 == 1:
        bye = sorted_players[-1]
        if bye not in torneio_data["byes"]:
            torneio_data["byes"].append(bye)
            torneio_data["scores"][str(bye)] = torneio_data["scores"].get(str(bye), 0) + 1
    torneio_data["pairings"] = pairings

# DM each player their pairings for current round
async def dm_pairings_round():
    for pid, pairing in torneio_data.get("pairings", {}).items():
        p1 = pairing["player1"]
        p2 = pairing["player2"]
        for uid in (p1, p2):
            u = await safe_fetch_user(uid)
            if u:
                try:
                    await u.send(
                        f"🏁 **Rodada {torneio_data['round']} — Confronto**\n"
                        f"<@{p1}> vs <@{p2}>\n\n"
                        f"Após a partida, reporte o resultado com:\n"
                        f"`!reportar {pid} vitoria` (se você venceu)\n"
                        f"`!reportar {pid} derrota` (se você perdeu)\n"
                        f"`!cancelarpartida {pid}` para solicitar cancelamento (deve ser confirmado pelo adversário)."
                    )
                except Exception:
                    pass

# ---------------- MENSAGENS E DM: receber decklists e outras respostas ----------------
@bot.event
async def on_message(message):
    # Do not respond to bots
    if message.author.bot:
        return

    # capture decklist in DM when inscriptions open or tournament active (we ask for decklists when tournament starts)
    # We'll accept decklists in DM at any time if the player is registered in torneio_data["players"]
    if isinstance(message.channel, discord.DMChannel):
        uid = message.author.id
        # Save decklist if user is participant and decklist not yet set
        if uid in torneio_data.get("players", []) and str(uid) not in torneio_data.get("decklists", {}):
            # Save decklist text
            torneio_data["decklists"][str(uid)] = message.content
            # Save to .txt file
            deckfile = os.path.join(DECKLIST_PATH, f"{uid}.txt")
            try:
                with open(deckfile, "w", encoding="utf-8") as f:
                    f.write(message.content)
            except Exception:
                pass
            # notify user
            try:
                await message.author.send("✅ Decklist recebida e armazenada. Obrigado!")
            except:
                pass
            save_json(TORNEIO_FILE, torneio_data)

            # if all decklists received, generate aggregated txt and send to owner
            all_players = set(map(str, torneio_data.get("players", [])))
            received = set(torneio_data["decklists"].keys())
            if all_players.issubset(received) and len(received) > 0:
                # build combined text
                combined = []
                for pid in torneio_data["players"]:
                    s = torneio_data["decklists"].get(str(pid), "")
                    combined.append(f"Player: {pid}\nDiscord: <@{pid}>\nDecklist:\n{s}\n\n---\n\n")
                combined_text = "".join(combined)
                # save combined file
                combined_path = os.path.join(DECKLIST_PATH, f"decklists_tournament_{int(datetime.datetime.utcnow().timestamp())}.txt")
                try:
                    with open(combined_path, "w", encoding="utf-8") as f:
                        f.write(combined_text)
                except Exception:
                    pass
                # DM owner with file
                owner = await safe_fetch_user(BOT_OWNER)
                if owner:
                    try:
                        with open(combined_path, "rb") as fh:
                            await owner.send("📦 Todas as decklists recebidas — segue arquivo:", file=discord.File(fh, os.path.basename(combined_path)))
                    except Exception:
                        try:
                            await owner.send("Todas as decklists recebidas — (falha ao enviar arquivo).")
                        except:
                            pass
            # done processing DM decklist
            return

    # allow commands to be processed as usual
    await bot.process_commands(message)

# ---------------- COMANDOS: fila, painel, torneio, inscrições ----------------
@bot.command(name="novopainel")
async def cmd_novopainel(ctx):
    if ctx.author.id != BOT_OWNER:
        await ctx.send("❌ Apenas o dono do bot pode usar este comando.")
        return
    global PANEL_MESSAGE_ID
    PANEL_MESSAGE_ID = 0
    await atualizar_painel()
    await ctx.send("✅ Painel reiniciado (nova mensagem).")

@bot.command(name="fila")
async def cmd_mostrarpainel_fila(ctx):
    msg = await ctx.send("Reaja nesta mensagem com ✅ para entrar na fila 1x1 e ❌ para sair.")
    try:
        await msg.add_reaction(EMOJI_CHECK)
        await msg.add_reaction(EMOJI_X)
    except Exception:
        pass

@bot.command(name="torneio")
async def cmd_torneio_open(ctx):
    if ctx.author.id != BOT_OWNER:
        await ctx.send("❌ Apenas o dono do bot pode abrir inscrições.")
        return
    torneio_data["inscriptions_open"] = True
    torneio_data["players"] = []
    torneio_data["decklists"] = {}
    torneio_data["inscription_message_id"] = 0
    msg = await ctx.send(
        "🏆 **TORNEIO ABERTO — INSCRIÇÕES** 🏆\n"
        "Reaja com 🏆 para se inscrever. Você receberá uma DM de confirmação."
    )
    try:
        await msg.add_reaction(EMOJI_TROPHY)
    except Exception:
        pass
    torneio_data["inscription_message_id"] = msg.id
    save_json(TORNEIO_FILE, torneio_data)
    await ctx.send("✅ Torneio aberto — mensagem de inscrição criada.")

@bot.command(name="fecharinscricoes")
async def cmd_fecharinscricoes(ctx):
    if ctx.author.id != BOT_OWNER:
        await ctx.send("❌ Apenas o dono do bot pode fechar inscrições.")
        return
    torneio_data["inscriptions_open"] = False
    save_json(TORNEIO_FILE, torneio_data)
    await ctx.send(f"🔒 Inscrições fechadas. Jogadores inscritos: {len(torneio_data.get('players', []))}")

@bot.command(name="começartorneio")
async def cmd_começar_torneio(ctx):
    if ctx.author.id != BOT_OWNER:
        await ctx.send("❌ Apenas o dono do bot pode iniciar o torneio.")
        return
    players = torneio_data.get("players", [])
    if len(players) < 2:
        await ctx.send("❌ Não há jogadores suficientes para iniciar o torneio (mínimo 2).")
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

    # DM each player asking for decklist if not present
    for uid in players:
        if str(uid) not in torneio_data.get("decklists", {}):
            u = await safe_fetch_user(uid)
            if u:
                try:
                    await u.send(
                        "✏️ **Solicitação de Decklist**\n"
                        "Por favor envie aqui (nesta DM) sua decklist copiada do simulador.\n"
                        "Use a função do simulador: 'Copy Deck List to Clipboard' e cole aqui."
                    )
                except Exception:
                    pass

    # DM pairings for round 1
    await dm_pairings_round()
    await ctx.send(f"🏁 Torneio iniciado com {len(players)} jogadores — rodadas: {torneio_data['rounds_target']}.")
    await atualizar_painel()

@bot.command(name="statustorneio")
async def cmd_statustorneio(ctx):
    if not torneio_data.get("active"):
        await ctx.send("❌ Nenhum torneio ativo.")
        return
    txt = f"🏆 **RODADA {torneio_data['round']}/{torneio_data['rounds_target']}** 🏆\n\n**Confrontos:**\n"
    for pid, p in torneio_data.get("pairings", {}).items():
        res = p.get("result") or "Pendente"
        txt += f"{pid}: <@{p['player1']}> vs <@{p['player2']}> — {res}\n"
    if torneio_data.get("byes"):
        txt += "\n**Byes:** " + ", ".join([f"<@{u}>" for u in torneio_data["byes"]]) + "\n"
    await ctx.send(txt)

# ---------------- REPORTAR RESULTADOS (fila & torneio) com confirmação mútua ----------------
@bot.command(name="reportar")
async def cmd_reportar(ctx, match_id: str, resultado: str):
    resultado = resultado.lower()
    # valid results: 'vitoria', 'derrota', 'empate'
    if resultado not in ("vitoria", "derrota", "empate"):
        await ctx.send("⚠️ Resultado inválido. Use: vitoria / derrota / empate")
        return

    partida = partidas_ativas.get(match_id) or torneio_data.get("pairings", {}).get(match_id)
    if not partida:
        await ctx.send("❌ Partida não encontrada (match_id inválido).")
        return

    uid = ctx.author.id
    if uid not in (partida.get("player1"), partida.get("player2")):
        await ctx.send("❌ Você não está nesta partida.")
        return

    # register attempt
    if "attempts" not in partida:
        partida["attempts"] = {}
    partida["attempts"][str(uid)] = resultado
    # Save in the correct place (if tournament pairing, update torneio_data; if fila, update partidas_ativas)
    if match_id in partidas_ativas:
        partidas_ativas[match_id] = partida
    else:
        torneio_data["pairings"][match_id] = partida
        save_json(TORNEIO_FILE, torneio_data)

    # if opponent already reported, check agreement
    opponent = partida["player2"] if uid == partida["player1"] else partida["player1"]
    opp_res = partida["attempts"].get(str(opponent))
    if opp_res:
        # both reported
        my_res = partida["attempts"][str(uid)]
        if my_res == opp_res:
            # agreement: compute winner
            if my_res == "vitoria":
                winner = uid
                loser = opponent
            elif my_res == "derrota":
                winner = opponent
                loser = uid
            else:  # empate
                winner = None
                loser = None

            # record result
            timestamp = datetime.datetime.utcnow().isoformat()
            if winner:
                # update historico and ranking (1x1 or tournament scoreboard)
                historico.append({"winner": winner, "loser": loser, "timestamp": timestamp, "match_id": match_id, "source": partida.get("source", "fila")})
                # update ranking 1x1 if from fila, else tournament score if tournament
                if partida.get("source") == "fila":
                    ranking["scores_1x1"][str(winner)] = ranking["scores_1x1"].get(str(winner), 0) + 1
                else:
                    torneio_data["scores"][str(winner)] = torneio_data["scores"].get(str(winner), 0) + 1
                # save
            else:
                # tie — store in historico as tie (no ranking points)
                historico.append({"winner": None, "loser": None, "timestamp": timestamp, "match_id": match_id, "source": partida.get("source", "fila"), "tie": True})

            # remove active match
            if match_id in partidas_ativas:
                partidas_ativas.pop(match_id, None)
            if match_id in torneio_data.get("pairings", {}):
                torneio_data["pairings"].pop(match_id, None)

            save_json(RANKING_FILE, ranking)
            save_json(HISTORICO_FILE, historico)
            save_json(TORNEIO_FILE, torneio_data)

            # Notify both players result confirmed
            p1_user = await safe_fetch_user(partida["player1"])
            p2_user = await safe_fetch_user(partida["player2"])
            notify_text = ""
            if winner:
                notify_text = f"✅ Resultado confirmado: <@{winner}> venceu <@{loser}> (match {match_id})"
            else:
                notify_text = f"🔷 Resultado confirmado: Empate registrado para match {match_id}"
            for u in (p1_user, p2_user):
                if u:
                    try:
                        await u.send(notify_text)
                    except:
                        pass
            await atualizar_painel()
            await ctx.send("✅ Resultado confirmado (ambos concordaram).")
        else:
            # disagreement
            p1_user = await safe_fetch_user(partida["player1"])
            p2_user = await safe_fetch_user(partida["player2"])
            for u in (p1_user, p2_user):
                if u:
                    try:
                        await u.send("⚠️ Detectamos relatórios divergentes para sua partida. Conversem e reenvie o mesmo resultado.")
                    except:
                        pass
            await ctx.send("⚠️ Relatórios divergentes. Ambos players precisam reportar o mesmo resultado.")
    else:
        # waiting for opponent
        await ctx.send("✅ Seu resultado foi registrado. Aguardando confirmação do adversário.")

# ---------------- CANCELAMENTO DE PARTIDA (pedido + confirmação) ----------------
@bot.command(name="cancelarpartida")
async def cmd_cancelar_partida(ctx, match_id: str):
    partida = partidas_ativas.get(match_id) or torneio_data.get("pairings", {}).get(match_id)
    if not partida:
        await ctx.send("❌ Partida não encontrada.")
        return

    uid = ctx.author.id
    if uid not in (partida.get("player1"), partida.get("player2")):
        await ctx.send("❌ Você não participa desta partida.")
        return

    # First ask for confirmation (to avoid accidental presses)
    confirm_msg = await ctx.send(f"⚠️ Tem certeza que deseja solicitar cancelamento de {match_id}? Reaja com {EMOJI_YES} para confirmar ou {EMOJI_NO} para cancelar.")
    try:
        await confirm_msg.add_reaction(EMOJI_YES)
        await confirm_msg.add_reaction(EMOJI_NO)
    except:
        pass

    def check(reaction, user):
        return user.id == uid and reaction.message.id == confirm_msg.id and str(reaction.emoji) in (EMOJI_YES, EMOJI_NO)

    try:
        reaction, user = await bot.wait_for("reaction_add", check=check, timeout=30)
        if str(reaction.emoji) == EMOJI_NO:
            await ctx.send("✋ Cancelamento abortado.")
            return
    except asyncio.TimeoutError:
        await ctx.send("⌛ Tempo esgotado. Pedido de cancelamento cancelado.")
        return

    # register cancel attempt
    if "cancel_attempts" not in partida:
        partida["cancel_attempts"] = {}
    partida["cancel_attempts"][str(uid)] = True

    opponent = partida["player2"] if uid == partida["player1"] else partida["player1"]
    # ask opponent to confirm cancel
    op_user = await safe_fetch_user(opponent)
    if op_user:
        try:
            msg = await op_user.send(f"⚠️ O jogador <@{uid}> solicitou cancelar a partida {match_id}. Reaja com {EMOJI_YES} para confirmar o cancelamento, ou {EMOJI_NO} para negar.")
            try:
                await msg.add_reaction(EMOJI_YES)
                await msg.add_reaction(EMOJI_NO)
            except:
                pass
        except:
            # cannot DM opponent — fallback: inform in channel (if possible)
            pass

    # now wait for opponent's reaction for a limited time
    def check_op(reaction, user):
        return user.id == opponent and str(reaction.emoji) in (EMOJI_YES, EMOJI_NO)

    try:
        reaction, user = await bot.wait_for("reaction_add", check=check_op, timeout=60)
        if str(reaction.emoji) == EMOJI_YES:
            # cancel match
            if match_id in partidas_ativas:
                partidas_ativas.pop(match_id, None)
            if match_id in torneio_data.get("pairings", {}):
                torneio_data["pairings"].pop(match_id, None)
            save_json(TORNEIO_FILE, torneio_data)
            await ctx.send("✅ Partida cancelada por acordo de ambos os jogadores.")
            # notify both
            p1u = await safe_fetch_user(partida["player1"])
            p2u = await safe_fetch_user(partida["player2"])
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
        await ctx.send("⌛ Tempo esgotado aguardando resposta do adversário. Pedido de cancelamento expirou.")

# ---------------- ABANDONO DE TORNEIO (!ff) ----------------
@bot.command(name="ff")
async def cmd_ff(ctx):
    uid = ctx.author.id
    if uid not in torneio_data.get("players", []):
        await ctx.send("❌ Você não está inscrito neste torneio.")
        return
    # remove player from players list (they abandon)
    torneio_data["players"].remove(uid)
    # award bye to scheduled opponents in this round: for any pairing where this player is present, give opponent point
    for pid, p in list(torneio_data.get("pairings", {}).items()):
        if p["player1"] == uid or p["player2"] == uid:
            other = p["player2"] if p["player1"] == uid else p["player1"]
            torneio_data["scores"][str(other)] = torneio_data["scores"].get(str(other), 0) + 1
            p["result"] = f"Vitória por abandono — <@{other}>"
            # remove pairing so it doesn't block next round
            torneio_data["pairings"].pop(pid, None)
    # mark bye record for this player
    if uid not in torneio_data["byes"]:
        torneio_data["byes"].append(uid)
    save_json(TORNEIO_FILE, torneio_data)
    await ctx.send("⚠️ Você abandonou o torneio. Seus próximos adversários receberam pontos (bye).")
    await atualizar_painel()

# ---------------- PROXIMA RODADA (admin) ----------------
@bot.command(name="proximarodada")
async def cmd_proxima_rodada(ctx):
    if ctx.author.id != BOT_OWNER:
        await ctx.send("❌ Apenas o dono do bot pode avançar as rodadas.")
        return
    if not torneio_data.get("active"):
        await ctx.send("❌ Nenhum torneio ativo.")
        return
    # check if current round >= rounds_target => finish tournament
    if torneio_data.get("round", 0) >= torneio_data.get("rounds_target", 0):
        # finish tournament
        torneio_data["active"] = False
        torneio_data["finished"] = True
        # determine champion (highest score)
        scores = torneio_data.get("scores", {})
        if scores:
            champion_id, champ_score = max(scores.items(), key=lambda kv: kv[1])
            # update tournament champions ranking
            torneio_data["tournament_champions"] = torneio_data.get("tournament_champions", {})
            torneio_data["tournament_champions"][str(champion_id)] = torneio_data["tournament_champions"].get(str(champion_id), 0) + 1
            ranking["scores_torneio"][str(champion_id)] = ranking["scores_torneio"].get(str(champion_id), 0) + 1
            # notify owner and channel
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
    # clear byes for new round? keep record but reset list for next pairing generation
    torneio_data["byes"] = []
    # generate new pairings
    await gerar_pairings_torneio()
    save_json(TORNEIO_FILE, torneio_data)
    # DM new pairings to players
    await dm_pairings_round()
    await ctx.send(f"➡️ Avançado para rodada {torneio_data['round']} — pairings enviados por DM.")
    await atualizar_painel()

# ---------------- RESET DO TORNEIO (admin) ----------------
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
        "results": {},
        "scores": {},
        "played": {},
        "byes": [],
        "finished": False,
        "inscription_message_id": 0
    })
    save_json(TORNEIO_FILE, torneio_data)
    await ctx.send("✅ Torneio resetado (sem registrar campeão).")
    await atualizar_painel()

# ---------------- RESET MANUAL DE RANKINGS (admin) ----------------
@bot.command(name="resetranking")
async def cmd_reset_ranking(ctx, scope: str = "1x1"):
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

# ---------------- VER RANKING (DM) ----------------
@bot.command(name="verranking")
async def cmd_ver_ranking(ctx):
    # send DM with ranking and ask if wants tournament ranking too with reactions
    user = ctx.author
    try:
        # 1x1 ranking top 10
        s_1x1 = sorted(ranking.get("scores_1x1", {}).items(), key=lambda kv: kv[1], reverse=True)
        lines = ["🏅 **Ranking 1x1** 🏅\n"]
        for i, (uid, pts) in enumerate(s_1x1[:20], 1):
            lines.append(f"{i}. <@{uid}> — {pts} vitórias")
        if not s_1x1:
            lines.append("Nenhuma partida registrada ainda.")
        dm = await user.send("\n".join(lines))
        # ask if wants tournament ranking in same DM using two reactions (➡️ = yes, ❌ = no)
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
        await ctx.send("❌ Erro ao enviar ranking via DM (verifique se DMs estão abertas).")
        print("Erro ver ranking DM:", e)

# ---------------- COMANDO AJUDA ----------------
@bot.command(name="ajuda")
async def cmd_ajuda(ctx):
    help_text = (
        "🎮 **Comandos Bot OPTTCG** 🎮\n\n"
        "**Geral / Jogadores**\n"
        "`!mostrerfila` — Mostra mensagem para entrar/sair na fila 1x1\n"
        "`Reaja no painel com ✅ para entrar / ❌ para sair` — Entrar/saír da fila\n"
        "`!reportar <match_id> <vitoria/derrota/empate>` — Reportar resultado (confirmação mútua exigida)\n"
        "`!cancelarpartida <match_id>` — Solicitar cancelamento (precisa de confirmação do adversário)\n"
        "`!verranking` — Recebe ranking 1x1 via DM (pergunta se quer ranking de torneios)\n\n"
        "**Torneio (Admin)**\n"
        "`!torneio` — Abre inscrições (admin). Jogadores reagem com 🏆 para entrar\n"
        "`!fecharinscricoes` — Fecha inscrições\n"
        "`!começartorneio` — Inicia o torneio (admin)\n"
        "`!statustorneio` — Mostra confrontos atuais\n"
        "`!proximarodada` — Avança para a próxima rodada (admin)\n"
        "`!resetartorneio` — Reseta o torneio (admin)\n"
        "`!ff` — Abandonar o torneio (player)\n"
        "`!resetranking <1x1|torneio>` — Reset manual de rankings (admin)\n\n"
        "Mais: painel automático, decklist por DM (após inscrição você recebe pedido), "
        "e armazenamento de histórico/arquivos em `data/`."
    )
    await ctx.send(help_text)

# ---------------- EVENTO: reactions em painel e inscrição ----------------
@bot.event
async def on_reaction_add(reaction, user):
    # ignore bots
    if user.bot:
        return

    # Panel queue reactions
    try:
        if reaction.message.id == PANEL_MESSAGE_ID:
            if str(reaction.emoji) == EMOJI_CHECK:
                if user.id not in fila:
                    fila.append(user.id)
                    try:
                        await user.send("✅ Você entrou na fila 1x1. Aguarde emparelhamento.")
                    except:
                        pass
                    await atualizar_painel()
                # remove user's reaction to allow re-click later
                try:
                    await reaction.remove(user)
                except:
                    pass
            elif str(reaction.emoji) == EMOJI_X:
                if user.id in fila:
                    fila.remove(user.id)
                    try:
                        await user.send("❌ Você saiu da fila 1x1.")
                    except:
                        pass
                    await atualizar_painel()
                try:
                    await reaction.remove(user)
                except:
                    pass
    except Exception:
        pass

    # Tournament inscription message reaction
    try:
        if torneio_data.get("inscription_message_id") and reaction.message.id == torneio_data.get("inscription_message_id"):
            if str(reaction.emoji) == EMOJI_TROPHY and torneio_data.get("inscriptions_open"):
                if user.id not in torneio_data.get("players", []):
                    torneio_data["players"].append(user.id)
                    save_json(TORNEIO_FILE, torneio_data)
                    try:
                        await user.send("✅ Você foi inscrito no torneio. Aguarde instruções por DM.")
                    except:
                        pass
                    await atualizar_painel()
                try:
                    await reaction.remove(user)
                except:
                    pass
    except Exception:
        pass

# ---------------- INICIALIZAÇÃO ----------------
@bot.event
async def on_ready():
    print(f"[{datetime.datetime.utcnow().isoformat()}] Bot conectado como {bot.user} (id: {bot.user.id})")
    # start background tasks
    if not task_save_states.is_running():
        task_save_states.start()
    if not daily_reset_check.is_running():
        daily_reset_check.start()
    # start fila worker
    bot.loop.create_task(fila_worker())
    await atualizar_painel()

# ---------------- RUNTIME: start bot ----------------
from aiohttp import web

async def webserver():
    app = web.Application()
    app.add_routes([web.get("/", lambda request: web.Response(text="Bot is running!"))])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 10000)))
    await site.start()

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("❌ DISCORD_TOKEN não definido nas variáveis de ambiente. Configure e reinicie.")
    else:
        try:
            bot.loop.create_task(webserver())
            bot.run(DISCORD_TOKEN)
        except Exception as e:
            print("❌ Erro ao iniciar o bot:", e)
