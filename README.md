# Whitech Knowledge Bot v5 Fast

Keeps the v4 security boundary:
- only descendants of page `3621748974`
- only Confluence space `pmprod`
- no Jira API
- fallback to `@MiaA_01t`

Speed changes:
- 3 search variants instead of up to 5
- 5 Confluence results per query instead of 8
- maximum 5 unique pages passed to the final answer
- maximum 5,000 characters per page instead of 12,000
- 25-second end-to-end timeout
- immediate `🔎 Ищу в базе знаний Whitech…` status message

Railway variables stay the same as v4.
Start command: `python app.py`
