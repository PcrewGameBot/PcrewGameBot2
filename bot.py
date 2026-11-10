import os, json, hmac, hashlib, time, sqlite3, asyncio, html
from datetime import datetime, timezone
from urllib.parse import parse_qsl
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, MenuButtonWebApp
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes


def env(name, default=""):
    # removes every space and newline, so copy-paste mistakes cannot break a value
    return "".join(os.environ.get(name, default).split())


TOKEN = env("BOT_TOKEN")
URL = env("WEBAPP_URL").rstrip("/")
if URL and not URL.startswith("http"):
    URL = "https://" + URL
PORT = int(env("PORT") or 8080)
DB = env("DB_PATH") or ("/data/scores.db" if os.path.isdir("/data") else "scores.db")
HERE = os.path.dirname(os.path.abspath(__file__))

# Join gate: set CHANNEL and GROUP in any of these forms: @username, username, https://t.me/username
# (or a numeric chat id like -100123... for private ones, plus CHANNEL_LINK / GROUP_LINK invite links).
def norm(v):
    if not v:
        return "", ""
    if v.startswith("http"):
        tail = v.split("t.me/")[-1].split("?")[0].strip("/")
        if tail.startswith("+") or tail.startswith("joinchat"):
            return "", v
        return "@" + tail, v
    if v.lstrip("-").isdigit():
        return v, ""
    name = v.lstrip("@")
    return "@" + name, "https://t.me/" + name


CHANNEL, _cl = norm(env("CHANNEL"))
GROUP, _gl = norm(env("GROUP"))
CHANNEL_LINK = env("CHANNEL_LINK") or _cl
GROUP_LINK = env("GROUP_LINK") or _gl
OWNER_ID = int(env("OWNER_ID") or 0)
OWNER_USERNAME = env("OWNER_USERNAME") or ""
BOT_USERNAME = env("BOT_USERNAME").lstrip("@")
LOG_CHAT = env("LOG_CHAT_ID")
if LOG_CHAT.lstrip("-").isdigit():
    LOG_CHAT = int(LOG_CHAT)

CAPTION = (
    "<b>🎮 Welcome to the PcrewGame Universe!</b>\n\n"
    "🔥 Your adventure starts here.\n"
    "🏆 Play • Compete • Win\n"
    "💎 Earn rewards and become a legend!\n"
    "🚀 Let’s Play!"
)
WELCOME_IMG = os.path.join(HERE, "welcome.jpg")
FILE_ID = None
BOT = None

# To add a game: upload its html file and add one line here.
# xp = XP per 1 score point, used for the Overall leaderboard (0.1 = 100 score is 10 XP).
GAMES = {
    "thunderstorm": {"cat": "action", "title": "Thunderstorm Survival", "emoji": "⚡", "file": "thunderstorm.html", "xp": 0.1},
    "spaceshooter": {"cat": "action", "title": "Space Shooter", "emoji": "🚀", "file": "spaceshooter.html", "xp": 0.05},
    "sortcolors": {"cat": "puzzle", "title": "Sort the Colours", "emoji": "🧪", "file": "sortcolors.html", "xp": 0.05},
    "timberchop": {"cat": "tap", "title": "Timber Chop", "emoji": "🪓", "file": "timberchop.html", "xp": 0.5},
    "crowdcannon": {"cat": "action", "title": "Crowd Cannon", "emoji": "💣", "file": "crowdcannon.html", "xp": 0.05},
    "trafficracer": {"cat": "sport", "title": "Traffic Racer", "emoji": "🏎️", "file": "trafficracer.html", "xp": 0.05},
    "penalty": {"cat": "sport", "title": "Penalty Shootout", "emoji": "⚽", "file": "penalty.html", "xp": 4},
    "leveldevil": {"cat": "action", "title": "Level Devil", "emoji": "😈", "file": "leveldevil.html", "xp": 3},
    "blockblast": {"cat": "puzzle", "title": "Block Blast", "emoji": "🧩", "file": "blockblast.html", "xp": 0.05},
    "fruitslice": {"cat": "tap", "title": "Fruit Slice", "emoji": "🍉", "file": "fruitslice.html", "xp": 1},
    "stacktower": {"cat": "tap", "title": "Stack Tower", "emoji": "🧱", "file": "stacktower.html", "xp": 1.5},
    "hoopshot": {"cat": "sport", "title": "Hoop Shot", "emoji": "🏀", "file": "hoopshot.html", "xp": 3},
    "game2048": {"cat": "puzzle", "title": "2048", "emoji": "🔢", "file": "game2048.html", "xp": 0.01},
    "helixjump": {"cat": "action", "title": "Helix Jump", "emoji": "🌀", "file": "helixjump.html", "xp": 1.5},
    "gravityflip": {"cat": "action", "title": "Gravity Flip", "emoji": "🔄", "file": "gravityflip.html", "xp": 0.2},
    "knifehit": {"cat": "tap", "title": "Knife Hit", "emoji": "🔪", "file": "knifehit.html", "xp": 1.5},
    "colorswitch": {"cat": "tap", "title": "Color Switch", "emoji": "🎨", "file": "colorswitch.html", "xp": 3},
    "rhythmtiles": {"cat": "tap", "title": "Rhythm Tiles", "emoji": "🎹", "file": "rhythmtiles.html", "xp": 1},
    "mergefruits": {"cat": "puzzle", "title": "Merge Fruits", "emoji": "🍇", "file": "mergefruits.html", "xp": 0.03},
}

