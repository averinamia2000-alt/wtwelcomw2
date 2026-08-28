
import asyncio
import html
import json
import logging
import os
import re
from typing import Any

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
ALLOWED_ROOT_PAGE_ID = os.getenv("ALLOWED_ROOT_PAGE_ID", "3621748974")
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
    s = re.sub(r"</(p|div|li|tr|h[1-6])>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()

def cql_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').strip()

async def make_search_plan(question: str) -> dict[str, Any]:
    instructions = """Ты маршрутизатор поиска по внутренней Confluence Whitech.
Твоя задача — НЕ отвечать на вопрос, а придумать хорошие поисковые запросы.

Верни ТОЛЬКО JSON, без markdown:
{
  "intent": "process|contact|link|policy|other",
  "queries": ["...", "...", "..."],
  "keywords": ["...", "..."]
}

Правила:
- queries: ровно 3 КОРОТКИХ варианта, обычно 1–3 слова.
- Не копируй целиком длинный вопрос пользователя.
- Используй вероятные названия корпоративных процессов и синонимы.
- Добавляй русский и английский термин, если в корпоративной базе вероятны оба.
- Для увольнения ищи также offboarding/увольнение/HR.
- Для найма — hiring/onboarding/найм.
- Для контакта — роль, направление, имя, contact/contacts.
- Для Jira не ищи Jira API: ищи название процесса и слово Jira только как один из вариантов.
- Не выдумывай факты о Whitech.
"""
    response = await openai.responses.create(
        model=OPENAI_MODEL,
        instructions=instructions,
        input=question,
    )
    raw = response.output_text.strip()
    raw = re.sub(r"^```json\s*|\s*```$", "", raw, flags=re.I)
    try:
        plan = json.loads(raw)
    except Exception:
        # Safe fallback if model did not return valid JSON.
        words = [w for w in re.findall(r"[A-Za-zА-Яа-яЁё0-9._-]+", question) if len(w) >= 4]
        return {"intent": "other", "queries": [" ".join(words[:4]) or question], "keywords": words[:6]}
    queries = [str(q).strip() for q in plan.get("queries", []) if str(q).strip()]
    plan["queries"] = queries[:3] or [question]
    return plan

async def search_one(query: str, limit: int = 5) -> list[dict]:
    safe = cql_escape(query)
    cql = f'space="{CONFLUENCE_SPACE_KEY}" AND type=page AND ancestor={ALLOWED_ROOT_PAGE_ID} AND text ~ "{safe}"'
    r = await client.get(
        "/wiki/rest/api/content/search",
        params={"cql": cql, "limit": limit, "expand": "body.view,version"},
    )
    r.raise_for_status()

    out = []
    for item in r.json().get("results", []):
        page_id = item["id"]
        title = item.get("title", "Confluence")
        body = clean_html(item.get("body", {}).get("view", {}).get("value", ""))
        webui = item.get("_links", {}).get("webui")
        url = f"{ATLASSIAN_BASE_URL}/wiki{webui}" if webui else (
            f"{ATLASSIAN_BASE_URL}/wiki/spaces/{CONFLUENCE_SPACE_KEY}/pages/{page_id}"
        )
        out.append({
            "id": page_id,
            "title": title,
            "url": url,
            "text": body[:5000],
            "matched_query": query,
        })
    return out

async def multi_search(plan: dict[str, Any]) -> list[dict]:
    queries = plan.get("queries", [])[:3]
    batches = await asyncio.gather(*(search_one(q) for q in queries))

    # Deduplicate pages, while giving a simple score for appearing in multiple searches.
    pages: dict[str, dict] = {}
    for batch in batches:
        for p in batch:
            if p["id"] not in pages:
                p["hits"] = 1
                p["matched_queries"] = [p["matched_query"]]
                pages[p["id"]] = p
            else:
                pages[p["id"]]["hits"] += 1
                pages[p["id"]]["matched_queries"].append(p["matched_query"])

    # Multiple-query hits first, then pages with useful keyword overlap.
    keywords = [str(k).lower() for k in plan.get("keywords", [])]
    for p in pages.values():
        hay = (p["title"] + " " + p["text"][:3000]).lower()
        p["score"] = p["hits"] * 5 + sum(1 for k in keywords if k and k in hay)

    return sorted(pages.values(), key=lambda x: x["score"], reverse=True)[:5]

async def build_answer(question: str) -> str:
    plan = await make_search_plan(question)
    log.info("Search plan: %s", plan)

    sources = await multi_search(plan)
    log.info("Found %s unique Confluence pages", len(sources))

    if not sources:
        return (
            "Я не нашёл ответа в доступном мне разделе базы знаний Whitech.\n\n"
            "Напишите @MiaA_01t — она поможет разобраться 💛"
        )

    context = "\n\n".join(
        f"""[SOURCE {i}]
TITLE: {s['title']}
URL: {s['url']}
MATCHED SEARCHES: {", ".join(s['matched_queries'])}
CONTENT:
{s['text']}"""
        for i, s in enumerate(sources, 1)
    )

    instructions = """Ты внутренний Q&A-бот Whitech.
Тебе переданы результаты поиска ТОЛЬКО из Confluence space pmprod.

Критические правила:
1. Отвечай только на основании CONTENT источников.
2. Не выдумывай процессы, людей, контакты, Jira, ссылки, сроки или правила.
3. Если найденные страницы не отвечают на вопрос — скажи, что точного ответа в базе не найдено.
4. Не считай страницу релевантной только потому, что поисковый запрос совпал с одним словом.
5. Если источники противоречат друг другу, явно укажи это.
6. Jira можно упоминать/давать ссылку только если Jira-ссылка реально есть в CONTENT.
7. Контакт можно дать только если имя/роль/contact реально есть в CONTENT.
8. Не раскрывай лишние персональные данные, не нужные для ответа пользователя.\n9. Если в переданных источниках нет достаточного ответа, обязательно закончи: "Напишите @MiaA_01t — она поможет разобраться 💛".

Формат:
- Сначала конкретный короткий ответ.
- Для процесса, если шаги подтверждены источником: «Что делать:» и шаги.
- Если есть подтвержденный контакт: «К кому обратиться:».
- Если есть нужная подтвержденная ссылка: «Ссылка:».
- В конце «Источники:» и 1–4 URL из переданных SOURCE.
- Не добавляй источник, который не использовал в ответе.
- Отвечай на языке пользователя.
"""

    response = await openai.responses.create(
        model=OPENAI_MODEL,
        instructions=instructions,
        input=f"ВОПРОС:\n{question}\n\nРЕЗУЛЬТАТЫ ПОИСКА:\n{context}",
    )
    return response.output_text.strip()

@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "Привет! Я помогатор Whitech 👋\n\n"
        "Помогу найти инфу из базы знаний Whitech. Просто напиши вопрос обычным языком — "
        "я поищу ответ в доступном мне разделе базы и пришлю нужную информацию и ссылки.\n\n"
        "Если я не найду ответ, напишите @MiaA_01t — она поможет разобраться 💛"
    )

