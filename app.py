
import asyncio
import html
import json
import logging
import os
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any

import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("whitech-helper")

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
REQUEST_TIMEOUT_SECONDS = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "10"))

bot = Bot(TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
openai = AsyncOpenAI(api_key=OPENAI_API_KEY, timeout=8.0, max_retries=1)
confluence = httpx.AsyncClient(
    base_url=ATLASSIAN_BASE_URL,
    auth=(ATLASSIAN_EMAIL, ATLASSIAN_API_TOKEN),
    headers={"Accept": "application/json"},
    timeout=5.0,
)

# Short-lived conversational context. No Telegram profile data is stored.
# It resets on deploy/restart by design.
history: dict[int, deque] = defaultdict(lambda: deque(maxlen=6))

def clean_html(value: str) -> str:
    value = re.sub(r"<br\s*/?>", "\n", value or "", flags=re.I)
    value = re.sub(r"</(p|div|li|tr|h[1-6])>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n\s*\n+", "\n", value)
    return value.strip()

def cql_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').strip()

def compact_history(chat_id: int) -> str:
    items = history.get(chat_id, [])
    if not items:
        return "Нет предыдущего контекста."
    return "\n".join(f"{role}: {text}" for role, text in items)

def analytics(event: str, **fields: Any) -> None:
    # Structured Railway logs: no username, name, phone, email or message text.
    payload = {
        "event": event,
        "ts": datetime.now(timezone.utc).isoformat(),
        **fields,
    }
    log.info("ANALYTICS %s", json.dumps(payload, ensure_ascii=False))

async def make_search_plan(question: str, conversation: str) -> dict[str, Any]:
    t0 = time.perf_counter()
    instructions = """Ты поисковый маршрутизатор внутреннего помощника Whitech.
Отвечай ТОЛЬКО JSON без markdown:
{
  "needs_clarification": false,
  "clarification_question": "",
  "intent": "process|contact|link|document|policy|other",
  "queries": ["...", "...", "..."],
  "keywords": ["...", "...", "..."]
}

Правила:
- Пользователь всегда получает ответ на русском.
- Если вопрос реально неоднозначный и без уточнения нельзя понять, что искать, поставь needs_clarification=true и задай ОДИН короткий вопрос.
- Учитывай предыдущий контекст: короткое продолжение вроде «а кому отправлять?» относится к предыдущей теме.
- Если смысл понятен, не задавай лишних уточнений.
- Дай ровно 3 коротких поисковых запроса по 1–3 слова.
- Используй корпоративные синонимы: увольнение/offboarding, онбординг/onboarding, релокейт/relocation, КДП/HR и т.п., когда уместно.
- Для контакта ищи функцию/роль/направление и contacts.
- Для Jira ищи процесс; Jira API не используется.
- Не отвечай на сам вопрос и не выдумывай факты Whitech.
"""
    response = await openai.responses.create(
        model=OPENAI_MODEL,
        instructions=instructions,
        input=f"КОНТЕКСТ:\n{conversation}\n\nНОВЫЙ ВОПРОС:\n{question}",
        max_output_tokens=350,
    )
    raw = response.output_text.strip()
    raw = re.sub(r"^```json\s*|\s*```$", "", raw, flags=re.I)
    try:
        plan = json.loads(raw)
    except Exception:
        words = [w for w in re.findall(r"[A-Za-zА-Яа-яЁё0-9._-]+", question) if len(w) >= 3]
        plan = {
            "needs_clarification": False,
            "clarification_question": "",
            "intent": "other",
            "queries": [" ".join(words[:3]) or question] * 3,
            "keywords": words[:6],
        }
    queries = [str(q).strip() for q in plan.get("queries", []) if str(q).strip()]
    plan["queries"] = (queries + queries[:1] * 3)[:3] if queries else [question, question, question]
    log.info("TIMING search_plan=%.2fs", time.perf_counter() - t0)
    return plan

async def search_one(query: str, limit: int = 5) -> list[dict]:
    safe = cql_escape(query)
    # HARD SECURITY BOUNDARY: only descendants of the approved root.
    cql = (
        f'space="{CONFLUENCE_SPACE_KEY}" AND type=page '
        f'AND ancestor={ALLOWED_ROOT_PAGE_ID} AND text ~ "{safe}"'
    )
    response = await confluence.get(
        "/wiki/rest/api/content/search",
        params={"cql": cql, "limit": limit, "expand": "body.view,version"},
    )
    response.raise_for_status()

    pages = []
    for item in response.json().get("results", []):
        page_id = item["id"]
        webui = item.get("_links", {}).get("webui")
        url = (
            f"{ATLASSIAN_BASE_URL}/wiki{webui}"
            if webui
            else f"{ATLASSIAN_BASE_URL}/wiki/spaces/{CONFLUENCE_SPACE_KEY}/pages/{page_id}"
        )
        pages.append({
            "id": page_id,
            "title": item.get("title", "Confluence"),
            "url": url,
            "text": clean_html(item.get("body", {}).get("view", {}).get("value", ""))[:5500],
            "modified": item.get("version", {}).get("when", ""),
            "matched_query": query,
        })
    return pages

def rerank(pages: list[dict], keywords: list[str]) -> list[dict]:
    """Fast local reranking: query hits + title/content overlap + recency tie-break."""
    kw = [k.lower().strip() for k in keywords if str(k).strip()]
    for page in pages:
        title = page["title"].lower()
        body = page["text"][:2500].lower()
        overlap = sum(4 for k in kw if k in title) + sum(1 for k in kw if k in body)
        page["score"] = page.get("hits", 1) * 6 + overlap
    return sorted(
        pages,
        key=lambda p: (p["score"], p.get("modified", "")),
        reverse=True,
    )[:5]

async def retrieve(plan: dict[str, Any]) -> list[dict]:
    t0 = time.perf_counter()
    batches = await asyncio.gather(*(search_one(q) for q in plan["queries"][:3]))

    dedup: dict[str, dict] = {}
    for batch in batches:
        for page in batch:
            if page["id"] not in dedup:
                page["hits"] = 1
                page["matched_queries"] = [page["matched_query"]]
                dedup[page["id"]] = page
            else:
                dedup[page["id"]]["hits"] += 1
                dedup[page["id"]]["matched_queries"].append(page["matched_query"])

    result = rerank(list(dedup.values()), [str(x) for x in plan.get("keywords", [])])
    log.info("TIMING retrieval_rerank=%.2fs candidates=%d selected=%d",
             time.perf_counter() - t0, len(dedup), len(result))
    return result

async def generate_answer(question: str, conversation: str, pages: list[dict]) -> str:
    t0 = time.perf_counter()
    context = "\n\n".join(
        f"""[SOURCE {i}]
TITLE: {p['title']}
URL: {p['url']}
MODIFIED: {p.get('modified', '')}
CONTENT:
{p['text']}"""
        for i, p in enumerate(pages, 1)
    )

    instructions = """Ты дружелюбный внутренний помогатор команды Whitech 👋
Отвечай ТОЛЬКО на русском и ТОЛЬКО по переданным SOURCE.

Жёсткие правила:
1. Нельзя добавлять факты, процессы, контакты, сроки или ссылки, которых нет в SOURCE.
2. Выбери наиболее релевантный источник; не перечисляй слабые совпадения.
3. Более свежему источнику можно отдать приоритет, если источники описывают одно и то же и расходятся.
4. Если источники противоречат друг другу существенно — не выбирай догадкой. Скажи об этом и направь к @MiaA_01t.
5. Если ответа недостаточно — напиши:
   «Не нашёл эту информацию в базе знаний Whitech 😔 Напишите @MiaA_01t — она поможет разобраться.»
6. Контакты: показывай только необходимые рабочие данные в формате «Имя — должность — Telegram».
7. Можно отдавать внешние ссылки, Jira, документы, Telegram и другие ссылки, только если они реально присутствуют в SOURCE.
8. Ссылку на Confluence-страницу вне разрешённого дерева можно показать ТОЛЬКО если она буквально присутствует внутри разрешённого SOURCE. Не читай содержимое такой внешней страницы.
9. Средняя длина ответа: конкретный ответ + подтверждённые шаги + нужный контакт/ссылка.
10. Всегда заканчивай разделом «📚 Источник:» и указывай 1–3 URL использованных SOURCE.
11. Если пользователь спрашивает продолжение, учитывай КОНТЕКСТ, но факты всё равно бери только из SOURCE.
"""
    response = await openai.responses.create(
        model=OPENAI_MODEL,
        instructions=instructions,
        input=f"КОНТЕКСТ ДИАЛОГА:\n{conversation}\n\nВОПРОС:\n{question}\n\nSOURCE:\n{context}",
        max_output_tokens=700,
    )
    log.info("TIMING final_answer=%.2fs", time.perf_counter() - t0)
    return response.output_text.strip()

async def build_answer(question: str, chat_id: int) -> tuple[str, str, list[str]]:
    t0 = time.perf_counter()
    conversation = compact_history(chat_id)
    plan = await make_search_plan(question, conversation)
    log.info("Search plan: %s", plan)

    if plan.get("needs_clarification"):
        answer = str(plan.get("clarification_question") or "Уточни, пожалуйста, о каком процессе идёт речь?")
        analytics("clarification", intent=plan.get("intent", "other"))
        return answer, str(plan.get("intent", "other")), []

    pages = await retrieve(plan)
    if not pages:
        answer = "Не нашёл эту информацию в базе знаний Whitech 😔 Напишите @MiaA_01t — она поможет разобраться."
        analytics("not_found", intent=plan.get("intent", "other"))
        return answer, str(plan.get("intent", "other")), []

    answer = await generate_answer(question, conversation, pages)
    elapsed = round(time.perf_counter() - t0, 2)
    analytics(
        "answered",
        intent=plan.get("intent", "other"),
        latency_seconds=elapsed,
        source_page_ids=[p["id"] for p in pages[:3]],
    )
    log.info("TIMING total=%.2fs", time.perf_counter() - t0)
    return answer, str(plan.get("intent", "other")), [p["id"] for p in pages[:3]]

@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "Привет! Я помогатор Whitech 👋\n\n"
        "Помогу найти информацию из базы знаний: инструкции, рабочие контакты, "
        "нужные ссылки, шаблоны и пошаговые процессы.\n\n"
        "Например, спроси:\n"
        "• Как создать заявку на увольнение?\n"
        "• Где найти контакты Retention?\n"
        "• Как уйти в отпуск?\n\n"
        "Если ответа в доступной базе не окажется, направлю к @MiaA_01t 💛"
    )

