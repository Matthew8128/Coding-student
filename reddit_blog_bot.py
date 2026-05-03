import os
import re
from google import genai
from google.genai import types
import requests
import time
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# --- 클라이언트 및 환경변수 ---
HN_API = "https://hacker-news.firebaseio.com/v0"
gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

WP_URL = os.getenv("WP_URL", "").rstrip("/")
WP_USER = os.getenv("WP_USERNAME")
WP_PASS = os.getenv("WP_APP_PASSWORD")

# 1. 워드프레스 카테고리 매핑 (스샷 반영)
CATEGORIES = {
    "tech": "AI·기술 트렌드",
    "productivity": "삶을 바꾸는 작은 실천",
    "money": "Quest for better"
}

# 2. 강력한 블로그 작성 지침 (포커스 키워드 파싱 추가)
BLOG_SYSTEM_PROMPT = """당신은 대한민국 상위 1% 블로거이자 콘텐츠 전략가입니다.
네이버/구글 검색 상위노출, 체류시간, 클릭률을 동시에 극대화하는 블로그 포스트를 작성합니다.

【작성 원칙】
1. 첫 3줄: 독자의 현실 고통을 찌르는 훅. "안녕하세요" 류 인사말 절대 금지.
2. 구조: 훅 → 공감 브리지 → 문제 정의 → 해결책/인사이트 3~5개 → 핵심 요약 → CTA
3. 공감 문장: 각 소제목 전후에 1인칭 경험담 또는 독자 상황 묘사 삽입
4. 정보 + 감정 + 경험: 세 가지를 1:1:1 비율로 혼합
5. 타겟: 30~40대 직장인. 전문용어 최소화, 구어체, 문단 3줄 이하
6. 연도: 반드시 2026년 기준 최신 트렌드로 해석 및 반영
7. 스크롤 유도: 소제목마다 다음 섹션이 궁금하게 만드는 브리지 문장 포함
8. 분량: 본문 1,500자 이상 (HTML 태그 제외)

【AI 생성 티 제거 — 이 표현들 절대 사용 금지】
- "~라고 합니다", "~될 것으로 기대됩니다", "~라고 할 수 있습니다"
- "안녕하세요", "이번 포스팅에서는", "오늘은 ~에 대해 알아보겠습니다"
- 마크다운 기호 ##, ** — HTML 태그만 사용

【출력 형식 — 반드시 아래 태그를 사용하여 출력할 것】
<POST_TITLE>클릭하고 싶은 제목</POST_TITLE>
<FOCUS_KEYWORD>검색 상위 노출을 위한 핵심 타겟 키워드 1개</FOCUS_KEYWORD>
<POST_EXCERPT>SEO 요약 1~2문장 (150자 내외)</POST_EXCERPT>
<POST_TAGS>태그1,태그2,태그3</POST_TAGS>
<POST_CONTENT>
(HTML 본문 전체)
</POST_CONTENT>"""


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
            "title": item.get("title", ""),
            "score": item.get("score", 0),
            "url": item.get("url", f"https://news.ycombinator.com/item?id={story_id}"),
        })
        if len(posts) >= limit:
            break

    posts.sort(key=lambda x: x["score"], reverse=True)
    return posts[:limit]


