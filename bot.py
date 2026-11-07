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
    "thunderstorm": {"title": "Thunderstorm Survival", "emoji": "⚡", "file": "thunderstorm.html", "xp": 0.1},
    "spaceshooter": {"title": "Space Shooter", "emoji": "🚀", "file": "spaceshooter.html", "xp": 0.05},
    "sortcolors": {"title": "Sort the Colours", "emoji": "🧪", "file": "sortcolors.html", "xp": 0.05},
    "timberchop": {"title": "Timber Chop", "emoji": "🪓", "file": "timberchop.html", "xp": 0.5},
    "crowdcannon": {"title": "Crowd Cannon", "emoji": "💣", "file": "crowdcannon.html", "xp": 0.05},
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
else:
    _db = sqlite3.connect(DB, check_same_thread=False)

    def q(sql, args=(), fetch=False):
        cur = _db.execute(sql, args)
        _db.commit()
        return cur.fetchall() if fetch else None

    BEST = "MAX(score, excluded.score)"

q("CREATE TABLE IF NOT EXISTS scores(game TEXT, uid BIGINT, name TEXT, score BIGINT, ts BIGINT, PRIMARY KEY(game, uid))")


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


def overall_rows(n=10):
    """Best score of every game, converted to XP and added together."""
    tot, names = {}, {}
    for uid, name, game, score in q("SELECT uid, name, game, score FROM scores", (), True):
        g = GAMES.get(game)
        if not g:
            continue
        tot[uid] = tot.get(uid, 0) + int(score * g.get("xp", 0.1))
        names[uid] = name
    return sorted(((names[u], t) for u, t in tot.items()), key=lambda x: -x[1])[:n]


async def hub(req):
    return web.FileResponse(os.path.join(HERE, "hub.html"))


async def game_page(req):
    g = GAMES.get(req.match_info["name"])
    if not g:
        raise web.HTTPNotFound()
    return web.FileResponse(os.path.join(HERE, g["file"]))


async def api_games(req):
    return web.json_response([{"id": k, "title": v["title"], "emoji": v["emoji"]} for k, v in GAMES.items()])


async def api_me(req):
    try:
        b = await req.json()
    except Exception:
        b = {}
    u = verify(b.get("initData", ""))
    if not u:
        return web.json_response({"ok": False})
    return web.json_response({"ok": True, "joined": await joined(u["id"]),
                              "channel": CHANNEL_LINK, "group": GROUP_LINK})


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


async def main():
    global BOT
    app = Application.builder().token(TOKEN).build()
    BOT = app.bot
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("top", top))
    app.add_handler(CommandHandler("id", chat_id))

    web_app = web.Application()
    web_app.add_routes([
        web.get("/", hub),
        web.get("/g/{name}", game_page),
        web.get("/api/games", api_games),
        web.get("/api/leaderboard", api_top),
        web.post("/api/score", api_score),
        web.post("/api/me", api_me),
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