# DATABASE_URL set = Postgres (data is never lost), otherwise SQLite
DATABASE_URL = env("DATABASE_URL")
if DATABASE_URL:
    import psycopg2

    def q(sql, args=(), fetch=False):
        conn = psycopg2.connect(DATABASE_URL)
        try:
            with conn, conn.cursor() as cur:
                cur.execute(sql.replace("?", "%s"), args)
                return cur.fetchall() if fetch else None
        finally:
            conn.close()

    BEST = "GREATEST(scores.score, excluded.score)"
    INC = "plays.n + 1"
else:
    _db = sqlite3.connect(DB, check_same_thread=False)

    def q(sql, args=(), fetch=False):
        cur = _db.execute(sql, args)
        _db.commit()
        return cur.fetchall() if fetch else None

    BEST = "MAX(score, excluded.score)"
    INC = "n + 1"

q("CREATE TABLE IF NOT EXISTS plays(game TEXT PRIMARY KEY, n BIGINT)")
q("CREATE TABLE IF NOT EXISTS scores(game TEXT, uid BIGINT, name TEXT, score BIGINT, ts BIGINT, PRIMARY KEY(game, uid))")

# Couple Court tables. Each couple has its own relationship settings, cases and used punishments.
q("CREATE TABLE IF NOT EXISTS couples(couple_id TEXT PRIMARY KEY, invite_code TEXT UNIQUE, user1 BIGINT, name1 TEXT, user2 BIGINT, name2 TEXT, permission INTEGER DEFAULT 0, type1 TEXT, type2 TEXT, created BIGINT)")
# Safe additive migrations for existing databases.
try: q("ALTER TABLE couples ADD COLUMN removed INTEGER DEFAULT 0")
except Exception: pass
q("CREATE TABLE IF NOT EXISTS court_cases(case_id TEXT PRIMARY KEY, couple_id TEXT, plaintiff BIGINT, defendant BIGINT, category TEXT, complaint TEXT, response TEXT, verdict TEXT, punishment TEXT, status TEXT, created BIGINT, completed BIGINT)")
q("CREATE TABLE IF NOT EXISTS used_punishments(couple_id TEXT, punishment TEXT, used BIGINT, PRIMARY KEY(couple_id, punishment))")

# Phase 1 supports LDR only. In-Person and Both are intentionally Coming Soon.
with open(os.path.join(HERE, "punishments_ldr.json"), encoding="utf-8") as _pf:
    _ldr_items = json.load(_pf)
PUNISHMENTS = {"ldr": [item["text"] for item in _ldr_items]}
if len(PUNISHMENTS["ldr"]) != 400:
    raise RuntimeError("punishments_ldr.json must contain exactly 400 LDR punishments")


def _user(init_data):
    return verify(init_data)

def _couple_for(uid):
    rows=q("SELECT couple_id, invite_code, user1, name1, user2, name2, permission, type1, type2 FROM couples WHERE user1=? OR user2=?", (uid,uid), True)
    return rows[0] if rows else None

