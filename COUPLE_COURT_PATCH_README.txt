PCREW Couple Court - Targeted Update

Replace ONLY these two files in your existing GitHub project root:
- bot.py
- hub.html

Do not delete or replace your database, other game files, images, icons, or other project assets.

This patch updates the Court verdict message to use Telegram HTML blockquote formatting, a case ID, and the actual plaintiff/defendant Telegram usernames. If a user has no Telegram username, their saved Telegram first name is displayed instead. Court case history and the in-app result view also show both parties.

After replacing these two files, commit/push to GitHub and let Railway redeploy. Keep your existing Railway variables and persistent volume/database unchanged.
