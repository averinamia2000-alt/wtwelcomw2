
import asyncio
import html
import logging
import os
import re
from urllib.parse import quote

import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("whitech-bot")

def required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value

TELEGRAM_BOT_TOKEN = required("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = required("OPENAI_API_KEY")
ATLASSIAN_BASE_URL = required("ATLASSIAN_BASE_URL").rstrip("/")
ATLASSIAN_EMAIL = required("ATLASSIAN_EMAIL")
ATLASSIAN_API_TOKEN = required("ATLASSIAN_API_TOKEN")
CONFLUENCE_SPACE_KEY = os.getenv("CONFLUENCE_SPACE_KEY", "pmprod")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")

bot = Bot(TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
openai = AsyncOpenAI(api_key=OPENAI_API_KEY)

client = httpx.AsyncClient(
    base_url=ATLASSIAN_BASE_URL,
    auth=(ATLASSIAN_EMAIL, ATLASSIAN_API_TOKEN),
    headers={"Accept": "application/json"},
    timeout=30,
)

def clean_html(s: str) -> str:
    s = re.sub(r"<br\s*/?>", "\n", s or "", flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()

async def confluence_search(question: str):
    # First use Confluence's full-text CQL. We search only pmprod.
    safe = question.replace("\\", " ").replace('"', '\\"')
    cql = f'space="{CONFLUENCE_SPACE_KEY}" AND type=page AND text ~ "{safe}"'
    r = await client.get(
        "/wiki/rest/api/content/search",
        params={"cql": cql, "limit": 8, "expand": "body.view"},
    )
    r.raise_for_status()
    results = r.json().get("results", [])

    # If a long natural-language query returns nothing, retry using useful words.
    if not results:
        words = [
            w for w in re.findall(r"[A-Za-zА-Яа-яЁё0-9._-]+", question)
            if len(w) >= 4
        ][:5]
        if words:
            term = " ".join(words).replace('"', '\\"')
            cql = f'space="{CONFLUENCE_SPACE_KEY}" AND type=page AND text ~ "{term}"'
            r = await client.get(
                "/wiki/rest/api/content/search",
                params={"cql": cql, "limit": 8, "expand": "body.view"},
            )
            r.raise_for_status()
            results = r.json().get("results", [])

    sources = []
    for item in results:
        page_id = item["id"]
        title = item.get("title", "Confluence")
        body = clean_html(item.get("body", {}).get("view", {}).get("value", ""))
        webui = item.get("_links", {}).get("webui")
        url = (
            f"{ATLASSIAN_BASE_URL}/wiki{webui}"
            if webui
            else f"{ATLASSIAN_BASE_URL}/wiki/spaces/{CONFLUENCE_SPACE_KEY}/pages/{page_id}"
        )
        sources.append({"title": title, "url": url, "text": body[:10000]})
    return sources

async def build_answer(question: str) -> str:
    sources = await confluence_search(question)
    if not sources:
        return (
            "Я не нашёл подтверждённого ответа в базе Whitech. "
            "Попробуй добавить название процесса, команды или проекта."
        )

    context = "\n\n".join(
        f"[{i}] {s['title']}\nURL: {s['url']}\n{s['text']}"
        for i, s in enumerate(sources, 1)
    )

    instructions = """Ты внутренний Q&A-бот Whitech.
Отвечай ТОЛЬКО по предоставленным материалам Confluence.
Не придумывай процессы, людей, контакты, Jira-ссылки или факты.
Если в найденных материалах недостаточно информации — прямо скажи об этом.
Если пользователь просит контакт, дай имя/роль/контакт только если они есть в источнике.
Если в источнике есть Jira-ссылка, можно вернуть её, но самостоятельно Jira не ищи.
Отвечай коротко и практично, на языке пользователя.
В конце обязательно добавь раздел «Источники» и 1–4 наиболее релевантные ссылки.
"""

    response = await openai.responses.create(
        model=OPENAI_MODEL,
        instructions=instructions,
        input=f"Вопрос пользователя:\n{question}\n\nМатериалы Confluence:\n{context}",
    )
    return response.output_text.strip()

@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "Привет! Я бот по базе знаний Whitech 👋\n\n"
        "Можно спросить меня, например:\n"
        "• Как проходит финансовая сверка?\n"
        "• К кому обратиться по Retention?\n"
        "• Где найти шаблон устава проекта?\n"
        "• Как передать запрос в CC?\n"
        "• Дай ссылку на Jira по процессу, если она есть в Confluence.\n\n"
        "Просто напиши вопрос обычным текстом."
    )

@dp.message(F.text)
async def question(message: Message):
    text = (message.text or "").strip()
    if not text:
        return
    await bot.send_chat_action(message.chat.id, "typing")
    try:
        answer = await build_answer(text)
    except httpx.HTTPStatusError as e:
        log.exception("Atlassian HTTP error")
        status = e.response.status_code
        answer = (
            f"Не удалось прочитать Confluence (HTTP {status}). "
            "Проверь ATLASSIAN_EMAIL, ATLASSIAN_API_TOKEN и права аккаунта на pmprod."
        )
    except Exception as e:
        log.exception("Request failed: %s", type(e).__name__)
        answer = (
            f"Ошибка при обработке запроса: {type(e).__name__}. "
            "Подробности записаны в Railway Logs."
        )
    await message.answer(answer, disable_web_page_preview=True)

async def main():
    log.info("Starting Whitech bot; space=%s model=%s", CONFLUENCE_SPACE_KEY, OPENAI_MODEL)
    try:
        await dp.start_polling(bot)
    finally:
        await client.aclose()

if __name__ == "__main__":
    asyncio.run(main())
