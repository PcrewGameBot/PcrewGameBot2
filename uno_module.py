"""PCREW UNO - group-only controls using Telegram inline mode.
Hands are never sent in private chat. Inline query results are visible only to
that querying player; selecting a card creates a confirmation message in the group.
"""
import json, random, time, asyncio, secrets
from telegram import (InlineKeyboardButton, InlineKeyboardMarkup, Update,
                      InlineQueryResultArticle, InputTextMessageContent)
from telegram.ext import (ContextTypes, CommandHandler, CallbackQueryHandler,
                          InlineQueryHandler)

UNO_STICKER_SET = "unocards"
UNO_LOCKS = {}
COLORS = ("red", "blue", "green", "yellow")
COLOR_EMOJI = {"red":"🔴", "blue":"🔵", "green":"🟢", "yellow":"🟡"}

def _uno_db(q):
    q("CREATE TABLE IF NOT EXISTS uno_games(chat_id BIGINT PRIMARY KEY, state TEXT NOT NULL, updated BIGINT NOT NULL)")
    q("CREATE TABLE IF NOT EXISTS uno_stats(user_id BIGINT PRIMARY KEY, wins BIGINT DEFAULT 0, games BIGINT DEFAULT 0)")

def _load(q, cid):
    rows=q("SELECT state FROM uno_games WHERE chat_id=?", (cid,), True)
    return json.loads(rows[0][0]) if rows else None

def _save(q, cid, state):
    if state is None:
        q("DELETE FROM uno_games WHERE chat_id=?", (cid,)); return
    q("INSERT INTO uno_games(chat_id,state,updated) VALUES(?,?,?) ON CONFLICT(chat_id) DO UPDATE SET state=excluded.state,updated=excluded.updated", (cid,json.dumps(state),int(time.time())))

def _deck():
    d=[]
    for c in COLORS:
        d.append(f"{c}:0")
        for n in range(1,10): d += [f"{c}:{n}",f"{c}:{n}"]
        for a in ("skip","reverse","draw2"): d += [f"{c}:{a}",f"{c}:{a}"]
    for _ in range(4): d += ["wild:wild","wild:draw4"]
    random.shuffle(d); return d

def _label(card):
    c,v=card.split(":",1)
    if c=="wild": return "🌈 Wild" if v=="wild" else "🌈 Wild +4"
    names={"skip":"Skip","reverse":"Reverse","draw2":"Draw Two +2"}
    return f"{COLOR_EMOJI[c]} {names.get(v,v)}"

def _valid(card, top, chosen=None):
    c,v=card.split(":",1); tc,tv=top.split(":",1)
    return c=="wild" or c==tc or v==tv or (chosen is not None and c==chosen)

def _draw(st, uid, count=1):
    got=[]
    for _ in range(count):
        if not st["deck"]:
            if len(st["discard"])>1:
                top=st["discard"].pop(); st["deck"]=st["discard"]; st["discard"]=[top]; random.shuffle(st["deck"])
            else: break
        if st["deck"]:
            card=st["deck"].pop(); st["hands"][str(uid)].append(card); got.append(card)
    return got

def _next(st, steps=1): st["turn"]=(st["turn"]+st["direction"]*steps)%len(st["players"])

def _player(st, uid): return next((p for p in st["players"] if p["id"]==uid),None)

