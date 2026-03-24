import os
from google import genai
import requests
import schedule
import time
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# --- 클라이언트 초기화 ---
HN_API = "https://hacker-news.firebaseio.com/v0"

gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

WP_URL = os.getenv("WP_URL")
WP_USER = os.getenv("WP_USERNAME")
WP_PASS = os.getenv("WP_APP_PASSWORD")


def get_top_posts(limit: int = 5) -> list[dict]:
    """Hacker News에서 인기 게시글 수집"""
    response = requests.get(f"{HN_API}/topstories.json", timeout=10)
    if response.status_code != 200:
        print(f"[경고] Hacker News 데이터 수집 실패: {response.status_code}")
        return []

    story_ids = response.json()[:30]
    posts = []
    for story_id in story_ids:
        r = requests.get(f"{HN_API}/item/{story_id}.json", timeout=10)
        if r.status_code != 200:
            continue
        item = r.json()
        if not item or item.get("type") != "story" or not item.get("title"):
            continue
        posts.append({
            "source": "Hacker News",
            "title": item.get("title", ""),
            "score": item.get("score", 0),
            "url": item.get("url", f"https://news.ycombinator.com/item?id={story_id}"),
            "num_comments": item.get("descendants", 0),
        })
        if len(posts) >= limit:
            break

    posts.sort(key=lambda x: x["score"], reverse=True)
    return posts[:limit]


def generate_blog_post(posts: list[dict]) -> dict:
    """Gemini API로 한국어 블로그 게시글 생성"""
    now = datetime.now()
    week_num = (now.day - 1) // 7 + 1
    current_date_str = f"{now.year}년 {now.month}월 {week_num}주차"

    posts_text = "\n\n".join([
        f"{i+1}. {p['title']}\n"
        f"   추천수: {p['score']:,} | 댓글: {p['num_comments']:,}\n"
        f"   링크: {p['url']}"
        for i, p in enumerate(posts)
    ])

    prompt = f"""아래는 이번 주 Hacker News에서 가장 주목받은 기술/AI 게시글 5개입니다.
오늘 날짜: {now.strftime('%Y년 %m월 %d일')}

{posts_text}

이를 바탕으로 한국어 블로그 게시글을 작성해주세요. 형식은 아래와 같습니다:

1. **제목**: 이번 주 AI/기술 트렌드 요약 ({current_date_str})
2. **도입부**: 이번 주 전반적인 트렌드 2-3문장 요약
3. **TOP 5 이슈**: 각 게시글을 한국어로 요약 (각 150자 내외)
4. **트렌드 분석**: 이번 주 이슈들의 공통점, 시사점, 앞으로의 전망 (300자 내외)
5. **마무리**: 한 줄 총평

HTML 형식으로 작성해주세요 (WordPress에 바로 게시할 수 있도록).
제목은 <title> 태그로, 본문은 <content> 태그로 감싸주세요."""

    model = os.getenv("GEMINI_MODEL_NAME", "gemini-1.5-flash")
    response = gemini_client.models.generate_content(model=model, contents=prompt)
    raw = response.text

    title = raw.split("<title>")[1].split("</title>")[0].strip() if "<title>" in raw else f"이번 주 AI/기술 트렌드 - {datetime.now().strftime('%Y년 %m월')}"
    content = raw.split("<content>")[1].split("</content>")[0].strip() if "<content>" in raw else raw

    return {"title": title, "content": content}


def get_category_id(slug: str) -> int | None:
    """슬러그로 WordPress 카테고리 ID 조회"""
    endpoint = f"{WP_URL.rstrip('/')}/wp-json/wp/v2/categories"
    response = requests.get(endpoint, params={"slug": slug}, auth=(WP_USER, WP_PASS))
    if response.status_code == 200:
        data = response.json()
        if data:
            return data[0]["id"]
    return None


def post_to_wordpress(title: str, content: str) -> bool:
    """WordPress REST API로 게시글 업로드"""
    endpoint = f"{WP_URL.rstrip('/')}/wp-json/wp/v2/posts"

    category_id = get_category_id("gpt-auto-posting")
    categories = [category_id] if category_id else []

    payload = {
        "title": title,
        "content": content,
        "status": "publish",
        "categories": categories,
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

    print("Hacker News 인기 게시글 수집 중...")
    posts = get_top_posts()

    if not posts:
        print("[오류] 데이터 수집 실패 - 게시글이 없습니다. 종료합니다.")
        return

    print("Gemini API로 블로그 게시글 생성 중...")
    blog = generate_blog_post(posts)

    print("WordPress에 업로드 중...")
    post_to_wordpress(blog["title"], blog["content"])


if __name__ == "__main__":
    print("블로그 자동화 봇 시작")
    print("매주 월요일 오전 9시에 자동 실행됩니다.\n")

    schedule.every().monday.at("09:00").do(run_weekly_job)

    while True:
        schedule.run_pending()
        time.sleep(60)
