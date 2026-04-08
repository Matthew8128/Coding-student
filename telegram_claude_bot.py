import os
import asyncio
import tempfile
from google import genai
from google.genai import types
import anthropic
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from dotenv import load_dotenv
from youtube_summarizer import process_youtube_url

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
# 보안: 허용할 텔레그램 유저 ID (쉼표로 여러 명 가능, 비워두면 전체 허용)
ALLOWED_USER_IDS = set(
    uid.strip() for uid in os.getenv("TELEGRAM_ALLOWED_USER_IDS", "").split(",") if uid.strip()
)

SYSTEM_PROMPT = """당신은 도움이 되는 개인 비서입니다.
한국어로 친근하게 답변해주세요.
문서 초안 작성, 파일 내용 분석, 정보 정리, 아이디어 제안 등 다양한 작업을 도와드립니다.
답변이 길어질 경우 핵심 내용을 먼저 말하고 상세 내용은 이후에 설명해주세요."""

gemini_client = genai.Client(api_key=GEMINI_API_KEY)
claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None

# 유저별 대화 기록 저장 (메모리 기반, 재시작 시 초기화)
conversation_history: dict[str, list] = {}


def is_allowed(user_id: int) -> bool:
    if not ALLOWED_USER_IDS:
        return True
    return str(user_id) in ALLOWED_USER_IDS


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "안녕하세요! Gemini 개인 비서입니다 👋\n\n"
        "메시지를 보내면 답변해드립니다.\n"
        "텍스트 파일을 첨부하면 내용을 읽고 분석해드립니다.\n\n"
        "명령어:\n"
        "/youtube [URL] — 유튜브 영상 요약 후 블로그 포스팅\n"
        "/clear — 대화 기록 초기화\n"
        "/help — 도움말"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "사용법:\n\n"
        "• 메시지 입력 → Gemini가 답변\n"
        "• 텍스트 파일 첨부 (+ 지시사항) → 파일 분석\n"
        "• /youtube [유튜브 URL] → 영상 요약 후 '삶을 바꾸는 작은 실천' 카테고리에 자동 포스팅\n"
        "• /clear → 대화 기록 초기화 (새 주제 시작 시)\n\n"
        "활용 예시:\n"
        "— '이 내용으로 이메일 초안 써줘'\n"
        "— '요점만 정리해줘'\n"
        "— 파일 첨부 후 '이 문서 요약해줘'\n"
        "— /youtube https://youtu.be/xxxxx"
    )


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conversation_history[user_id] = []
    await update.message.reply_text("대화 기록을 초기화했습니다. 새로운 대화를 시작하세요.")


async def cmd_youtube(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("접근 권한이 없습니다.")
        return

    if not context.args:
        await update.message.reply_text(
            "사용법: /youtube [유튜브 URL]\n\n예시:\n/youtube https://youtu.be/xxxxx"
        )
        return

    url = context.args[0]
    await update.message.reply_text("영상 자막을 가져오는 중입니다... 잠시만 기다려주세요.")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        result = await asyncio.to_thread(process_youtube_url, url)
        if result.get("success"):
            await update.message.reply_text(
                f"블로그 포스팅 완료!\n\n"
                f"제목: {result.get('title', '')}\n"
                f"링크: {result.get('url', '')}"
            )
        else:
            await update.message.reply_text(
                f"포스팅 실패: {result.get('error', '알 수 없는 오류')}"
            )
    except Exception as e:
        await update.message.reply_text(f"오류가 발생했습니다: {e}")


async def _call_claude(user_id: int, user_content: str) -> str:
    """Claude API 호출 (Gemini 실패 시 백업)"""
    history = conversation_history.get(user_id, [])
    messages = [{"role": msg["role"] if msg["role"] != "assistant" else "assistant",
                 "content": msg["content"]} for msg in history]
    messages.append({"role": "user", "content": user_content})

    response = await asyncio.to_thread(
        claude_client.messages.create,
        model="claude-haiku-4-5-20251001",
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=messages,
    )
    reply = response.content[0].text

    if user_id not in conversation_history:
        conversation_history[user_id] = []
    conversation_history[user_id].append({"role": "user", "content": user_content})
    conversation_history[user_id].append({"role": "assistant", "content": reply})
    return reply


async def _call_ai(user_id: int, user_content: str) -> str:
    """Gemini 우선 호출, 실패 시 Claude로 자동 전환"""
    if user_id not in conversation_history:
        conversation_history[user_id] = []

    # Gemini 시도
    try:
        contents = [
            types.Content(
                role="user" if msg["role"] == "user" else "model",
                parts=[types.Part(text=msg["content"])]
            )
            for msg in conversation_history[user_id]
        ]
        contents.append(types.Content(role="user", parts=[types.Part(text=user_content)]))

        response = await asyncio.to_thread(
            gemini_client.models.generate_content,
            model=os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-flash"),
            contents=contents,
            config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT),
        )
        reply = response.text
        conversation_history[user_id].append({"role": "user", "content": user_content})
        conversation_history[user_id].append({"role": "assistant", "content": reply})
        return reply

    except Exception as gemini_err:
        # Gemini 실패 → Claude 백업
        if claude_client:
            print(f"[Gemini 실패, Claude로 전환] {gemini_err}")
            return await _call_claude(user_id, user_content)
        raise gemini_err


async def _send_long_message(update: Update, text: str):
    """4096자 초과 시 분할 전송"""
    chunk_size = 4096
    for i in range(0, len(text), chunk_size):
        await update.message.reply_text(text[i : i + chunk_size])


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("접근 권한이 없습니다.")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        reply = await _call_ai(user_id, update.message.text)
        await _send_long_message(update, reply)
    except Exception as e:
        await update.message.reply_text(f"오류가 발생했습니다: {e}")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("접근 권한이 없습니다.")
        return

    doc = update.message.document
    caption = update.message.caption or "이 파일을 분석해주세요."

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        file_obj = await context.bot.get_file(doc.file_id)
        with tempfile.NamedTemporaryFile(delete=True) as tmp:
            await file_obj.download_to_drive(tmp.name)
            try:
                with open(tmp.name, "r", encoding="utf-8") as f:
                    file_content = f.read()
            except UnicodeDecodeError:
                await update.message.reply_text(
                    "텍스트 파일만 읽을 수 있습니다. (txt, md, csv, py, json 등)"
                )
                return

        # 너무 큰 파일은 앞부분만 사용
        if len(file_content) > 12000:
            file_content = file_content[:12000] + "\n\n[파일이 길어서 앞부분만 포함했습니다]"

        user_content = f"{caption}\n\n파일명: {doc.file_name}\n\n--- 파일 내용 ---\n{file_content}"
        reply = await _call_ai(user_id, user_content)
        await _send_long_message(update, reply)
    except Exception as e:
        await update.message.reply_text(f"파일 처리 중 오류가 발생했습니다: {e}")


def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN 환경변수가 설정되지 않았습니다.")
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY 환경변수가 설정되지 않았습니다.")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("youtube", cmd_youtube))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    print("텔레그램 Gemini 비서 봇 시작!")
    print("=== 사용 가능한 Gemini 모델 목록 ===")
    for m in client.models.list():
        print(m.name)
    print("====================================")
    if ALLOWED_USER_IDS:
        print(f"허용된 유저 ID: {ALLOWED_USER_IDS}")
    else:
        print("경고: TELEGRAM_ALLOWED_USER_IDS 미설정 — 모든 유저 접근 가능")

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
