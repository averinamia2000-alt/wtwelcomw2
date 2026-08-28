
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
# Comma-separated list of page IDs. Empty = no ancestor restriction, search the whole
# space. A single hardcoded ID here silently excludes every page outside that one
# subtree from search results, no matter how well the keywords match.
ALLOWED_ROOT_PAGE_IDS = [
    p.strip() for p in os.getenv("ALLOWED_ROOT_PAGE_IDS", "").split(",") if p.strip()
]
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
MAX_PAGE_CHARS = int(os.getenv("MAX_PAGE_CHARS", "12000"))

bot = Bot(TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
# Give the single final model call enough room; outer request timeout remains authoritative.
openai = AsyncOpenAI(api_key=OPENAI_API_KEY)
confluence = httpx.AsyncClient(
    base_url=ATLASSIAN_BASE_URL,
    auth=(ATLASSIAN_EMAIL, ATLASSIAN_API_TOKEN),
    headers={"Accept": "application/json"},
    timeout=8.0,
)

history: dict[int, deque] = defaultdict(lambda: deque(maxlen=6))

STOPWORDS = {
    "как","где","что","кто","куда","мне","можно","могу","ли","я","мы","вы","это",
    "найти","дай","дайте","нужно","надо","хочу","пожалуйста","плиз","есть","для",
    "на","в","во","из","по","к","ко","с","со","и","или","а","у","про","об","о",
    "the","a","an","is","are","how","where","who","what","can","i","me","to","for"
}

SYNONYMS = {
    "уволить": ["увольнение", "offboarding"],
    "увольнение": ["offboarding", "termination"],
    "релокейт": ["relocation", "релокация"],
    "релокация": ["relocation", "релокейт"],
    "онбординг": ["onboarding", "адаптация"],
    "онбордингa": ["onboarding", "адаптация"],
    "отпуск": ["vacation", "leave"],
    "техника": ["оборудование", "equipment"],
    "ноутбук": ["техника", "оборудование"],
    "ретена": ["retention", "CRM"],
    "ретен": ["retention", "CRM"],
    "retention": ["ретен", "CRM"],
    "сис": ["system administrator", "IT"],
    "админов": ["system administrator", "IT"],
    "кдп": ["HR", "кадровое"],
    "грейд": ["grade", "грейды"],
    "грейды": ["grade", "грейд"],
    "недельный": ["weekly", "еженедельный"],
    "отчет": ["отчёт", "report"],
    "отчёт": ["отчет", "report"],
}

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
    payload = {"event": event, "ts": datetime.now(timezone.utc).isoformat(), **fields}
    log.info("ANALYTICS %s", json.dumps(payload, ensure_ascii=False))

def normalize_tokens(text: str) -> list[str]:
    return [
        t.lower() for t in re.findall(r"[A-Za-zА-Яа-яЁё0-9._-]+", text)
        if len(t) >= 2 and t.lower() not in STOPWORDS
    ]

def local_search_queries(question: str, conversation: str) -> list[str]:
    """Fast deterministic variants. Prefer single terms / OR groups so CQL is not overly strict."""
    tokens = normalize_tokens(question)

    if len(tokens) <= 2 and conversation != "Нет предыдущего контекста.":
        context_tokens = normalize_tokens(conversation)
        tokens = list(dict.fromkeys(tokens + context_tokens[-5:]))

    core = list(dict.fromkeys(tokens))[:6]
    expanded = []
    for token in core:
        expanded.extend(SYNONYMS.get(token, []))
    expanded = list(dict.fromkeys(expanded))

    queries = []
    # Strong individual concepts are much more recall-friendly than a 3-word phrase.
    for token in core[:2]:
        queries.append(token)

    # One broad OR query for synonyms/related concepts.
    broad_terms = list(dict.fromkeys(core[:3] + expanded[:4]))
    if broad_terms:
        queries.append(" OR ".join(broad_terms))

    return [q for q in dict.fromkeys(queries) if q.strip()][:3] or [question[:80]]

async def search_one(query: str, limit: int = 8) -> list[dict]:
    if " OR " in query:
        terms = [cql_escape(x) for x in query.split(" OR ") if x.strip()]
        text_clause = "(" + " OR ".join(f'text ~ "{term}"' for term in terms) + ")"
    else:
        text_clause = f'text ~ "{cql_escape(query)}"'
    cql_parts = [f'space="{CONFLUENCE_SPACE_KEY}"', "type=page"]
    if ALLOWED_ROOT_PAGE_IDS:
        ancestor_clause = " OR ".join(f"ancestor={pid}" for pid in ALLOWED_ROOT_PAGE_IDS)
        cql_parts.append(f"({ancestor_clause})")
    cql_parts.append(text_clause)
    cql = " AND ".join(cql_parts)
    response = await confluence.get(
        "/wiki/rest/api/content/search",
        params={"cql": cql, "limit": limit, "expand": "body.view,version"},
    )
    response.raise_for_status()

    pages = []
    for item in response.json().get("results", []):
        page_id = item["id"]
        webui = item.get("_links", {}).get("webui")
        url = f"{ATLASSIAN_BASE_URL}/wiki{webui}" if webui else (
            f"{ATLASSIAN_BASE_URL}/wiki/spaces/{CONFLUENCE_SPACE_KEY}/pages/{page_id}"
        )
        pages.append({
            "id": page_id,
            "title": item.get("title", "Confluence"),
            "url": url,
            "text": clean_html(item.get("body", {}).get("view", {}).get("value", ""))[:MAX_PAGE_CHARS],
            "modified": item.get("version", {}).get("when", ""),
            "matched_query": query,
        })
    return pages

def rerank(pages: list[dict], question: str) -> list[dict]:
    keywords = normalize_tokens(question)
    for p in pages:
        title = p["title"].lower()
        body = p["text"].lower()  # was p["text"][:2500] — matches past char 2500 scored as zero
        overlap = sum(5 for k in keywords if k in title) + sum(1 for k in keywords if k in body)
        p["score"] = p.get("hits", 1) * 7 + overlap
    ranked = sorted(pages, key=lambda p: (p["score"], p.get("modified", "")), reverse=True)
    log.info(
        "Candidates before cutoff: %s",
        [(p["title"], p["score"]) for p in ranked],
    )
    return ranked[:4]

async def retrieve(question: str, conversation: str) -> tuple[list[dict], list[str]]:
    t0 = time.perf_counter()
    queries = local_search_queries(question, conversation)
    log.info("Local search queries: %s", queries)
    batches = await asyncio.gather(*(search_one(q) for q in queries))

    dedup: dict[str, dict] = {}
    for batch in batches:
        for p in batch:
            if p["id"] not in dedup:
                p["hits"] = 1
                p["matched_queries"] = [p["matched_query"]]
                dedup[p["id"]] = p
            else:
                dedup[p["id"]]["hits"] += 1
                dedup[p["id"]]["matched_queries"].append(p["matched_query"])

    result = rerank(list(dedup.values()), question)
    log.info("TIMING retrieval=%.2fs candidates=%d selected=%d",
             time.perf_counter() - t0, len(dedup), len(result))
    return result, queries

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
Всегда отвечай на русском. Факты бери ТОЛЬКО из SOURCE.

Правила:
- Дай компактный, но ЗАВЕРШЁННЫЙ практичный ответ: суть + подтверждённые шаги + нужная ссылка.
- Никогда не обрывай предложение или список. Лучше сократи ответ, чем оставь его незавершённым.
- Всегда добавляй «📚 Источник:» и 1–3 URL реально использованных SOURCE.
- Выбирай наиболее релевантный источник; при равной релевантности можно предпочесть более свежий.
- Если вопрос неоднозначен и из SOURCE нельзя понять, что именно нужно пользователю, задай ОДИН короткий уточняющий вопрос. Не выдумывай ответ.
- Если SOURCE не содержит достаточного ответа, ответь:
  «Не нашёл эту информацию в базе знаний Whitech 😔 Напишите @MiaA_01t — она поможет разобраться.»
- При существенном конфликте источников тоже направь к @MiaA_01t.
- Контакт: только необходимые рабочие данные, формат «Имя — должность — Telegram».
- Можно отдавать Jira, Google Docs, Miro, Telegram и другие ссылки, если они буквально присутствуют в SOURCE.
- Содержимое Confluence вне разрешённого дерева не используй. Если ссылка на внешнюю страницу буквально есть в разрешённом SOURCE, саму ссылку показать можно.
- Учитывай КОНТЕКСТ для продолжения разговора, но факты всё равно должны быть подтверждены SOURCE.
"""
    response = await openai.responses.create(
        model=OPENAI_MODEL,
        instructions=instructions,
        input=f"КОНТЕКСТ:\n{conversation}\n\nВОПРОС:\n{question}\n\nSOURCE:\n{context}",
        max_output_tokens=3000,
        reasoning={"effort": "minimal"},
    )
    log.info("TIMING final_answer=%.2fs", time.perf_counter() - t0)

    # gpt-5-mini is a reasoning model: its internal "thinking" tokens are drawn from
    # the SAME max_output_tokens budget as the visible answer. If reasoning eats most
    # of the budget, output_text gets cut off mid-sentence with no error raised.
    # response.status == "incomplete" is how the API tells us this actually happened.
    if getattr(response, "status", None) == "incomplete":
        reason = getattr(getattr(response, "incomplete_details", None), "reason", "unknown")
        log.warning("OpenAI response incomplete, reason=%s", reason)
        analytics("incomplete_model_output", reason=reason)

    answer = (response.output_text or "").strip()
    if not answer:
        log.warning("OpenAI returned empty output")
        analytics("empty_model_output")
        return "Не нашёл эту информацию в базе знаний Whitech 😔 Напишите @MiaA_01t — она поможет разобраться."
    return answer

async def build_answer(question: str, chat_id: int) -> str:
    t0 = time.perf_counter()
    conversation = compact_history(chat_id)
    pages, queries = await retrieve(question, conversation)

    if not pages:
        analytics("not_found", latency_seconds=round(time.perf_counter() - t0, 2))
        return "Не нашёл эту информацию в базе знаний Whitech 😔 Напишите @MiaA_01t — она поможет разобраться."

    answer = await generate_answer(question, conversation, pages)
    elapsed = round(time.perf_counter() - t0, 2)
    analytics(
        "answered",
        latency_seconds=elapsed,
        source_page_ids=[p["id"] for p in pages[:3]],
        query_count=len(queries),
    )
    log.info("TIMING total=%.2fs", time.perf_counter() - t0)
    return answer

@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "Привет! Я помогатор Whitech 👋\n\n"
        "Помогу найти информацию из базы знаний: инструкции, рабочие контакты, "
        "нужные ссылки, шаблоны и пошаговые процессы.\n\n"
        "Например, спроси:\n"
        "• Где найти контакты Retention?\n"
        "• Как писать недельный отчёт?\n"
        "• Могу ли я запросить себе технику?\n\n"
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
        answer = await build_answer(question_text, message.chat.id)
    except httpx.HTTPStatusError as exc:
        log.exception("Confluence HTTP error")
        analytics("error", error_type="ConfluenceHTTP", status=exc.response.status_code)
        answer = "Не получилось обратиться к базе знаний. Напишите @MiaA_01t — она поможет разобраться 💛"
    except Exception as exc:
        log.exception("Request failed: %s", type(exc).__name__)
        analytics("error", error_type=type(exc).__name__)
        answer = "Что-то пошло не так при поиске 😔 Напишите @MiaA_01t — она поможет разобраться."

    if not answer or not answer.strip():
        log.warning("Empty answer blocked before Telegram send")
        analytics("empty_answer_blocked")
        answer = "Не нашёл эту информацию в базе знаний Whitech 😔 Напишите @MiaA_01t — она поможет разобраться."

    history[message.chat.id].append(("Пользователь", question_text[:800]))
    history[message.chat.id].append(("Помогатор", answer[:1200]))

    # This send was previously outside any try/except: if edit_text raised for any
    # reason (status message too old, "message not modified", a transient Telegram
    # API error), the exception propagated up, aiogram just logged it, and the user
    # silently got nothing — even though `answer` was computed successfully. That is
    # the most likely cause of "просто не присылает ответ".
    try:
        if len(answer) <= 4000:
            await status.edit_text(answer, disable_web_page_preview=True)
        else:
            await status.edit_text("Нашёл информацию — отправляю ниже 👇")
            for i in range(0, len(answer), 3900):
                await message.answer(answer[i:i+3900], disable_web_page_preview=True)
    except Exception:
        log.exception("Failed to deliver answer via edit_text, falling back to plain send")
        analytics("delivery_failed_fallback")
        try:
            for i in range(0, len(answer), 3900):
                await message.answer(answer[i:i+3900], disable_web_page_preview=True)
        except Exception:
            log.exception("Fallback send also failed")
            analytics("delivery_failed_final")

async def main():
    log.info(
        "Starting Whitech Helper v7.2.1-empty-guard; space=%s root=%s model=%s",
        CONFLUENCE_SPACE_KEY, ALLOWED_ROOT_PAGE_ID, OPENAI_MODEL
    )
    try:
        await dp.start_polling(bot)
    finally:
        await confluence.aclose()

if __name__ == "__main__":
    asyncio.run(main())