def _is_removed(cid):
    try:
        rows=q("SELECT removed FROM couples WHERE couple_id=?",(cid,),True)
        return bool(rows and rows[0][0])
    except Exception:
        return False

def _couple_ready(row):
    return bool(row and row[4] and not _is_removed(row[0]) and row[7] == "ldr" and row[8] == "ldr")

def _couple_json(row, uid):
    if not row: return {"linked":False,"owner":bool(OWNER_ID and uid==OWNER_ID)}
    cid, code, u1, n1, u2, n2, perm, t1, t2 = row
    return {"linked":bool(u2),"coupleId":cid,"partnerName":(n2 if uid==u1 else n1) if u2 else None,
            "permission":not _is_removed(cid),"removed":_is_removed(cid),"myType":t1 if uid==u1 else t2,"partnerType":t2 if uid==u1 else t1,
            "typeMatched":bool(t1 and t2 and t1==t2),"ready":_couple_ready(row),
            "inviteCode":code if not u2 else None,"inviteLink":(f"https://t.me/{BOT_USERNAME}?startapp=join_{code}" if code and BOT_USERNAME else None),"owner":bool(OWNER_ID and uid==OWNER_ID)}

async def court_state(req):
    u=_user((await req.json()).get("initData",""))
    if not u: return web.json_response({"ok":False},status=401)
    row=_couple_for(u["id"])
    data=_couple_json(row,u["id"])
    data["isOwner"]=bool(OWNER_ID and u["id"]==OWNER_ID)
    data["ownerUsername"]=OWNER_USERNAME
    if row:
        cases=q("SELECT case_id, category, complaint, response, verdict, punishment, status, created, plaintiff, defendant FROM court_cases WHERE couple_id=? ORDER BY created DESC LIMIT 20",(row[0],),True)
        data["cases"]=[{"id":x[0],"category":x[1],"complaint":x[2],"response":x[3],"verdict":x[4],"punishment":x[5],"status":x[6],"created":x[7],"plaintiff":x[8],"defendant":x[9]} for x in cases]
        data["myId"]=u["id"]
        data["isOwner"]=bool(OWNER_ID and u["id"]==OWNER_ID)
        data["ownerUsername"]=OWNER_USERNAME
    return web.json_response({"ok":True,"court":data})

async def court_create(req):
    b=await req.json(); u=_user(b.get("initData",""))
    if not u: return web.json_response({"ok":False,"error":"Open inside Telegram."},status=401)
    if _couple_for(u["id"]): return web.json_response({"ok":False,"error":"You are already linked to a couple."},status=409)
    import secrets
    cid="CP"+secrets.token_hex(3).upper(); code=secrets.token_urlsafe(9).replace('-','').replace('_','').upper()[:12]
    name=(u.get("first_name") or u.get("username") or "Player")[:40]
    q("INSERT INTO couples(couple_id,invite_code,user1,name1,permission,created) VALUES(?,?,?,?,1,?)",(cid,code,u["id"],name,int(time.time())))
    link=f"https://t.me/{BOT_USERNAME}?startapp=join_{code}" if BOT_USERNAME else None
    return web.json_response({"ok":True,"coupleId":cid,"inviteCode":code,"inviteLink":link,"error":None if link else "Set BOT_USERNAME in Railway Variables to generate Telegram invite links."})

async def court_cancel_invite(req):
    b=await req.json(); u=_user(b.get("initData",""))
    if not u: return web.json_response({"ok":False},status=401)
    rows=q("SELECT couple_id FROM couples WHERE user1=? AND user2 IS NULL AND invite_code IS NOT NULL",(u["id"],),True)
    if not rows: return web.json_response({"ok":False,"error":"No pending invitation found."},status=404)
    q("DELETE FROM couples WHERE couple_id=?",(rows[0][0],))
    return web.json_response({"ok":True})

