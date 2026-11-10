PCREW Couple Court - Partner Profile Fix

Only bot.py is included in this patch.

Update steps:
1. Open your GitHub repository.
2. Replace the existing root-level bot.py with this bot.py.
3. Commit the change and let Railway redeploy.
4. Ask your partner to open the Mini App once, then reopen/refresh the Profile tab.

What this changes:
- Saves each linked member's Telegram username and photo_url whenever they open the Mini App.
- Refreshes the partner's Telegram username/profile photo through the Telegram Bot API when available.
- Keeps the existing database and all game files; database columns are updated additively by the existing migration logic.

Important:
- Do not delete or replace your database.
- If Telegram does not provide the profile photo to the Mini App and the Bot API cannot access the partner's profile, the app will keep showing the default avatar until the partner opens the Mini App and Telegram supplies photo_url.
