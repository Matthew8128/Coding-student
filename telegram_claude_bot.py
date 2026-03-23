import os
import asyncio
import tempfile
from google import genai
from google.genai import types
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
# 보안: 허용할 텔레그램 유저 ID (쉼표로 여러 명 가능, 비워두면 전체 허용)
ALLOWED_USER_IDS = set(
    uid.strip() for uid in os.getenv("TELEGRAM_ALLOWED_USER_IDS", "").split(",") if uid.strip()
)

SYSTEM_PROMPT = """당신은 도움이 되는 개인 비서입니다.
한국어로 친근하게 답변해주세요.
문서 초안 작성, 파일 내용 분석, 정보 정리, 아이디어 제안 등 다양한 작업을 도와드립니다.
답변이 길어질 경우 핵심 내용을 먼저 말하고 상세 내용은 이후에 설명해주세요."""

client = genai.Client(api_key=GEMINI_API_KEY)

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
        "/clear — 대화 기록 초기화\n"
        "/help — 도움말"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "사용법:\n\n"
        "• 메시지 입력 → Gemini가 답변\n"
        "• 텍스트 파일 첨부 (+ 지시사항) → 파일 분석\n"
        "• /clear → 대화 기록 초기화 (새 주제 시작 시)\n\n"
        "활용 예시:\n"
        "— '이 내용으로 이메일 초안 써줘'\n"
        "— '요점만 정리해줘'\n"
        "— 파일 첨부 후 '이 문서 요약해줘'"
    )


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conversation_history[user_id] = []
    await update.message.reply_text("대화 기록을 초기화했습니다. 새로운 대화를 시작하세요.")


async def _call_gemini(user_id: int, user_content: str) -> str:
    if user_id not in conversation_history:
        conversation_history[user_id] = []

    # 기존 대화 기록을 새 SDK 형식으로 변환
    contents = [
        types.Content(
            role="user" if msg["role"] == "user" else "model",
            parts=[types.Part(text=msg["content"])]
        )
        for msg in conversation_history[user_id]
    ]
    contents.append(types.Content(role="user", parts=[types.Part(text=user_content)]))

    response = await asyncio.to_thread(
        client.models.generate_content,
        model="gemini-2.0-flash",
        contents=contents,
        config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT),
    )
    reply = response.text

    conversation_history[user_id].append({"role": "user", "content": user_content})
    conversation_history[user_id].append({"role": "assistant", "content": reply})
    return reply


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
        reply = await _call_gemini(user_id, update.message.text)
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
        reply = await _call_gemini(user_id, user_content)
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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    print("텔레그램 Gemini 비서 봇 시작!")
    if ALLOWED_USER_IDS:
        print(f"허용된 유저 ID: {ALLOWED_USER_IDS}")
    else:
        print("경고: TELEGRAM_ALLOWED_USER_IDS 미설정 — 모든 유저 접근 가능")

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