async def court_join(req):
    b=await req.json(); u=_user(b.get("initData","")); code=(b.get("code") or "").strip().upper()
    if not u: return web.json_response({"ok":False},status=401)
    if _couple_for(u["id"]): return web.json_response({"ok":False,"error":"You are already linked to a couple."},status=409)
    rows=q("SELECT couple_id,user1 FROM couples WHERE invite_code=? AND user2 IS NULL AND COALESCE(removed,0)=0",(code,),True)
    if not rows: return web.json_response({"ok":False,"error":"This invitation is invalid, expired, or unavailable."},status=404)
    cid,creator=rows[0]
    if creator==u["id"]: return web.json_response({"ok":False,"error":"You cannot accept your own invitation."},status=400)
    name=(u.get("first_name") or u.get("username") or "Player")[:40]
    q("UPDATE couples SET user2=?,name2=?,invite_code=NULL WHERE couple_id=? AND user2 IS NULL",(u["id"],name,cid))
    if BOT:
        try: await BOT.send_message(creator,f"💑 Your partner accepted the invitation! Couple ID: {cid}. Both partners must select LDR.")
        except Exception: pass
    return web.json_response({"ok":True,"coupleId":cid})

async def court_type(req):
    b=await req.json(); u=_user(b.get("initData","")); typ=b.get("type")
    if not u: return web.json_response({"ok":False},status=401)
    if typ != "ldr": return web.json_response({"ok":False,"error":"In-Person and Both modes are coming soon. LDR is available now."},status=403)
    row=_couple_for(u["id"])
    if not row or not row[4]: return web.json_response({"ok":False,"error":"Link with your partner first."},status=400)
    col="type1" if row[2]==u["id"] else "type2"
    q(f"UPDATE couples SET {col}=? WHERE couple_id=?",(typ,row[0]))
    updated=_couple_for(u["id"])
    return web.json_response({"ok":True,"court":_couple_json(updated,u["id"])})

async def court_open(req):
    b=await req.json(); u=_user(b.get("initData","")); complaint=(b.get("complaint") or "").strip(); category=(b.get("title") or b.get("category") or "General").strip()[:80]
    if not u: return web.json_response({"ok":False},status=401)
    row=_couple_for(u["id"])
    if not _couple_ready(row): return web.json_response({"ok":False,"error":"Both partners must be linked, active, and have selected LDR."},status=403)
    if len(complaint)<5 or len(complaint)>1000: return web.json_response({"ok":False,"error":"Complaint must be 5–1000 characters."},status=400)
    active=q("SELECT case_id FROM court_cases WHERE couple_id=? AND status='awaiting_response'",(row[0],),True)
    if active: return web.json_response({"ok":False,"error":"Please finish the current case first."},status=409)
    import secrets
    caseid="CASE-"+secrets.token_hex(3).upper()
    defendant=row[4] if row[2]==u["id"] else row[2]
    plaintiff_name=row[3] if row[2]==u["id"] else row[5]
    q("INSERT INTO court_cases(case_id,couple_id,plaintiff,defendant,category,complaint,status,created) VALUES(?,?,?,?,?,?,?,?)",(caseid,row[0],u["id"],defendant,category,complaint,"awaiting_response",int(time.time())))
    if BOT:
        try: await BOT.send_message(defendant,f"⚖️ NEW COUPLE COURT CASE\nCase: {caseid}\nFrom: {plaintiff_name}\nCategory: {category}\n\nComplaint: {complaint}\n\nOpen the PCREW Game Mini App to submit your response.")
        except Exception: pass
    return web.json_response({"ok":True,"caseId":caseid})

