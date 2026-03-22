import threading
import time
import schedule
from reddit_blog_bot import run_weekly_job
from telegram_claude_bot import main as telegram_main


def run_reddit_scheduler():
    schedule.every().monday.at("09:00").do(run_weekly_job)
    while True:
        schedule.run_pending()
        time.sleep(60)


reddit_thread = threading.Thread(target=run_reddit_scheduler, daemon=True)
reddit_thread.start()

telegram_main()
