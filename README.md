# Whitech Helper v7.2 Recall

Fixes observed in live testing:
- Search recall: no longer relies on strict multi-word phrases.
- Uses strong single terms plus a broad OR query with synonyms.
- Confluence limit raised to 8 per query.
- Up to 9k characters/page supplied to the final answer.
- Final answer budget raised to 1200 tokens.
- Prompt explicitly requires a complete answer and forbids cutting a sentence/list mid-way.
- No application time limit.
- No AI search-planning call.
- Hard Confluence scope remains `ancestor=3621748974`.

Expected startup:
`Starting Whitech Helper v7.2-recall; ...`

Important regression:
- `Как я могу увеличить свой грейд?` should retrieve page 3518660854 / "Грейды и план развития".
- `Как писать недельный отчёт?` should finish the whole response.
- `Как уволить сотрудника?` should retrieve by `уволить`, `сотрудника`, and synonym OR query.