@dp.message(F.text)
async def question(message: Message):
    question_text = (message.text or "").strip()
    if not question_text:
        return

    status = await message.answer("🔎 Ищу в базе знаний Whitech…")
    started = time.perf_counter()

    try:
        answer, intent, source_ids = await asyncio.wait_for(
            build_answer(question_text, message.chat.id),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        analytics("timeout", latency_seconds=round(time.perf_counter() - started, 2))
        answer = (
            "Поиск занял больше 10 секунд 😔 Попробуйте сформулировать вопрос чуть короче "
            "или напишите @MiaA_01t — она поможет разобраться."
        )
    except httpx.HTTPStatusError as exc:
        log.exception("Confluence HTTP error")
        analytics("error", error_type="ConfluenceHTTP", status=exc.response.status_code)
        answer = "Не получилось обратиться к базе знаний. Напишите @MiaA_01t — она поможет разобраться 💛"
    except Exception as exc:
        log.exception("Request failed: %s", type(exc).__name__)
        analytics("error", error_type=type(exc).__name__)
        answer = "Что-то пошло не так при поиске 😔 Напишите @MiaA_01t — она поможет разобраться."

    # Keep only conversational text, not user identity/profile data.
    history[message.chat.id].append(("Пользователь", question_text[:800]))
    history[message.chat.id].append(("Помогатор", answer[:1200]))

    if len(answer) <= 4000:
        await status.edit_text(answer, disable_web_page_preview=True)
    else:
        await status.edit_text("Нашёл информацию — отправляю ниже 👇")
        for i in range(0, len(answer), 3900):
            await message.answer(answer[i:i+3900], disable_web_page_preview=True)

async def main():
    log.info(
        "Starting Whitech Helper v6; space=%s root=%s model=%s timeout=%ss",
        CONFLUENCE_SPACE_KEY, ALLOWED_ROOT_PAGE_ID, OPENAI_MODEL, REQUEST_TIMEOUT_SECONDS
    )
    try:
        await dp.start_polling(bot)
    finally:
        await confluence.aclose()

if __name__ == "__main__":
    asyncio.run(main())
