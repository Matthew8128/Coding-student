"""
WordPress 최적화 스크립트
1단계: 현황 조회
2단계: 카테고리 정리 및 메뉴 연결
3단계: 자동포스팅 글 SEO 최적화
"""
import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

WP_URL = os.getenv("WP_URL", "").rstrip("/")
WP_USER = os.getenv("WP_USERNAME")
WP_PASS = os.getenv("WP_APP_PASSWORD")
AUTH = (WP_USER, WP_PASS)
BASE = f"{WP_URL}/wp-json/wp/v2"


# ─────────────────────────────────────────────
# 1단계: 현황 조회
# ─────────────────────────────────────────────
def step1_audit():
    print("\n" + "=" * 60)
    print("1단계: WordPress 현황 조회")
    print("=" * 60)

    # 카테고리 조회
    res = requests.get(f"{BASE}/categories", params={"per_page": 100}, auth=AUTH)
    categories = res.json()
    print(f"\n[카테고리 목록] 총 {len(categories)}개")
    for c in sorted(categories, key=lambda x: -x["count"]):
        print(f"  ID:{c['id']:4d} | {c['name']:<30} | slug: {c['slug']:<25} | 글수: {c['count']}")

    # 태그 조회
    res = requests.get(f"{BASE}/tags", params={"per_page": 100, "orderby": "count", "order": "desc"}, auth=AUTH)
    tags = res.json()
    print(f"\n[태그 목록] 총 {len(tags)}개 (상위 10개)")
    for t in tags[:10]:
        print(f"  ID:{t['id']:4d} | {t['name']:<30} | 글수: {t['count']}")

    # 전체 글 조회
    res = requests.get(f"{BASE}/posts", params={"per_page": 100, "status": "publish"}, auth=AUTH)
    posts = res.json()
    total = int(res.headers.get("X-WP-Total", len(posts)))
    print(f"\n[게시글 목록] 총 {total}개 (최근 {len(posts)}개)")
    for p in posts:
        cats = [c["name"] for c in p.get("_embedded", {}).get("wp:term", [[]])[0]] if "_embedded" in p else []
        print(f"  ID:{p['id']:5d} | {p['title']['rendered'][:45]:<45} | {p['date'][:10]}")

    # 메뉴 조회
    res = requests.get(f"{WP_URL}/wp-json/wp/v2/menus", auth=AUTH)
    if res.status_code == 200:
        menus = res.json()
        print(f"\n[메뉴 목록] 총 {len(menus)}개")
        for m in menus:
            print(f"  ID:{m['id']} | {m['name']} | slug: {m['slug']}")
    else:
        print(f"\n[메뉴] REST API 메뉴 접근 불가 (status: {res.status_code}) - 테마에서 직접 관리 필요")

    return categories, posts


# ─────────────────────────────────────────────
# 2단계: 카테고리 정리
# ─────────────────────────────────────────────
def step2_categories(categories):
    print("\n" + "=" * 60)
    print("2단계: 카테고리 정리")
    print("=" * 60)

    existing_slugs = {c["slug"]: c for c in categories}

    # 필요한 카테고리 구조 정의 (광고수익 최적화 기준)
    required_categories = [
        {"name": "AI/기술 트렌드", "slug": "ai-tech-trend", "description": "매주 AI·기술 업계 주요 이슈 분석"},
        {"name": "GPT 자동화", "slug": "gpt-auto-posting", "description": "GPT를 활용한 자동화 실험 기록"},
        {"name": "수익화 실험", "slug": "monetization", "description": "블로그·디지털 수익화 도전기"},
        {"name": "작은 실천", "slug": "small-action", "description": "일상 속 작은 변화와 실천 기록"},
        {"name": "퀘스트 일지", "slug": "quest-log", "description": "더 나은 삶을 향한 퀘스트 진행 일지"},
    ]

    category_map = {}  # slug -> id

    for cat in required_categories:
        slug = cat["slug"]
        if slug in existing_slugs:
            cid = existing_slugs[slug]["id"]
            print(f"  [유지] {cat['name']} (ID:{cid})")
            category_map[slug] = cid
        else:
            res = requests.post(
                f"{BASE}/categories",
                json={"name": cat["name"], "slug": slug, "description": cat["description"]},
                auth=AUTH,
            )
            if res.status_code == 201:
                cid = res.json()["id"]
                print(f"  [생성] {cat['name']} (ID:{cid})")
                category_map[slug] = cid
            else:
                print(f"  [오류] {cat['name']} 생성 실패: {res.text[:100]}")

    return category_map


