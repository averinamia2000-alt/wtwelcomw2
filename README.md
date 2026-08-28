# Whitech Helper v7 Fast

## Main speed change
The AI search-planning call is removed completely.

Flow:
`question -> local deterministic query expansion -> parallel Confluence -> local rerank -> ONE OpenAI answer call`

This removes the ~6–8 second search-plan model call and prevents duplicate AI-generated queries.

## Timeout
Default end-to-end timeout: `75` seconds.
The OpenAI client call itself has a 60-second timeout.

If Railway already has `REQUEST_TIMEOUT_SECONDS`, set it to:
`75`

## Security
Every Confluence search still includes:
`ancestor=3621748974`

Only descendants of the approved root are read. No Jira API.

## UX
/start examples:
- Где найти контакты Retention?
- Как писать недельный отчёт?
- Могу ли я запросить себе технику?

Fallback: `@MiaA_01t`.

## Logs
Look for:
- `Local search queries: [...]`
- `TIMING retrieval=...`
- `TIMING final_answer=...`
- `TIMING total=...`