async def court_respond(req):
    b=await req.json(); u=_user(b.get("initData","")); response=(b.get("response") or "").strip(); caseid=b.get("caseId")
    if not u or len(response)<2 or len(response)>1000: return web.json_response({"ok":False,"error":"Response must be 2–1000 characters."},status=400)
    rows=q("SELECT couple_id,plaintiff,defendant,status FROM court_cases WHERE case_id=?",(caseid,),True)
    if not rows: return web.json_response({"ok":False,"error":"Case not found."},status=404)
    cid,plaintiff,defendant,status=rows[0]
    if u["id"]!=defendant or status!="awaiting_response": return web.json_response({"ok":False,"error":"You cannot respond to this case."},status=403)
    verdict="GUILTY" if __import__('secrets').randbelow(2)==0 else "NOT GUILTY"
    punishment=None
    if verdict=="GUILTY":
        cp=q("SELECT type1 FROM couples WHERE couple_id=?",(cid,),True)[0][0]
        if cp != "ldr":
            return web.json_response({"ok":False,"error":"Only LDR mode is available right now."},status=403)
        allowed=PUNISHMENTS["ldr"]
        used={x[0] for x in q("SELECT punishment FROM used_punishments WHERE couple_id=?",(cid,),True)}
        remaining=[x for x in allowed if x not in used]
        if not remaining:
            q("DELETE FROM used_punishments WHERE couple_id=?",(cid,)); remaining=allowed
        punishment=__import__('secrets').choice(remaining)
        q("INSERT INTO used_punishments(couple_id,punishment,used) VALUES(?,?,?)",(cid,punishment,int(time.time())))
    q("UPDATE court_cases SET response=?,verdict=?,punishment=?,status='completed',completed=? WHERE case_id=?",(response,verdict,punishment,int(time.time()),caseid))
    names=q("SELECT user1,name1,user2,name2 FROM couples WHERE couple_id=?",(cid,),True)[0]
    if BOT:
        msg=f"⚖️ COURT VERDICT — {caseid}\n\nVerdict: {verdict}"
        if punishment: msg+=f"\n\n🎡 Punishment: {punishment}\n\nNo skip allowed."
        else: msg+="\n\nThe case is closed. No punishment this time."
        for uid in (names[0],names[2]):
            try: await BOT.send_message(uid,msg)
            except Exception: pass
    return web.json_response({"ok":True,"verdict":verdict,"punishment":punishment})

async def court_owner_list(req):
    b=await req.json(); u=_user(b.get("initData",""))
    if not u or not OWNER_ID or u["id"]!=OWNER_ID: return web.json_response({"ok":False},status=403)
    rows=q("SELECT couple_id,name1,name2,user1,user2,type1,type2,COALESCE(removed,0) FROM couples ORDER BY created DESC LIMIT 200",(),True)
    return web.json_response({"ok":True,"couples":[{"id":x[0],"name1":x[1],"name2":x[2],"user1":x[3],"user2":x[4],"type1":x[5],"type2":x[6],"removed":bool(x[7])} for x in rows]})

async def court_owner_status(req):
    b=await req.json(); u=_user(b.get("initData","")); cid=(b.get("coupleId") or "").upper(); remove=bool(b.get("remove"))
    if not u or not OWNER_ID or u["id"]!=OWNER_ID: return web.json_response({"ok":False},status=403)
    rows=q("SELECT user1,user2 FROM couples WHERE couple_id=?",(cid,),True)
    if not rows: return web.json_response({"ok":False,"error":"Couple not found."},status=404)
    q("UPDATE couples SET removed=? WHERE couple_id=?",(1 if remove else 0,cid))
    if BOT:
        for uid in rows[0]:
            if uid:
                try: await BOT.send_message(uid,"🚫 This couple has been disabled by the bot owner." if remove else "✅ Your Couple Court access has been restored by the bot owner.")
                except Exception: pass
    return web.json_response({"ok":True})


def verify(init_data):
    """Checks that the data really comes from Telegram, so scores cannot be faked."""
    try:
        d = dict(parse_qsl(init_data, strict_parsing=True))
        h = d.pop("hash")
        check = "\n".join(f"{k}={v}" for k, v in sorted(d.items()))
        secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
        good = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(good, h):
            return None
        if time.time() - int(d.get("auth_date", 0)) > 86400:
            return None
        return json.loads(d["user"])
    except Exception:
        return None


_ok_cache = {}


async def joined(uid):
    """True if the user is in the required channel and group."""
    if not (CHANNEL or GROUP):
        return True
    c = _ok_cache.get(uid)
    if c and time.time() - c < 60:
        return True
    for chat in (CHANNEL, GROUP):
        if not chat:
            continue
        try:
            cid = int(chat) if chat.lstrip("-").isdigit() else chat
            m = await BOT.get_chat_member(cid, uid)
            ok = m.status in ("member", "administrator", "creator") or (
                m.status == "restricted" and getattr(m, "is_member", False))
        except Exception as e:
            print("membership check error:", chat, e)
            ok = False
        if not ok:
            return False
    _ok_cache[uid] = time.time()
    return True


def top_rows(game, n=10):
    return q("SELECT name, score FROM scores WHERE game=? ORDER BY score DESC LIMIT ?", (game, n), True)


