PCREW COUPLE COURT - LOVE COURT UPGRADE
=======================================

WHAT WAS ADDED
- A separate Love Court section inside the existing Couple Court page.
- Eight selectable romantic prompts: First Impression, Favorite Memory, Missing You,
  Love Language, Dream Date, Little Things, Future Together, and Love Confession.
- One active Love Court challenge per couple at a time.
- Both partners submit their own private answer (1-300 characters).
- Answers remain hidden from the other partner until both have answered.
- When both answers are saved, both answers are revealed to the couple.
- Completing a challenge awards 10 shared Couple Love Points.
- Telegram notifications are sent when a challenge is created and completed, when possible.
- Love Court history is stored separately from existing court_cases.

DATABASE SAFETY
- Two new tables are created automatically: love_challenges and love_court_rewards.
- Existing couples, court_cases, punishments, scores, and game data are not deleted or rewritten.
- Keep the existing Railway database / persistent volume. Do not delete scores.db or /data.

FILES UPDATED
- bot.py
- hub.html

DEPLOYMENT
1. Back up your current project and database first.
2. Upload the updated project files to the same GitHub repository root.
3. If updating manually, replace only bot.py and hub.html.
4. Keep all existing environment variables, Railway volume, and database unchanged.
5. Commit/push and let Railway redeploy.
6. Test with two linked Telegram accounts, both set to LDR:
   a. Open Mini App > Couple Court.
   b. Partner A sends a Love Court challenge.
   c. Partner A submits an answer; it should show that it is waiting for the partner.
   d. Partner B submits an answer; both answers should then appear to both users.
   e. Check Profile to confirm shared Love Points increased by 10.

VALIDATION PERFORMED LOCALLY
- Python syntax compilation passed for bot.py, uno_game.py, and uno_module.py.
- Inline JavaScript syntax check passed for hub.html.
- Live Telegram / Railway deployment was not performed in this environment.
- Runtime import test could not be completed because python-telegram-bot is not installed in this environment.
