import os
import re
import requests
from google import genai
from youtube_transcript_api import YouTubeTranscriptApi
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
WP_URL = os.getenv("WP_URL")
WP_USER = os.getenv("WP_USERNAME")
WP_PASS = os.getenv("WP_APP_PASSWORD")


def extract_video_id(url: str) -> str:
    """YouTube URL에서 비디오 ID 추출"""
    patterns = [
        r'(?:youtube\.com/watch\?v=)([a-zA-Z0-9_-]{11})',
        r'(?:youtu\.be/)([a-zA-Z0-9_-]{11})',
        r'(?:youtube\.com/embed/)([a-zA-Z0-9_-]{11})',
        r'(?:youtube\.com/shorts/)([a-zA-Z0-9_-]{11})',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    raise ValueError(f"YouTube URL에서 비디오 ID를 찾을 수 없습니다: {url}")


def get_video_info(video_id: str) -> dict:
    """YouTube oEmbed로 영상 제목/채널 가져오기 (API 키 불필요)"""
    try:
        r = requests.get(
            f"https://www.youtube.com/oembed",
            params={"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            return {"title": data.get("title", ""), "author": data.get("author_name", "")}
    except Exception:
        pass
    return {"title": "", "author": ""}


def get_transcript(video_id: str) -> str:
    """YouTube 자막 추출 (한국어 우선 → 영어 → 자동생성)"""
    transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)

    # 우선순위: 수동 한국어 → 수동 영어 → 자동생성 한국어 → 자동생성 영어
    for lang in ["ko", "en"]:
        try:
            t = transcript_list.find_manually_created_transcript([lang])
            entries = t.fetch()
            return " ".join(e["text"] for e in entries)
        except Exception:
            pass

    for lang in ["ko", "en"]:
        try:
            t = transcript_list.find_generated_transcript([lang])
            entries = t.fetch()
            return " ".join(e["text"] for e in entries)
        except Exception:
            pass

    raise ValueError("이 영상은 자막이 없어서 요약할 수 없습니다.")


def summarize_with_gemini(transcript: str, video_info: dict, video_url: str) -> dict:
    """Gemini로 요약 및 블로그 글 생성"""
    now = datetime.now()
    title_text = video_info.get("title", "제목 없음")
    author_text = video_info.get("author", "")

    prompt = f"""다음은 YouTube 영상의 자막입니다.

영상 제목: {title_text}
채널: {author_text}
영상 URL: {video_url}
오늘 날짜: {now.strftime('%Y년 %m월 %d일')}

자막 내용:
{transcript[:8000]}

위 내용을 바탕으로 "삶을 바꾸는 작은 실천" 블로그 카테고리에 올릴 한국어 게시글을 작성해주세요.

형식:
1. 도입부: 이 영상이 왜 중요한지 2-3문장
2. 핵심 내용: 영상의 주요 포인트 3-5개 (각 100-150자)
3. 실천 방법: 오늘 당장 실천할 수 있는 구체적인 행동 2-3개
4. 마무리: 독자에게 동기부여가 되는 한 문장
5. 원본 영상 링크 포함

HTML 형식으로 작성해주세요 (WordPress에 바로 게시할 수 있도록).
제목은 <title> 태그로, 본문은 <content> 태그로 감싸주세요."""

    model = os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-flash")
    response = gemini_client.models.generate_content(model=model, contents=prompt)
    raw = response.text

    if "<title>" in raw and "</title>" in raw:
        title = raw.split("<title>")[1].split("</title>")[0].strip()
    else:
        title = title_text or "YouTube 영상 요약"

    if "<content>" in raw and "</content>" in raw:
        content = raw.split("<content>")[1].split("</content>")[0].strip()
    else:
        content = raw

    return {"title": title, "content": content}


def get_or_create_category(slug: str, name: str) -> int:
    """WordPress 카테고리 ID 조회, 없으면 생성"""
    endpoint = f"{WP_URL.rstrip('/')}/wp-json/wp/v2/categories"
    r = requests.get(endpoint, params={"slug": slug}, auth=(WP_USER, WP_PASS), timeout=10)
    if r.status_code == 200 and r.json():
        return r.json()[0]["id"]

    r = requests.post(
        endpoint,
        json={"name": name, "slug": slug},
        auth=(WP_USER, WP_PASS),
        timeout=10,
    )
    if r.status_code == 201:
        return r.json()["id"]
    return 1  # fallback: 미분류


def post_to_wordpress(title: str, content: str) -> dict:
    """WordPress '삶을 바꾸는 작은 실천' 카테고리에 포스팅"""
    category_id = get_or_create_category("small-action", "삶을 바꾸는 작은 실천")

    endpoint = f"{WP_URL.rstrip('/')}/wp-json/wp/v2/posts"
    payload = {
        "title": title,
        "content": content,
        "status": "publish",
        "categories": [category_id],
    }
    r = requests.post(endpoint, json=payload, auth=(WP_USER, WP_PASS), timeout=15)
    if r.status_code == 201:
        return {"success": True, "url": r.json().get("link", "")}
    return {"success": False, "error": r.text[:300]}


def process_youtube_url(url: str) -> dict:
    """YouTube URL → 자막 추출 → Gemini 요약 → WordPress 포스팅"""
    video_id = extract_video_id(url)
    video_info = get_video_info(video_id)
    transcript = get_transcript(video_id)
    post_data = summarize_with_gemini(transcript, video_info, url)
    result = post_to_wordpress(post_data["title"], post_data["content"])
    return {**result, "title": post_data["title"]}
