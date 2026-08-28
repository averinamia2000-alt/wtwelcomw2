
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
OPENAI_MAX_OUTPUT_TOKENS = int(os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "3000"))
OPENAI_RETRY_MAX_OUTPUT_TOKENS = int(os.getenv("OPENAI_RETRY_MAX_OUTPUT_TOKENS", "5000"))
OPENAI_REASONING_EFFORT = os.getenv("OPENAI_REASONING_EFFORT", "low")

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
user_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

FAQ_PATH = os.path.join(os.path.dirname(__file__), "faq.json")
with open(FAQ_PATH, "r", encoding="utf-8") as fh:
    FAST_FAQ = json.load(fh)

FAQ_STOPWORDS = {"как","где","что","кто","куда","мне","можно","могу","ли","я","мы","вы","это","дай","дайте","нужно","надо","хочу","пожалуйста","плиз","на","в","во","из","по","к","с","и","или","а","у","про","об","о"}

FAQ_ALIASES = {
    "впн":"vpn","жира":"jira","джира":"jira","сисадмин":"sysadm","сисадмины":"sysadm",
    "админы":"sysadm","ретен":"retention","ретеншен":"retention","релокация":"relocation",
    "релокейт":"relocation","офбординг":"offboarding","онборд":"onboarding","онбординг":"onboarding",
    "новичок":"onboarding","новичка":"onboarding","нового":"new_employee","сотрудника":"employee",
    "грейд":"grade","греид":"grade","грейд":"grade","grade":"grade","мидл":"middle","миддл":"middle","мидлом":"middle","миддлом":"middle","полиграфа":"полиграф","полиграфом":"полиграф","полиграфе":"полиграф",
    "трафика":"трафик","трафику":"трафик","обучалка":"обучение","обучалку":"обучение","обучалки":"обучение",
    "порекомендовать":"рекомендация","порекомендую":"рекомендация","рекомендовать":"рекомендация",
    "моник":"equipment","монитор":"equipment","оборудование":"equipment","техника":"equipment","ноут":"equipment","ноутбук":"equipment",
    "фоллоуап":"follow-up","фоллоуапы":"follow-up","ответственных":"лпр","ответственные":"лпр"
}
FAQ_NOISE = {
    "дай","дайте","скинь","скиньте","кинь","киньте","кинуть","плиз","пожалуйста","мне","бы","где","как",
    "что","кто","куда","кому","за","про","по","на","в","во","и","а","ли","это","там","есть","посмотреть",
    "нужен","нужна","нужно","хочу","можно","инфа","инфу","заявку","заявка"
}
RU_SUFFIXES = ("ами","ями","ого","ему","ому","ими","ыми","ах","ях","ам","ям","ов","ев","ей","ой","ий","ый","ая","яя","ое","ее","ую","юю","ом","ем","ы","и","а","я","у","ю","е")
NAV_PHRASES = ("где почитать","дай ссылку","скинь ссылку","кинь ссылку","где найти","где посмотреть","обучалк","материал","раздел","что такое")
SITUATIONAL_PHRASES = ("почему","как улучшить","что делать если","что делать, если","стоит ли","помоги решить","упал","упала","упало","ухудш","конфликт","недоволен","не работает","плохо работает")

CONCEPT_GROUPS = {
    "onboarding": {"onboarding","new_employee","employee","выходит","первый","день"},
    "equipment": {"equipment","доп","дозаказ"},
    "vpn": {"vpn"},
    "polygraph": {"полиграф"},
    "lpr": {"лпр","отделам","отдел"},
}

def _faq_word(word: str) -> str:
    w = word.lower().replace("ё","е")
    if w in FAQ_ALIASES:
        return FAQ_ALIASES[w]
    if len(w) >= 7:
        for sfx in RU_SUFFIXES:
            if w.endswith(sfx) and len(w)-len(sfx) >= 4:
                stem=w[:-len(sfx)]
                return FAQ_ALIASES.get(stem, stem)
    return w

def faq_tokens(text: str) -> set[str]:
    raw = re.findall(r"[A-Za-zА-Яа-яЁё0-9._:-]+", text)
    out=set()
    for x in raw:
        low=x.lower().replace("ё","е")
        if len(low)<2 or low in FAQ_STOPWORDS or low in FAQ_NOISE:
            continue
        out.add(_faq_word(low))
    return out

def render_fast_item(item: dict) -> str:
    if item.get("answer"):
        return item["answer"].strip()
    title = item.get("title") or "Вот нужная ссылка"
    url = item.get("url", "").strip()
    intro = item.get("intro", "").strip()
    source = item.get("source", "").strip()
    parts = [intro] if intro else []
    if not parts:
        if item.get("type") == "contact": parts.append(f"Вот нужный контакт: {title} 👇")
        elif item.get("type") == "jira_link": parts.append(f"{title} 👇")
        else: parts.append(f"Вот нужный раздел: {title} 👇")
    if url: parts.append(f"🔗 {url}")
    if source and source != url: parts.append(f"📚 Источник: {source}")
    return "\n\n".join(parts).strip()

