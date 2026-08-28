# Whitech Knowledge Bot v2

V2 intentionally searches only Confluence space `pmprod`.
It does NOT query Jira. Jira links can be returned only when they are already present in Confluence.

## Railway Variables

Required:
- TELEGRAM_BOT_TOKEN
- OPENAI_API_KEY
- ATLASSIAN_BASE_URL=https://01tech.atlassian.net
- ATLASSIAN_EMAIL
- ATLASSIAN_API_TOKEN
- CONFLUENCE_SPACE_KEY=pmprod

Optional:
- OPENAI_MODEL=gpt-5-mini

## Start command

`python app.py`

## Test

After deployment:
1. Send `/start`
2. Send `Кто такой PM в Whitech?`
3. If an error occurs, the Telegram response now names the error class and Railway Logs contain the traceback.
