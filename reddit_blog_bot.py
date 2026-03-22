import os
import praw
import google.generativeai as genai
import requests
import schedule
import time
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# --- 클라이언트 초기화 ---
reddit = praw.Reddit(
    client_id=os.getenv("REDDIT_CLIENT_ID"),
    client_secret=os.getenv("REDDIT_CLIENT_SECRET"),
    user_agent=os.getenv("REDDIT_USER_AGENT", "blog-bot/1.0"),
)

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel(model_name="gemini-1.5-flash")

WP_URL = os.getenv("WP_URL")
WP_USER = os.getenv("WP_USERNAME")
WP_PASS = os.getenv("WP_APP_PASSWORD")


def get_top_posts(subreddits: list[str], limit: int = 10) -> list[dict]:
    """여러 서브레딧에서 주간 인기 게시글 수집"""
    posts = []
    for sub in subreddits:
        for post in reddit.subreddit(sub).top(time_filter="week", limit=limit):
            posts.append({
                "subreddit": sub,
                "title": post.title,
                "score": post.score,
                "url": post.url,
                "selftext": post.selftext[:500] if post.selftext else "",
                "num_comments": post.num_comments,
            })
    # 점수 기준 정렬 후 상위 5개
    posts.sort(key=lambda x: x["score"], reverse=True)
    return posts[:5]


def generate_blog_post(posts: list[dict]) -> dict:
    """Gemini API로 한국어 블로그 게시글 생성"""
    posts_text = "\n\n".join([
        f"{i+1}. [{p['subreddit']}] {p['title']}\n"
        f"   추천수: {p['score']:,} | 댓글: {p['num_comments']:,}\n"
        f"   링크: {p['url']}\n"
        f"   내용: {p['selftext']}"
        for i, p in enumerate(posts)
    ])

    prompt = f"""아래는 이번 주 Reddit r/technology와 r/artificial 에서 가장 주목받은 게시글 5개입니다.

{posts_text}

이를 바탕으로 한국어 블로그 게시글을 작성해주세요. 형식은 아래와 같습니다:

1. **제목**: 이번 주 AI/기술 트렌드 요약 (날짜 포함, 예: 2025년 1월 1주차)
2. **도입부**: 이번 주 전반적인 트렌드 2-3문장 요약
3. **TOP 5 이슈**: 각 게시글을 한국어로 요약 (각 150자 내외)
4. **트렌드 분석**: 이번 주 이슈들의 공통점, 시사점, 앞으로의 전망 (300자 내외)
5. **마무리**: 한 줄 총평

HTML 형식으로 작성해주세요 (WordPress에 바로 게시할 수 있도록).
제목은 <title> 태그로, 본문은 <content> 태그로 감싸주세요."""

    response = model.generate_content(prompt)
    raw = response.text

    title = raw.split("<title>")[1].split("</title>")[0].strip() if "<title>" in raw else f"이번 주 AI/기술 트렌드 - {datetime.now().strftime('%Y년 %m월')}"
    content = raw.split("<content>")[1].split("</content>")[0].strip() if "<content>" in raw else raw

    return {"title": title, "content": content}


def post_to_wordpress(title: str, content: str) -> bool:
    """WordPress REST API로 게시글 업로드"""
    endpoint = f"{WP_URL.rstrip('/')}/wp-json/wp/v2/posts"
    payload = {
        "title": title,
        "content": content,
        "status": "publish",
        "categories": [],
        "tags": [],
    }
    response = requests.post(
        endpoint,
        json=payload,
        auth=(WP_USER, WP_PASS),
    )
    if response.status_code == 201:
        post_url = response.json().get("link", "")
        print(f"[성공] 게시글 업로드 완료: {post_url}")
        return True
    else:
        print(f"[오류] WordPress 업로드 실패: {response.status_code} - {response.text}")
        return False


def run_weekly_job():
    """매주 실행되는 메인 작업"""
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M')}] 주간 블로그 자동화 시작...")

    print("Reddit 인기 게시글 수집 중...")
    posts = get_top_posts(["technology", "artificial"])

    print("Gemini API로 블로그 게시글 생성 중...")
    blog = generate_blog_post(posts)

    print("WordPress에 업로드 중...")
    post_to_wordpress(blog["title"], blog["content"])


if __name__ == "__main__":
    print("블로그 자동화 봇 시작")
    print("매주 월요일 오전 9시에 자동 실행됩니다.\n")

    schedule.every().monday.at("09:00").do(run_weekly_job)

    # 시작 시 즉시 1회 실행하려면 아래 주석 해제
    # run_weekly_job()

    while True:
        schedule.run_pending()
        time.sleep(60)