@dp.message(F.text)
async def question(message: Message):
    text = (message.text or "").strip()
    if not text:
        return

    status = await message.answer("🔎 Ищу в базе знаний Whitech…")
    await bot.send_chat_action(message.chat.id, "typing")
    try:
        answer = await asyncio.wait_for(build_answer(text), timeout=25)
    except httpx.HTTPStatusError as e:
        log.exception("Atlassian HTTP error")
        answer = (
            f"Не удалось прочитать Confluence (HTTP {e.response.status_code}). "
            "Проверь права Atlassian-аккаунта на pmprod."
        )
    except asyncio.TimeoutError:
        log.error("Request timed out after 25 seconds")
        answer = "Поиск занял слишком много времени. Попробуйте сформулировать вопрос короче или напишите @MiaA_01t — она поможет разобраться 💛"
    except Exception as e:
        log.exception("Request failed: %s", type(e).__name__)
        answer = f"Ошибка при обработке запроса: {type(e).__name__}. Подробности есть в Railway Logs."

    # Replace the temporary status message when possible.
    if len(answer) <= 4000:
        await status.edit_text(answer, disable_web_page_preview=True)
    else:
        await status.edit_text("Нашёл информацию — отправляю ответ ниже 👇")
        for i in range(0, len(answer), 3900):
            await message.answer(answer[i:i+3900], disable_web_page_preview=True)

async def main():
    log.info("Starting Whitech bot v5-fast; space=%s root=%s model=%s", CONFLUENCE_SPACE_KEY, ALLOWED_ROOT_PAGE_ID, OPENAI_MODEL)
    try:
        await dp.start_polling(bot)
    finally:
        await client.aclose()

if __name__ == "__main__":
    asyncio.run(main())