def _concept_bonus(item: dict, q_tokens: set[str]) -> float:
    corpus = faq_tokens(" ".join(item.get("keywords", []) + item.get("aliases", []) + item.get("variants", [])))
    bonus=0.0
    for concept_tokens in CONCEPT_GROUPS.values():
        if q_tokens & concept_tokens and corpus & concept_tokens:
            bonus=max(bonus, 0.16)
    return bonus

def find_fast_faq(question: str):
    q_norm = " ".join(question.lower().replace("ё","е").split())
    q_tokens = faq_tokens(question)
    situational = any(p in q_norm for p in SITUATIONAL_PHRASES)
    navigational = any(p in q_norm for p in NAV_PHRASES)
    best, best_score, best_method = None, 0.0, "none"
    for item in FAST_FAQ:
        if not item.get("enabled", True): continue
        topic_tokens = faq_tokens(" ".join(item.get("keywords", []) + item.get("aliases", [])))
        for variant in item.get("variants", []):
            v_norm = " ".join(variant.lower().replace("ё","е").split())
            v_tokens = faq_tokens(variant)
            method="token_overlap"
            if q_norm == v_norm:
                score, method = 1.0, "exact"
            elif v_norm in q_norm or q_norm in v_norm:
                score, method = 0.94, "variant"
            elif v_tokens:
                overlap = len(q_tokens & v_tokens)
                score = 0.55*(overlap/max(1,len(q_tokens))) + 0.45*(overlap/len(v_tokens))
                if overlap: score += _concept_bonus(item, q_tokens)
            else: score=0.0
            if topic_tokens and not (q_tokens & topic_tokens): score *= 0.45
            if item.get("type") == "section_link" and navigational: score += 0.08
            # Situational/diagnostic questions should not be swallowed by generic section links.
            if situational and item.get("type") == "section_link": score *= 0.50
            if score > best_score:
                best, best_score, best_method = item, min(score,1.0), method
    threshold = 0.72
    return (best, best_score, best_method) if best and best_score >= threshold else (None, best_score, best_method)

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
    "грейд": ["grade", "грейды", "греид"],
    "греид": ["grade", "грейд", "грейды"],
    "grade": ["грейд", "грейды"],
    "мидл": ["middle", "миддл"],
    "миддл": ["middle", "мидл"],
    "middle": ["мидл", "миддл"],
    "грейды": ["grade", "грейд", "греид"],
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
    out=[]
    for t in re.findall(r"[A-Za-zА-Яа-яЁё0-9._-]+", text):
        low=t.lower().replace("ё","е")
        if len(low) < 2 or low in STOPWORDS or low in FAQ_NOISE:
            continue
        out.append(_faq_word(low))
    return list(dict.fromkeys(out))

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
    cql = (
        f'space="{CONFLUENCE_SPACE_KEY}" AND type=page '
        f'AND (id={ALLOWED_ROOT_PAGE_ID} OR ancestor={ALLOWED_ROOT_PAGE_ID}) AND {text_clause}'
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
        url = f"{ATLASSIAN_BASE_URL}/wiki{webui}" if webui else (
            f"{ATLASSIAN_BASE_URL}/wiki/spaces/{CONFLUENCE_SPACE_KEY}/pages/{page_id}"
        )
        pages.append({
            "id": page_id,
            "title": item.get("title", "Confluence"),
            "url": url,
            "text": clean_html(item.get("body", {}).get("view", {}).get("value", ""))[:9000],
            "modified": item.get("version", {}).get("when", ""),
            "matched_query": query,
        })
    return pages

def rerank(pages: list[dict], question: str) -> list[dict]:
    keywords = normalize_tokens(question)
    qset=set(keywords)
    for p in pages:
        title_tokens=set(normalize_tokens(p["title"]))
        body_tokens=set(normalize_tokens(p["text"][:4000]))
        title_hits=len(qset & title_tokens)
        body_hits=len(qset & body_tokens)
        coverage=(title_hits+body_hits)/max(1,len(qset))
        p["title_hits"]=title_hits
        p["body_hits"]=body_hits
        p["coverage"]=round(coverage,3)
        p["score"] = p.get("hits", 1) * 5 + title_hits * 8 + min(body_hits,4) * 2
    return sorted(pages, key=lambda p: (p["score"], p.get("modified", "")), reverse=True)[:4]

def relevance_gate(pages: list[dict], question: str) -> tuple[list[dict], float]:
    if not pages: return [], 0.0
    q=set(normalize_tokens(question))
    if not q: return pages[:2], 1.0
    kept=[]
    for p in pages:
        if p.get("title_hits",0) >= 1 or p.get("body_hits",0) >= 2 or p.get("hits",1) >= 2:
            kept.append(p)
    top_score=max((p.get("coverage",0.0) for p in kept), default=0.0)
    return kept[:4], top_score

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

    ranked = rerank(list(dedup.values()), question)
    result, relevance = relevance_gate(ranked, question)
    log.info("TIMING retrieval=%.2fs candidates=%d selected=%d relevance=%.3f",
             time.perf_counter() - t0, len(dedup), len(result), relevance)
    for p in result:
        log.info("RETRIEVAL page=%s title=%r score=%s title_hits=%s body_hits=%s hits=%s",
                 p["id"], p["title"], p.get("score"), p.get("title_hits"), p.get("body_hits"), p.get("hits"))
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
    model_input = f"КОНТЕКСТ:\n{conversation}\n\nВОПРОС:\n{question}\n\nSOURCE:\n{context}"

    async def call_model(max_tokens: int):
        return await openai.responses.create(
            model=OPENAI_MODEL,
            instructions=instructions,
            input=model_input,
            max_output_tokens=max_tokens,
            reasoning={"effort": OPENAI_REASONING_EFFORT},
        )

    response = await call_model(OPENAI_MAX_OUTPUT_TOKENS)
    answer = (response.output_text or "").strip()

    def usage_fields(resp):
        usage = getattr(resp, "usage", None)
        out_details = getattr(usage, "output_tokens_details", None) if usage else None
        return {
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
            "reasoning_tokens": getattr(out_details, "reasoning_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        }

    status = getattr(response, "status", None)
    incomplete = getattr(response, "incomplete_details", None)
    reason = getattr(incomplete, "reason", None) if incomplete else None
    first_usage = usage_fields(response)

    # A reasoning model can spend the whole output budget on reasoning and emit no text.
    # Retry once only for that specific, recoverable condition.
    if not answer and status == "incomplete" and reason == "max_output_tokens":
        analytics("openai_retry", reason="max_output_tokens", first_max_output_tokens=OPENAI_MAX_OUTPUT_TOKENS, **first_usage)
        log.warning("OpenAI exhausted output budget; retrying once max_output_tokens=%d", OPENAI_RETRY_MAX_OUTPUT_TOKENS)
        response = await call_model(OPENAI_RETRY_MAX_OUTPUT_TOKENS)
        answer = (response.output_text or "").strip()

    log.info("TIMING final_answer=%.2fs", time.perf_counter() - t0)
    final_usage = usage_fields(response)
    analytics("openai_usage", response_status=str(getattr(response, "status", None)), reasoning_effort=OPENAI_REASONING_EFFORT, **final_usage)

    if not answer:
        status=getattr(response, "status", None)
        incomplete=getattr(response, "incomplete_details", None)
        usage=getattr(response, "usage", None)
        output=getattr(response, "output", None)
        log.warning("OpenAI returned empty output status=%r incomplete_details=%r usage=%r output_types=%r",
                    status, incomplete, usage, [getattr(x, "type", None) for x in (output or [])])
        analytics("empty_model_output", response_status=str(status), incomplete_details=str(incomplete), **final_usage)
        return "Не нашёл эту информацию в базе знаний Whitech 😔 Напишите @MiaA_01t — она поможет разобраться."
    return answer

async def build_answer(question: str, chat_id: int) -> str:
    t0 = time.perf_counter()
    faq_item, faq_score, faq_method = find_fast_faq(question)
    if faq_item:
        elapsed = round(time.perf_counter() - t0, 4)
        log.info("FAST_FAQ hit id=%s score=%.3f", faq_item["id"], faq_score)
        analytics("fast_faq", faq_id=faq_item["id"], match_method=faq_method, score=round(faq_score,3), latency_seconds=elapsed)
        return render_fast_item(faq_item)

    log.info("FAST_FAQ miss best_score=%.3f method=%s; using Confluence", faq_score, faq_method)
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
        route="confluence",
        faq_best_score=round(faq_score,3),
        faq_match_method=faq_method,
        source_page_ids=[p["id"] for p in pages[:3]],
        retrieval_scores=[p.get("score") for p in pages[:3]],
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
        async with user_locks[message.chat.id]:
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

    if len(answer) <= 4000:
        await status.edit_text(answer, disable_web_page_preview=True)
    else:
        await status.edit_text("Нашёл информацию — отправляю ниже 👇")
        for i in range(0, len(answer), 3900):
            await message.answer(answer[i:i+3900], disable_web_page_preview=True)

async def main():
    log.info(
        "Starting Whitech Helper v8.3.1-output-budget; space=%s root=%s model=%s",
        CONFLUENCE_SPACE_KEY, ALLOWED_ROOT_PAGE_ID, OPENAI_MODEL
    )
    try:
        await dp.start_polling(bot)
    finally:
        await confluence.aclose()

if __name__ == "__main__":
    asyncio.run(main())
