# Whitech Helper v6

Target version based on agreed requirements.

## Behavior
- Russian-only, friendly Whitech helper.
- Natural-language questions for processes, contacts, instructions, Jira/external links and documents.
- Medium answer: direct answer + confirmed steps + relevant link.
- Always cites the used Confluence source(s).
- Clarifies genuinely ambiguous questions instead of guessing.
- Keeps a short in-memory conversation context for follow-ups.
- If missing/conflicting evidence: routes to `@MiaA_01t`.
- Shows `🔎 Ищу в базе знаний Whitech…`.

## Hard Confluence boundary
Every live Confluence search contains:
`ancestor=3621748974`

Therefore only descendants of the approved root are read. New descendants are picked up automatically; moved-out pages stop matching automatically. The bot does not query Jira API.

## Speed
- 3 parallel Confluence searches
- local reranking (no extra AI reranking call)
- max 5 pages / ~5.5k chars each
- 10-second end-to-end timeout by default
- timing logs: `search_plan`, `retrieval_rerank`, `final_answer`, `total`

## Analytics
Structured `ANALYTICS` records are written to Railway logs:
- answered / not_found / clarification / timeout / error
- intent
- latency
- source page IDs

No Telegram username/name/phone/email is written to analytics logs. Full question text is also excluded from analytics.

## Railway variables
Use `.env.example`. Existing v5 variables stay the same. Optional:
`REQUEST_TIMEOUT_SECONDS=10`

## Regression tests
1. Как уволить сотрудника?
2. Где найти контакты ретена?
3. Могу ли я запросить себе технику?
4. Как проходит онбординг нового сотрудника?
5. Как я могу увеличить свой грейд?
6. Куда писать КДП?
7. Как создать заявку на релокейт?
8. Как уйти в отпуск?
9. Дай контакт сис админов.
10. Как писать недельный отчет?
11. Как оформить заявку?  ← should clarify
