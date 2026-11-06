import os, json, hmac, hashlib, time, sqlite3, asyncio
from urllib.parse import parse_qsl
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, MenuButtonWebApp
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = os.environ["BOT_TOKEN"]
URL = os.environ.get("WEBAPP_URL", "").rstrip("/")
PORT = int(os.environ.get("PORT", 8080))
DB = os.environ.get("DB_PATH", "/data/scores.db" if os.path.isdir("/data") else "scores.db")
HERE = os.path.dirname(os.path.abspath(__file__))

# ဂိမ်းအသစ်ထည့်ချင်ရင် ဖိုင်တင်ပြီး ဒီစာရင်းမှာ တစ်ကြောင်း ထပ်ထည့်ပါ
GAMES = {
    "thunderstorm": {"title": "Thunderstorm Survival", "emoji": "⚡", "file": "thunderstorm.html"},
}

db = sqlite3.connect(DB, check_same_thread=False)
db.execute("CREATE TABLE IF NOT EXISTS scores(game TEXT, uid INTEGER, name TEXT, score INTEGER, ts INTEGER, PRIMARY KEY(game, uid))")
db.commit()


def verify(init_data):
    """Telegram က ပို့တဲ့ initData အစစ်ဟုတ်မဟုတ် စစ်တယ် (ရမှတ်အတုမပို့နိုင်အောင်)"""
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


def top_rows(game, n=10):
    return db.execute("SELECT name, score FROM scores WHERE game=? ORDER BY score DESC LIMIT ?", (game, n)).fetchall()


async def hub(req):
    return web.FileResponse(os.path.join(HERE, "hub.html"))


async def game_page(req):
    g = GAMES.get(req.match_info["name"])
    if not g:
        raise web.HTTPNotFound()
    return web.FileResponse(os.path.join(HERE, g["file"]))


async def api_games(req):
    return web.json_response([{"id": k, "title": v["title"], "emoji": v["emoji"]} for k, v in GAMES.items()])


async def api_top(req):
    rows = top_rows(req.query.get("game", ""))
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
    name = (u.get("first_name") or u.get("username") or "Player")[:20]
    db.execute(
        "INSERT INTO scores VALUES(?,?,?,?,?) ON CONFLICT(game,uid) DO UPDATE SET "
        "name=excluded.name, score=MAX(score, excluded.score), ts=excluded.ts",
        (g, u["id"], name, s, int(time.time())),
    )
    db.commit()
    return web.json_response({"ok": True})


def play_button():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🎮 ဂိမ်းဆော့မယ်", web_app=WebAppInfo(url=URL))]])


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not URL:
        await update.message.reply_text("WEBAPP_URL မသတ်မှတ်ရသေးပါ")
        return
    await update.message.reply_text("🎮 ဂိမ်းတွေ ဆော့ရအောင်!", reply_markup=play_button())


async def top(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    parts = []
    for k, v in GAMES.items():
        rows = top_rows(k, 5)
        lines = [f"{i+1}. {n} - {s}" for i, (n, s) in enumerate(rows)] or ["မရှိသေးပါ"]
        parts.append(f"{v['emoji']} {v['title']}\n" + "\n".join(lines))
    await update.message.reply_text("🏆 Leaderboard\n\n" + "\n\n".join(parts))


async def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("top", top))

    web_app = web.Application()
    web_app.add_routes([
        web.get("/", hub),
        web.get("/g/{name}", game_page),
        web.get("/api/games", api_games),
        web.get("/api/leaderboard", api_top),
        web.post("/api/score", api_score),
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