def overall_all():
    """Best score of every game, converted to XP and added together. Returns [(uid, name, xp)] sorted."""
    tot, names = {}, {}
    for uid, name, game, score in q("SELECT uid, name, game, score FROM scores", (), True):
        g = GAMES.get(game)
        if not g:
            continue
        tot[uid] = tot.get(uid, 0) + int(score * g.get("xp", 0.1))
        names[uid] = name
    return sorted(((u, names[u], t) for u, t in tot.items()), key=lambda x: -x[2])


def overall_rows(n=10):
    return [(name, xp) for _, name, xp in overall_all()[:n]]


def my_overall(uid):
    for i, (u, _, xp) in enumerate(overall_all()):
        if u == uid:
            return xp, i + 1
    return 0, None


async def hub(req):
    return web.FileResponse(os.path.join(HERE, "hub.html"))


async def game_page(req):
    g = GAMES.get(req.match_info["name"])
    if not g:
        raise web.HTTPNotFound()
    try:
        q("INSERT INTO plays VALUES(?, 1) ON CONFLICT(game) DO UPDATE SET n=" + INC, (req.match_info["name"],))
    except Exception as e:
        print("plays counter error:", e)
    return web.FileResponse(os.path.join(HERE, g["file"]))


async def api_games(req):
    plays = dict(q("SELECT game, n FROM plays", (), True))
    return web.json_response([{"id": k, "title": v["title"], "emoji": v["emoji"], "cat": v.get("cat", "tap"),
                               "plays": int(plays.get(k, 0))} for k, v in GAMES.items()])


async def api_me(req):
    try:
        b = await req.json()
    except Exception:
        b = {}
    u = verify(b.get("initData", ""))
    if not u:
        return web.json_response({"ok": False})
    ok = await joined(u["id"])
    xp, rank = my_overall(u["id"]) if ok else (0, None)
    return web.json_response({"ok": True, "joined": ok, "channel": CHANNEL_LINK, "group": GROUP_LINK,
                              "xp": xp, "rank": rank})


async def api_top(req):
    game = req.query.get("game", "")
    rows = overall_rows() if game == "overall" else top_rows(game)
    return web.json_response([{"name": n, "score": s} for n, s in rows])


async def api_score(req):
    try:
        b = await req.json()
    except Exception:
        return web.json_response({"ok": False}, status=400)
    u = verify(b.get("initData", ""))
    g, s = b.get("game"), b.get("score")
    if not u or g not in GAMES or not isinstance(s, int) or not 0 <= s <= 1_000_000:
        return web.json_response({"ok": False}, status=400)
    if not await joined(u["id"]):
        return web.json_response({"ok": False, "joined": False}, status=403)
    name = (u.get("first_name") or u.get("username") or "Player")[:20]
    q(
        "INSERT INTO scores VALUES(?,?,?,?,?) ON CONFLICT(game,uid) DO UPDATE SET "
        "name=excluded.name, score=" + BEST + ", ts=excluded.ts",
        (g, u["id"], name, s, int(time.time())),
    )
    return web.json_response({"ok": True})


def start_kb():
    rows, r = [], []
    if CHANNEL_LINK:
        r.append(InlineKeyboardButton("📢 Channel", url=CHANNEL_LINK))
    if GROUP_LINK:
        r.append(InlineKeyboardButton("👥 Group", url=GROUP_LINK))
    if r:
        rows.append(r)
    if URL:
        rows.append([InlineKeyboardButton("▶️ Start", web_app=WebAppInfo(url=URL))])
    return InlineKeyboardMarkup(rows)


async def log_start(bot, u):
    if not LOG_CHAT:
        return
    name = html.escape(" ".join(filter(None, [u.first_name, u.last_name])) or "-")
    uname = "@" + html.escape(u.username) if u.username else "-"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    text = (f"<blockquote>🚀 New start\n"
            f"👤 Name: {name}\n"
            f"🔗 Username: {uname}\n"
            f"🆔 ID: <code>{u.id}</code>\n"
            f"🕒 Time: {now}</blockquote>")
    try:
        await bot.send_message(LOG_CHAT, text, parse_mode=ParseMode.HTML)
    except Exception as e:
        print("log error:", e)


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global FILE_ID
    msg = update.message
    kb = start_kb()
    try:
        if FILE_ID:
            await msg.reply_photo(FILE_ID, caption=CAPTION, parse_mode=ParseMode.HTML, reply_markup=kb)
        else:
            with open(WELCOME_IMG, "rb") as f:
                sent = await msg.reply_photo(f, caption=CAPTION, parse_mode=ParseMode.HTML, reply_markup=kb)
            FILE_ID = sent.photo[-1].file_id
    except FileNotFoundError:
        await msg.reply_text(CAPTION, parse_mode=ParseMode.HTML, reply_markup=kb)
    await log_start(ctx.bot, update.effective_user)


