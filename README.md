# Whitech Knowledge Bot v3

## What's new
- AI generates 3–5 short search queries from a natural-language question.
- Searches only Confluence space `pmprod`.
- No Jira API calls.
- Jira links may be returned only if present in Confluence.
- Deduplicates/ranks pages found by multiple search variants.
- Answers only from retrieved content.
- Railway logs show the generated search plan.

## Railway Variables
Keep the same variables as v2:
- TELEGRAM_BOT_TOKEN
- OPENAI_API_KEY
- ATLASSIAN_BASE_URL=https://01tech.atlassian.net
- ATLASSIAN_EMAIL
- ATLASSIAN_API_TOKEN
- CONFLUENCE_SPACE_KEY=pmprod
- OPENAI_MODEL=gpt-5-mini

## Start
python app.py

## Good tests
- Кто такой PM в Whitech?
- Мне нужно уволить сотрудника, что делать и куда писать?
- К кому обратиться по Retention?
- Как передать запрос в CC?
- Где найти шаблон устава проекта?