async def _announce(ctx,cid,st,extra=""):
    p=st["players"][st["turn"]]
    lines=["🎴 <b>PCREW UNO</b>"]
    if extra: lines.append(extra)
    lines += [f"Top card: <b>{_label(st['discard'][-1])}</b>",
              f"Turn: <a href=\"tg://user?id={p['id']}\">{p['name']}</a>",
              "Players: "+" · ".join(f"{x['name']} ({len(st['hands'][str(x['id'])])})" for x in st['players'])]
    kb=InlineKeyboardMarkup([
        [InlineKeyboardButton("🎴 CHOOSE CARD", switch_inline_query_current_chat=f"uno {cid}"),
         InlineKeyboardButton("📥 DRAW CARD", callback_data=f"uno:draw:{cid}")],
        [InlineKeyboardButton("📣 UNO!", callback_data=f"uno:call:{cid}")]
    ])
    if st.get("message_id"):
        try:
            await ctx.bot.edit_message_text("\n".join(lines),chat_id=cid,message_id=st['message_id'],parse_mode="HTML",reply_markup=kb)
            return
        except Exception: pass
    m=await ctx.bot.send_message(cid,"\n".join(lines),parse_mode="HTML",reply_markup=kb)
    st["message_id"]=m.message_id

def _group(update): return bool(update.effective_chat and update.effective_chat.type in ("group","supergroup"))

