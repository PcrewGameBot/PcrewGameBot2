"""PCREW group-only classic UNO. Private hands are exposed only to their owner via Telegram inline mode."""
import json, random, uuid, time, html
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultArticle, InputTextMessageContent, Update
from telegram.ext import ContextTypes

COLORS = ("Red", "Blue", "Green", "Yellow")
EMOJI = {"Red":"🔴", "Blue":"🔵", "Green":"🟢", "Yellow":"🟡", "Wild":"⚫"}

def make_deck():
    deck=[]
    for c in COLORS:
        deck.append({"id":uuid.uuid4().hex[:10],"c":c,"v":"0"})
        for v in [str(x) for x in range(1,10)]+["Skip","Reverse","Draw Two"]:
            for _ in range(2): deck.append({"id":uuid.uuid4().hex[:10],"c":c,"v":v})
    for v in ("Wild","Wild Draw Four"):
        for _ in range(4): deck.append({"id":uuid.uuid4().hex[:10],"c":"Wild","v":v})
    random.shuffle(deck); return deck

def label(card):
    return (EMOJI.get(card['c'],'⚫') + " " + card['c'] + " " + card['v']).strip()

class UNO:
    def __init__(self, q): self.q=q
    def load(self, gid):
        rows=self.q("SELECT data FROM uno_games WHERE game_id=? AND status='active'",(gid,),True)
        return json.loads(rows[0][0]) if rows else None
    def save(self,gid,d): self.q("UPDATE uno_games SET data=?,updated=? WHERE game_id=?",(json.dumps(d),int(time.time()),gid))
    def create(self,chat_id,creator):
        gid=uuid.uuid4().hex[:8]
        d={"chat":chat_id,"creator":creator.id,"players":[],"hands":{},"draw":make_deck(),"discard":[],"turn":0,"direction":1,"color":None,"pending4":None,"status":"lobby"}
        d['players'].append({"id":creator.id,"name":creator.full_name})
        self.q("INSERT INTO uno_games(game_id,chat_id,data,status,updated) VALUES(?,?,?,?,?)",(gid,chat_id,json.dumps(d),'active',int(time.time())))
        return gid,d
    def render(self,gid,d,notice=''):
        if d['status']=='lobby':
            names='\n'.join(f"• {html.escape(p['name'])}" for p in d['players'])
            text=f"🎴 <b>PCREW UNO</b>\n\nWaiting for players…\n\n{names}\n\nPlayers: {len(d['players'])} · Minimum: 2\nPress JOIN, then the creator starts the game."
            kb=[[InlineKeyboardButton('➕ JOIN',callback_data=f'uj:{gid}')],[InlineKeyboardButton('▶️ START',callback_data=f'us:{gid}')],[InlineKeyboardButton('✖️ CANCEL',callback_data=f'ux:{gid}')]]
        else:
            p=d['players'][d['turn']]; top=d['discard'][-1]
            counts='\n'.join(f"• {html.escape(x['name'])}: {len(d['hands'][str(x['id'])])} cards" for x in d['players'])
            text=f"🎴 <b>PCREW UNO</b>\n\nTop card: <b>{label(top)}</b>\nCurrent color: {EMOJI.get(d['color'], '')} {d['color']}\n\n👉 Turn: <b>{html.escape(p['name'])}</b>\n\n{counts}"
            if notice: text += '\n\n'+notice
            kb=[[InlineKeyboardButton('🎴 CHOOSE CARD',switch_inline_query_current_chat=f"uno_{gid} "),InlineKeyboardButton('📥 DRAW CARD',callback_data=f'ud:{gid}')]]
            if d.get('pending4'):
                kb=[[InlineKeyboardButton('⚖️ CHALLENGE +4',callback_data=f'uc:{gid}'),InlineKeyboardButton('✅ ACCEPT +4',callback_data=f'ua:{gid}')]]
            else:
                one=[x for x in d['players'] if len(d['hands'].get(str(x['id']),[]))==1]
                if one: kb.append([InlineKeyboardButton('📣 UNO!',callback_data=f'un:{gid}:{one[0]["id"]}')])
        return text,InlineKeyboardMarkup(kb)
    async def post(self,update,ctx,gid,d,notice=''):
        text,kb=self.render(gid,d,notice)
        if update.callback_query:
            try: await update.callback_query.edit_message_text(text,parse_mode='HTML',reply_markup=kb)
            except Exception: pass
        else: await update.effective_message.reply_text(text,parse_mode='HTML',reply_markup=kb)
    async def command(self,update,ctx):
        chat=update.effective_chat
        if chat.type not in ('group','supergroup'): return await update.message.reply_text('UNO ကို Group ထဲမှာပဲ ဖွင့်လို့ရပါတယ်။')
        gid,d=self.create(chat.id,update.effective_user)
        text,kb=self.render(gid,d); await update.message.reply_text(text,parse_mode='HTML',reply_markup=kb)
    async def callback(self,update,ctx):
        q=update.callback_query; await q.answer(); parts=q.data.split(':'); action,gid=parts[0],parts[1]; d=self.load(gid)
        if not d: return await q.answer('ဒီ Game မရှိတော့ပါဘူး။',show_alert=True)
        uid=q.from_user.id; player=next((p for p in d['players'] if p['id']==uid),None)
        if action=='uj':
            if d['status']!='lobby': return await q.answer('Game စပြီးပါပြီ။',show_alert=True)
            if not player: d['players'].append({'id':uid,'name':q.from_user.full_name}); d['hands'][str(uid)]=[]
        elif action=='uw':
            color=parts[2] if len(parts)>2 else ''
            pending=d.get('pending_wild')
            if not pending or pending['uid']!=uid or color not in COLORS: return await q.answer('အရောင်ရွေးချယ်ခွင့် မရှိပါ။',show_alert=True)
            d['color']=color; d['pending_wild']=None
            if not d['hands'].get(str(uid),[]):
                d['status']='ended'; self.q("UPDATE uno_games SET status='ended' WHERE game_id=?",(gid,)); self.save(gid,d)
                return await ctx.bot.send_message(d['chat'],f"🏆 <b>{html.escape(next(p['name'] for p in d['players'] if p['id']==uid))} wins PCREW UNO!</b>",parse_mode='HTML')
            if pending['value']=='Wild Draw Four':
                d['pending4']={'offender':uid,'had_color':pending['had_color']}
                self._advance(d)
            else: self._advance(d)
            self.save(gid,d); return await self.post(update,ctx,gid,d,f'Color changed to {color}.')
        elif action=='un':
            target=int(parts[2]) if len(parts)>2 else 0
            if uid!=target or len(d['hands'].get(str(uid),[]))!=1: return await q.answer('UNO call မလုပ်နိုင်ပါ။',show_alert=True)
            await q.answer('UNO! 📣',show_alert=True)
            return
        elif action=='ux':
            if uid!=d['creator']: return await q.answer('ဖန်တီးသူပဲ Cancel လုပ်နိုင်ပါတယ်။',show_alert=True)
            self.q("UPDATE uno_games SET status='ended' WHERE game_id=?",(gid,)); return await q.edit_message_text('UNO game cancelled.')
        elif action=='us':
            if uid!=d['creator']: return await q.answer('ဖန်တီးသူပဲ စနိုင်ပါတယ်။',show_alert=True)
            if len(d['players'])<2: return await q.answer('အနည်းဆုံး လူ ၂ ယောက်လိုပါတယ်။',show_alert=True)
            deck=d['draw']
            for p in d['players']: d['hands'][str(p['id'])]=[deck.pop() for _ in range(7)]
            first=deck.pop()
            while first['c']=='Wild': deck.insert(0,first); first=deck.pop()
            d['discard']=[first]; d['color']=first['c']; d['status']='playing'; d['turn']=0
        elif action=='ud':
            if d['status']!='playing' or not player or d['players'][d['turn']]['id']!=uid: return await q.answer('မင်းအလှည့်မဟုတ်သေးပါ။',show_alert=True)
            self._draw(d,uid,1)
            drawn=d['hands'][str(uid)][-1]
            if not self.playable(drawn,d): self._advance(d)
        elif action in ('uc','ua'):
            if not d.get('pending4') or d['players'][d['turn']]['id']!=uid: return await q.answer('ဒီ Action ကို မလုပ်နိုင်ပါ။',show_alert=True)
            pending=d['pending4']; offender=pending['offender']; target=uid
            if action=='uc':
                if pending['had_color']:
                    self._draw(d,offender,4)
                    d['turn']=next(i for i,p in enumerate(d['players']) if p['id']==target)
                    note='Challenge successful: offender draws 4 cards.'
                else:
                    self._draw(d,target,6); self._advance(d)
                    note='Challenge failed: draw 6 cards.'
            else: self._draw(d,target,4); self._advance(d); note='Accepted +4: draw 4 cards.'
            d['pending4']=None; self.save(gid,d); return await self.post(update,ctx,gid,d,note)
        self.save(gid,d); await self.post(update,ctx,gid,d)
    def _draw(self,d,uid,n):
        for _ in range(n):
            if not d['draw']:
                if len(d['discard'])>1:
                    top=d['discard'][-1]; d['draw']=d['discard'][:-1]; d['discard']=[top]; random.shuffle(d['draw'])
                else: break
            if d['draw']: d['hands'].setdefault(str(uid),[]).append(d['draw'].pop())
    def _advance(self,d,steps=1): d['turn']=(d['turn']+d['direction']*steps)%len(d['players'])
    def playable(self,card,d):
        top=d['discard'][-1]
        return card['c']=='Wild' or card['c']==d['color'] or card['v']==top['v']
    async def inline(self,update,ctx):
        query=update.inline_query.query.strip()
        if not query.startswith('uno_'): return await update.inline_query.answer([],cache_time=0)
        gid=query.split()[0][4:]; d=self.load(gid)
        if not d or d['status']!='playing': return await update.inline_query.answer([],cache_time=0)
        uid=update.inline_query.from_user.id
        if d['players'][d['turn']]['id']!=uid: return await update.inline_query.answer([],cache_time=0)
        hand=d['hands'].get(str(uid),[]); results=[]
        for card in hand:
            if self.playable(card,d):
                title=label(card)
                results.append(InlineQueryResultArticle(id=card['id'],title=title,input_message_content=InputTextMessageContent(f"!uno|{gid}|{card['id']}"),description='Play this card'))
        if not results: results=[InlineQueryResultArticle(id='none',title='No playable card',input_message_content=InputTextMessageContent('!uno_no_play'))]
        await update.inline_query.answer(results,cache_time=0,is_personal=True)
    async def action_message(self,update,ctx):
        msg=update.effective_message
        if not msg or not msg.text: return
        if msg.text=='!uno_no_play':
            try: await msg.delete()
            except Exception: pass
            return
        try: _,gid,cid=msg.text.split('|',2)
        except ValueError: return
        d=self.load(gid)
        if not d: return
        uid=msg.from_user.id
        try: await msg.delete()
        except Exception: pass
        if d['chat'] != msg.chat_id or d['status']!='playing' or d['players'][d['turn']]['id']!=uid: return
        hand=d['hands'].get(str(uid),[]); card=next((c for c in hand if c['id']==cid),None)
        if not card or not self.playable(card,d): return
        previous_color=d['color']; hand.remove(card)
        d['discard'].append(card); note=f"{html.escape(next(p['name'] for p in d['players'] if p['id']==uid))} played {label(card)}."
        if card['c']=='Wild':
            d['pending_wild']={'uid':uid,'value':card['v'],'had_color':any(x['c']==previous_color for x in hand)}
            self.save(gid,d)
            colors=[[InlineKeyboardButton(EMOJI[c]+' '+c,callback_data=f'uw:{gid}:{c}') for c in COLORS[:2]], [InlineKeyboardButton(EMOJI[c]+' '+c,callback_data=f'uw:{gid}:{c}') for c in COLORS[2:]]]
            await ctx.bot.send_message(d['chat'], '🎨 <b>Choose the next color:</b>',parse_mode='HTML',reply_markup=InlineKeyboardMarkup(colors)); return
        d['color']=card['c']
        if not hand:
            d['status']='ended'; self.q("UPDATE uno_games SET status='ended' WHERE game_id=?",(gid,)); self.save(gid,d)
            await ctx.bot.send_message(d['chat'],f"🏆 <b>{html.escape(next(p['name'] for p in d['players'] if p['id']==uid))} wins PCREW UNO!</b>",parse_mode='HTML'); return
        if card['v']=='Skip': self._advance(d,2 if len(d['players'])==2 else 2)
        elif card['v']=='Reverse':
            d['direction']*=-1
            if len(d['players'])>2: self._advance(d)
        elif card['v']=='Draw Two':
            self._advance(d); target=d['players'][d['turn']]['id']; self._draw(d,target,2); self._advance(d)
        else: self._advance(d)
        self.save(gid,d)
        text,kb=self.render(gid,d,note)
        try: await ctx.bot.send_message(d['chat'],text,parse_mode='HTML',reply_markup=kb)
        except Exception as e: print('UNO update error:',e)
