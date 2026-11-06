import os, json, hmac, hashlib, time, sqlite3, asyncio
from urllib.parse import parse_qsl
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, MenuButtonWebApp
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = os.environ["BOT_TOKEN"].strip()
URL = "".join(os.environ.get("WEBAPP_URL", "").split()).rstrip("/")  # space တွေ အကုန်ဖယ်
if URL and not URL.startswith("http"):
    URL = "https://" + URL
PORT = int(os.environ.get("PORT", 8080))
DB = os.environ.get("DB_PATH", "/data/scores.db" if os.path.isdir("/data") else "scores.db")
HERE = os.path.dirname(os.path.abspath(__file__))

# ဂိမ်းအသစ်ထည့်ချင်ရင် ဖိုင်တင်ပြီး ဒီစာရင်းမှာ တစ်ကြောင်း ထပ်ထည့်ပါ
# xp = ရမှတ် ၁ မှတ်ကို XP ဘယ်လောက်ပေးမလဲ (Overall Leaderboard အတွက်၊ 0.1 = ရမှတ် ၁၀၀ က XP ၁၀)
GAMES = {
    "thunderstorm": {"title": "Thunderstorm Survival", "emoji": "⚡", "file": "thunderstorm.html", "xp": 0.1},
}

# DATABASE_URL ရှိရင် Postgres (ဘယ်တော့မှ မပျောက်), မရှိရင် SQLite သုံးမယ်
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
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
    return q("SELECT name, score FROM scores WHERE game=? ORDER BY score DESC LIMIT ?", (game, n), True)


def overall_rows(n=10):
    """ဂိမ်းအားလုံးရဲ့ အမြင့်ဆုံးရမှတ်ကို XP ပြောင်းပြီး ပေါင်းတယ်"""
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
    name = (u.get("first_name") or u.get("username") or "Player")[:20]
    q(
        "INSERT INTO scores VALUES(?,?,?,?,?) ON CONFLICT(game,uid) DO UPDATE SET "
        "name=excluded.name, score=" + BEST + ", ts=excluded.ts",
        (g, u["id"], name, s, int(time.time())),
    )
    return web.json_response({"ok": True})


def play_button():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🎮 ဂိမ်းဆော့မယ်", web_app=WebAppInfo(url=URL))]])


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not URL:
        await update.message.reply_text("WEBAPP_URL မသတ်မှတ်ရသေးပါ")
        return
    await update.message.reply_text("🎮 ဂိမ်းတွေ ဆော့ရအောင်!", reply_markup=play_button())


async def top(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    orows = overall_rows(5)
    olines = [f"{i+1}. {n} - {s} XP" for i, (n, s) in enumerate(orows)] or ["မရှိသေးပါ"]
    parts = ["🌟 Overall XP\n" + "\n".join(olines)]
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