async def uno_new(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    if not _group(update): return await update.effective_message.reply_text("Use /new in a group.")
    q=ctx.application.bot_data['uno_q']; cid=update.effective_chat.id; u=update.effective_user
    async with UNO_LOCKS.setdefault(cid,asyncio.Lock()):
        old=_load(q,cid)
        if old and old.get('status') in ('lobby','playing'): return await update.effective_message.reply_text("A game is already active. Use /stop first.")
        st={"status":"lobby","creator":u.id,"players":[{"id":u.id,"name":u.full_name}],"hands":{},"deck":[],"discard":[],"turn":0,"direction":1,"message_id":None,"last_card":{}}
        _save(q,cid,st)
    await update.effective_message.reply_text("🎴 <b>Created a new game!</b>\nJoin the game with /join and start the game with /start",parse_mode="HTML")

async def uno_join(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    if not _group(update): return await update.effective_message.reply_text("Use /join in the game group.")
    q=ctx.application.bot_data['uno_q']; cid=update.effective_chat.id; u=update.effective_user
    async with UNO_LOCKS.setdefault(cid,asyncio.Lock()):
        st=_load(q,cid)
        if not st or st['status']!='lobby': return await update.effective_message.reply_text("No open game. Create one with /new.")
        if _player(st,u.id): return await update.effective_message.reply_text("You already joined!")
        st['players'].append({"id":u.id,"name":u.full_name}); _save(q,cid,st)
        count=len(st['players'])
    await update.effective_message.reply_text(f"➕ {u.mention_html()} joined the game! ({count} players)",parse_mode="HTML")

async def uno_start(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    if not _group(update): return False
    q=ctx.application.bot_data['uno_q']; cid=update.effective_chat.id
    async with UNO_LOCKS.setdefault(cid,asyncio.Lock()):
        st=_load(q,cid)
        if not st or st['status']!='lobby': return False
        if len(st['players'])<2:
            await update.effective_message.reply_text("At least 2 players are needed. Use /join."); return True
        st['deck']=_deck(); st['hands']={str(p['id']):[st['deck'].pop() for _ in range(7)] for p in st['players']}
        first=st['deck'].pop()
        while first.startswith('wild:'): st['deck'].insert(0,first); first=st['deck'].pop()
        st['discard']=[first]; st['status']='playing'; st['turn']=0; st['direction']=1; _save(q,cid,st)
    await update.effective_message.reply_text("🚀 <b>Game started!</b> Use the <b>CHOOSE CARD</b> button in this group. No private chat is used.",parse_mode="HTML")
    await _announce(ctx,cid,st)
    return True

async def uno_leave(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    if not _group(update): return await update.effective_message.reply_text("Use /leave in the game group.")
    q=ctx.application.bot_data['uno_q']; cid=update.effective_chat.id; uid=update.effective_user.id
    async with UNO_LOCKS.setdefault(cid,asyncio.Lock()):
        st=_load(q,cid)
        if not st or st['status']!='lobby': return await update.effective_message.reply_text("You can only leave before the game starts.")
        st['players']=[p for p in st['players'] if p['id']!=uid]
        if not st['players']: _save(q,cid,None)
        else: _save(q,cid,st)
    await update.effective_message.reply_text("You left the UNO game.")

async def uno_stop(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    if not _group(update): return await update.effective_message.reply_text("Use /stop in a group.")
    q=ctx.application.bot_data['uno_q']; cid=update.effective_chat.id; st=_load(q,cid)
    if not st: return await update.effective_message.reply_text("No active UNO game.")
    member=await ctx.bot.get_chat_member(cid,update.effective_user.id)
    if update.effective_user.id!=st['creator'] and member.status not in ('administrator','creator'):
        return await update.effective_message.reply_text("Only the creator or a group admin can stop this game.")
    _save(q,cid,None); await update.effective_message.reply_text("🛑 UNO game stopped.")

async def uno_inline(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    iq=update.inline_query; parts=(iq.query or "").split()
    if len(parts)<2 or parts[0].lower()!="uno": return await iq.answer([],cache_time=0)
    try: cid=int(parts[1])
    except ValueError: return await iq.answer([],cache_time=0)
    q=ctx.application.bot_data['uno_q']; st=_load(q,cid); uid=iq.from_user.id
    if not st or st.get('status')!='playing' or not _player(st,uid): return await iq.answer([],cache_time=0,is_personal=True)
    if st['players'][st['turn']]['id']!=uid: return await iq.answer([],cache_time=0,is_personal=True)
    results=[]
    for idx,card in enumerate(st['hands'][str(uid)]):
        if _valid(card,st['discard'][-1]):
            token=secrets.token_urlsafe(8)
            st.setdefault('pending',{})[token]={"uid":uid,"index":idx,"card":card,"created":int(time.time())}
            results.append(InlineQueryResultArticle(id=token,title=_label(card),description="Play this card in the group",input_message_content=InputTextMessageContent(f"🎴 {iq.from_user.full_name} selected {_label(card)}"),reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("▶️ PLAY CARD",callback_data=f"uno:play:{cid}:{token}")]])))
    _save(q,cid,st)
    await iq.answer(results,cache_time=0,is_personal=True,button=None)

async def uno_callback(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    cq=update.callback_query; data=cq.data.split(":"); q=ctx.application.bot_data['uno_q']
    if len(data)<3: return
    action=data[1]
    try: cid=int(data[2])
    except ValueError: return
    async with UNO_LOCKS.setdefault(cid,asyncio.Lock()):
        st=_load(q,cid)
        if not st or st.get('status')!='playing': return await cq.answer("No active game.",show_alert=True)
        uid=cq.from_user.id
        if action=='draw':
            if st['players'][st['turn']]['id']!=uid: return await cq.answer("Not your turn.",show_alert=True)
            _draw(st,uid,1); _next(st); _save(q,cid,st)
            await cq.answer("Card drawn.")
            await cq.edit_message_reply_markup(reply_markup=None)
            await _announce(ctx,cid,st,f"📥 {cq.from_user.full_name} drew a card.")
            return
        if action=='call':
            if not _player(st,uid): return await cq.answer("Join the game first.",show_alert=True)
            hand=st['hands'][str(uid)]
            if len(hand)==1:
                await cq.answer("UNO! You have one card.",show_alert=True)
            else: await cq.answer("You can call UNO when you have one card.",show_alert=True)
            return
        if action=='play' and len(data)==4:
            token=data[3]; pending=st.get('pending',{}).get(token)
            if not pending or pending['uid']!=uid: return await cq.answer("This card selection isn't yours or has expired.",show_alert=True)
            if st['players'][st['turn']]['id']!=uid: return await cq.answer("Not your turn.",show_alert=True)
            hand=st['hands'][str(uid)]; idx=pending['index']
            if idx>=len(hand) or hand[idx]!=pending['card']: return await cq.answer("Your hand changed. Choose again.",show_alert=True)
            card=hand[idx]
            if not _valid(card,st['discard'][-1],st.get('chosen_color')): return await cq.answer("That card cannot be played now.",show_alert=True)
            hand.pop(idx); st['discard'].append(card); st.get('pending',{}).clear()
            c,v=card.split(':',1); skip=0; extra=f"{cq.from_user.full_name} played {_label(card)}."
            if v=='reverse': st['direction']*=-1; skip=1 if len(st['players'])==2 else 0
            elif v=='skip': skip=1
            elif v=='draw2':
                target=st['players'][(st['turn']+st['direction'])%len(st['players'])]['id']; _draw(st,target,2); skip=1
            elif v=='draw4':
                target=st['players'][(st['turn']+st['direction'])%len(st['players'])]['id']; _draw(st,target,4); skip=1
            if c=='wild':
                # Color selection is done in group after playing the wild card.
                st['await_color']={"uid":uid,"value":v}
                _save(q,cid,st)
                await cq.answer("Wild card selected.")
                await cq.edit_message_reply_markup(reply_markup=None)
                colors=InlineKeyboardMarkup([[InlineKeyboardButton(COLOR_EMOJI[x]+" "+x.title(),callback_data=f"uno:color:{cid}:{x}") for x in COLORS]])
                await ctx.bot.send_message(cid,f"🌈 {cq.from_user.mention_html()} choose the next color:",parse_mode="HTML",reply_markup=colors)
                return
            if not hand:
                _save(q,cid,None); await cq.edit_message_reply_markup(reply_markup=None)
                await ctx.bot.send_message(cid,f"🏆 <a href=\"tg://user?id={uid}\">{cq.from_user.full_name}</a> wins PCREW UNO!",parse_mode="HTML"); return
            _next(st,1+skip); _save(q,cid,st); await cq.answer("Card played."); await cq.edit_message_reply_markup(reply_markup=None)
            await _announce(ctx,cid,st,extra); return
        if action=='color' and len(data)==4:
            choice=data[3]
            pending=st.get('await_color')
            if not pending or pending['uid']!=uid: return await cq.answer("Only the player who used Wild can choose.",show_alert=True)
            # Store chosen color as a temporary color override for matching.
            st['chosen_color']=choice; st['await_color']=None
            if pending['value']=='draw4':
                target=st['players'][(st['turn']+st['direction'])%len(st['players'])]['id']; _draw(st,target,4); _next(st,2)
            else: _next(st,1)
            _save(q,cid,st); await cq.answer("Color selected."); await cq.edit_message_reply_markup(reply_markup=None)
            await _announce(ctx,cid,st,f"🌈 Color changed to {COLOR_EMOJI[choice]} {choice.title()}."); return

async def uno_stats(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=ctx.application.bot_data['uno_q']; rows=q("SELECT wins,games FROM uno_stats WHERE user_id=?",(update.effective_user.id,),True); w,g=rows[0] if rows else (0,0)
    await update.effective_message.reply_text(f"🎴 PCREW UNO Stats\n🏆 Wins: {w}\n🎮 Games: {g}")

async def register_uno(app,q):
    _uno_db(q); app.bot_data['uno_q']=q
    try:
        pack=await app.bot.get_sticker_set(UNO_STICKER_SET)
        print(f"UNO sticker pack available: {pack.name} ({len(pack.stickers)} stickers)")
    except Exception as exc: print(f"UNO sticker pack not loaded yet: {exc}")
    app.add_handler(CommandHandler('new',uno_new))
    app.add_handler(CommandHandler('join',uno_join))
    app.add_handler(CommandHandler('leave',uno_leave))
    app.add_handler(CommandHandler('stop',uno_stop))
    app.add_handler(CommandHandler('start',uno_start),group=1)
    app.add_handler(InlineQueryHandler(uno_inline))
    app.add_handler(CallbackQueryHandler(uno_callback,pattern=r'^uno:'))
    app.add_handler(CommandHandler('unostats',uno_stats))
