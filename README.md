# Whitech Helper v8.4.4 — AI Router Fix

Current routing:
`question -> guided/AI fast router -> FAQ -> Confluence/OpenAI fallback`

## What is included
- Hard Confluence scope: PM Products root `3621748974` + descendants only
- Guided UX menus
- Fast FAQ with direct answers, contacts, Jira links and Confluence section links
- Weekly Global / Weekly Operational routing
- Templates menu
- KDP quick links
- SysAdmin contact
- Correct FAME definition
- AI tools for PM Whitech FAQ
- Deterministic AI router before fuzzy FAQ / Confluence
- OpenAI output-budget guard and retry for reasoning-only incomplete responses

## AI FAQ
Source:
https://01tech.atlassian.net/wiki/spaces/pmprod/pages/3811803379/AI+tools+for+PM+Whitech

Dedicated topics:
- allowed AI tools
- model / effort selection
- chat/context and hallucination guidance
- `/goal`
- skills/plugins/connectors/MCP
- automation examples
- AI security
- code review

For AI-related questions the bot should normally answer via `fast_ai_faq`
without calling Confluence or OpenAI.

## Expected startup log
`Starting Whitech Helper v8.4.4-ai-router-fix; ...`

## Files
- `app.py` — bot
- `faq.json` — fast FAQ
- `.env.example` — environment variable example
- `requirements.txt` — Python dependencies

Jira API is not called. Approved Jira URLs are stored as static quick links.