async def top(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rows = overall_rows(5)
    lines = [f"{i+1}. {n} - {s} XP" for i, (n, s) in enumerate(rows)] or ["No scores yet"]
    parts = ["🌟 Overall XP\n" + "\n".join(lines)]
    for k, v in GAMES.items():
        rows = top_rows(k, 5)
        lines = [f"{i+1}. {n} - {s}" for i, (n, s) in enumerate(rows)] or ["No scores yet"]
        parts.append(f"{v['emoji']} {v['title']}\n" + "\n".join(lines))
    await update.message.reply_text("🏆 Leaderboard\n\n" + "\n\n".join(parts))


async def chat_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Chat ID: {update.effective_chat.id}")


async def couples_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not OWNER_ID or update.effective_user.id != OWNER_ID: return
    rows=q("SELECT couple_id,name1,name2,COALESCE(removed,0) FROM couples ORDER BY created DESC LIMIT 50",(),True)
    lines=[f"{x[0]} | {x[1]} + {x[2] or 'Waiting'} | {'REMOVED' if x[3] else 'ACTIVE'}" for x in rows]
    await update.message.reply_text("💑 Couple management\n\n"+("\n".join(lines) if lines else "No couples yet.")+"\n\nUse /remove CPxxxx or /restore CPxxxx")

async def remove_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not OWNER_ID or update.effective_user.id != OWNER_ID: return
    if not ctx.args: return await update.message.reply_text("Usage: /remove CPxxxx")
    cid=ctx.args[0].upper(); rows=q("SELECT user1,user2 FROM couples WHERE couple_id=?",(cid,),True)
    if not rows: return await update.message.reply_text("Couple not found.")
    q("UPDATE couples SET removed=1 WHERE couple_id=?",(cid,))
    await update.message.reply_text(f"🚫 Couple removed: {cid}")

async def restore_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not OWNER_ID or update.effective_user.id != OWNER_ID: return
    if not ctx.args: return await update.message.reply_text("Usage: /restore CPxxxx")
    cid=ctx.args[0].upper(); rows=q("SELECT user1,user2 FROM couples WHERE couple_id=?",(cid,),True)
    if not rows: return await update.message.reply_text("Couple not found.")
    q("UPDATE couples SET removed=0 WHERE couple_id=?",(cid,))
    await update.message.reply_text(f"✅ Couple restored: {cid}")

async def main():
    global BOT
    app = Application.builder().token(TOKEN).build()
    BOT = app.bot
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("top", top))
    app.add_handler(CommandHandler("id", chat_id))
    app.add_handler(CommandHandler("couples", couples_cmd))
    app.add_handler(CommandHandler("remove", remove_cmd))
    app.add_handler(CommandHandler("restore", restore_cmd))

    web_app = web.Application()
    web_app.add_routes([
        web.get("/", hub),
        web.get("/g/{name}", game_page),
        web.get("/api/games", api_games),
        web.get("/api/leaderboard", api_top),
        web.post("/api/score", api_score),
        web.post("/api/me", api_me),
        web.post("/api/court/state", court_state),
        web.post("/api/court/create", court_create),
        web.post("/api/court/join", court_join),
        web.post("/api/court/cancel", court_cancel_invite),
        web.post("/api/court/type", court_type),
        web.post("/api/court/open", court_open),
        web.post("/api/court/respond", court_respond),
        web.post("/api/court/owner/list", court_owner_list),
        web.post("/api/court/owner/status", court_owner_status),
    ])
    runner = web.AppRunner(web_app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()

    await app.initialize()
    await app.start()
    if URL:
        try:
            await app.bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="Games", web_app=WebAppInfo(url=URL)))
        except Exception as e:
            print("menu button error:", e)
    await app.updater.start_polling()
    print("Bot is running")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