# ─────────────────────────────────────────────
# 3단계: SEO 최적화 (자동포스팅 글 개선)
# ─────────────────────────────────────────────
def step3_seo_optimize(posts, category_map):
    print("\n" + "=" * 60)
    print("3단계: 자동포스팅 글 SEO 최적화")
    print("=" * 60)

    # gpt-auto-posting 카테고리 글만 대상
    target_cat_id = category_map.get("gpt-auto-posting") or category_map.get("ai-tech-trend")
    if not target_cat_id:
        print("  [스킵] 대상 카테고리 없음")
        return

    res = requests.get(
        f"{BASE}/posts",
        params={"per_page": 100, "status": "publish", "categories": target_cat_id},
        auth=AUTH,
    )
    target_posts = res.json()
    print(f"  대상 글: {len(target_posts)}개")

    for post in target_posts:
        pid = post["id"]
        title = post["title"]["rendered"]
        content = post["content"]["rendered"]

        updates = {}

        # 1. excerpt (요약) 없으면 추가
        if not post.get("excerpt", {}).get("rendered", "").strip():
            # 콘텐츠에서 첫 150자 추출
            import re
            plain = re.sub(r"<[^>]+>", "", content)[:150].strip()
            updates["excerpt"] = plain
            print(f"  [excerpt 추가] ID:{pid} - {title[:30]}...")

        # 2. AI/기술 관련 태그 추가
        existing_tags = post.get("tags", [])
        if not existing_tags:
            tag_names = ["AI트렌드", "기술뉴스", "해커뉴스", "주간기술", "인공지능"]
            tag_ids = []
            for tag_name in tag_names:
                # 태그 생성 또는 조회
                res_tag = requests.get(f"{BASE}/tags", params={"search": tag_name}, auth=AUTH)
                found = res_tag.json()
                if found:
                    tag_ids.append(found[0]["id"])
                else:
                    res_create = requests.post(f"{BASE}/tags", json={"name": tag_name}, auth=AUTH)
                    if res_create.status_code == 201:
                        tag_ids.append(res_create.json()["id"])
            if tag_ids:
                updates["tags"] = tag_ids
                print(f"  [태그 추가] ID:{pid} - {tag_names}")

        # 3. 카테고리를 ai-tech-trend로 업데이트 (gpt-auto-posting → ai-tech-trend)
        ai_cat_id = category_map.get("ai-tech-trend")
        if ai_cat_id and ai_cat_id not in post.get("categories", []):
            updates["categories"] = list(set(post.get("categories", []) + [ai_cat_id]))
            print(f"  [카테고리 추가] ID:{pid} - ai-tech-trend 추가")

        # 업데이트 실행
        if updates:
            res_update = requests.post(f"{BASE}/posts/{pid}", json=updates, auth=AUTH)
            if res_update.status_code == 200:
                print(f"  [완료] ID:{pid} 업데이트 성공")
            else:
                print(f"  [오류] ID:{pid} 업데이트 실패: {res_update.text[:100]}")
        else:
            print(f"  [스킵] ID:{pid} - 이미 최적화됨")


# ─────────────────────────────────────────────
# 메인 실행
# ─────────────────────────────────────────────
def run_optimization():
    print("\n🚀 WordPress 최적화 시작")
    print(f"   대상: {WP_URL}")

    try:
        categories, posts = step1_audit()
        category_map = step2_categories(categories)
        step3_seo_optimize(posts, category_map)
        print("\n✅ 모든 최적화 작업 완료!")
    except Exception as e:
        print(f"\n❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    run_optimization()
