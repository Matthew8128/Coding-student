import threading
import time
import schedule
from reddit_blog_bot import run_weekly_job
from telegram_claude_bot import main as telegram_main
from wp_optimizer import run_optimization


def run_reddit_scheduler():
    schedule.every().monday.at("09:00").do(run_weekly_job)
    while True:
        schedule.run_pending()
        time.sleep(60)


# WordPress 최적화 (1회 실행)
run_optimization()

# 블로그 글 즉시 1회 포스팅
run_weekly_job()

reddit_thread = threading.Thread(target=run_reddit_scheduler, daemon=True)
reddit_thread.start()

telegram_main()