def generate_blog_post(posts: list[dict], category_key: str) -> dict:
    """Gemini API로 규격화된 한국어 블로그 게시글 및 메타데이터 생성"""
    now = datetime.now()
    category_name = CATEGORIES.get(category_key, "AI·기술 트렌드")
    
    posts_text = "\n\n".join([
        f"{i+1}. {p['title']}\n   추천수: {p['score']} | 링크: {p['url']}"
        for i, p in enumerate(posts)
    ])

    user_message = f"""오늘 날짜: {now.strftime('%Y년 %m월 %d일')}
발행 카테고리: {category_name}

아래 제공된 최신 이슈 5가지를 바탕으로, 이 카테고리와 타겟 독자에 완벽하게 맞는 블로그 포스트를 작성해주세요.

== 수집된 이슈 ==
{posts_text}"""

    print("Gemini API 호출 중 (System Prompt 적용)...")
    model_name = os.getenv("GEMINI_MODEL_NAME", "gemini-1.5-flash")
    
    response = gemini_client.models.generate_content(
        model=model_name,
        contents=user_message,
        config=types.GenerateContentConfig(
            system_instruction=BLOG_SYSTEM_PROMPT,
            temperature=0.7
        )
    )
    raw = response.text

    # 정규식(Regex)을 이용한 태그 파싱
    def extract_tag(text: str, tag: str) -> str:
        pattern = rf"<{tag}>(.*?)</{tag}>"
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        return match.group(1).strip() if match else ""

    return {
        "title": extract_tag(raw, "POST_TITLE") or f"{category_name} 트렌드 요약",
        "focus_keyword": extract_tag(raw, "FOCUS_KEYWORD"),
        "excerpt": extract_tag(raw, "POST_EXCERPT"),
        "tags": extract_tag(raw, "POST_TAGS"),
        "content": extract_tag(raw, "POST_CONTENT") or raw,
    }


def get_wp_category_id(category_name: str) -> int | None:
    """이름으로 WordPress 카테고리 ID 조회 (없으면 생성)"""
    endpoint = f"{WP_URL}/wp-json/wp/v2/categories"
    response = requests.get(endpoint, auth=(WP_USER, WP_PASS))
    
    if response.status_code == 200:
        for cat in response.json():
            if cat["name"] == category_name:
                return cat["id"]
                
    # 카테고리가 없으면 새로 생성
    new_cat_resp = requests.post(endpoint, json={"name": category_name}, auth=(WP_USER, WP_PASS))
    if new_cat_resp.status_code == 201:
        return new_cat_resp.json()["id"]
        
    return None


def post_to_wordpress(blog_data: dict, category_key: str) -> bool:
    """WordPress REST API로 게시글 업로드 (SEO 메타데이터 포함)"""
    endpoint = f"{WP_URL}/wp-json/wp/v2/posts"

    category_name = CATEGORIES.get(category_key, "AI·기술 트렌드")
    category_id = get_wp_category_id(category_name)
    category_ids = [category_id] if category_id else []

    # SEO 플러그인용 포커스 키워드 메타 데이터 (Rank Math / Yoast SEO 공용 세팅)
    meta_data = {}
    if blog_data["focus_keyword"]:
        meta_data["rank_math_focus_keyword"] = blog_data["focus_keyword"]
        meta_data["_yoast_wpseo_focuskw"] = blog_data["focus_keyword"]

    payload = {
        "title": blog_data["title"],
        "content": blog_data["content"],
        "excerpt": blog_data["excerpt"],
        "status": "publish", # 또는 "draft"
        "categories": category_ids,
        "meta": meta_data # 포커스 키워드 주입
    }

    response = requests.post(endpoint, json=payload, auth=(WP_USER, WP_PASS))
    
    if response.status_code == 201:
        post_url = response.json().get("link", "")
        print(f"[성공] 카테고리 '{category_name}'에 게시 완료: {post_url}")
        print(f" └ 포커스 키워드: {blog_data['focus_keyword']}")
        return True
    else:
        print(f"[오류] WordPress 업로드 실패: {response.status_code} - {response.text}")
        return False


def run_job(category_key: str = "tech"):
    """단일 실행기: 카테고리를 지정받아 실행"""
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M')}] '{CATEGORIES[category_key]}' 카테고리 작업 시작...")

    posts = get_top_posts()
    if not posts:
        print("데이터 수집 실패. 종료합니다.")
        return

    blog_data = generate_blog_post(posts, category_key)
    post_to_wordpress(blog_data, category_key)


if __name__ == "__main__":
    import sys

    print("=== 블로그 자동 포스팅 파이프라인 ===")
    print("사용 가능한 카테고리 인자: tech, productivity, money\n")

    # 커맨드라인에서 카테고리를 입력받아 실행 (예: python script.py money)
    target_category = "tech" # 기본값
    if len(sys.argv) > 1 and sys.argv[1] in CATEGORIES:
        target_category = sys.argv[1]
    
    run_job(target_category)
