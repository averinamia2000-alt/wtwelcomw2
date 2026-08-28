# Whitech Knowledge Bot v4

Hard scope:
- Only descendant pages under Confluence page 3621748974 ("База") are searchable.
- Content elsewhere in pmprod is excluded at the CQL query level.
- No Jira API calls. Jira links can be returned only when present in allowed Confluence pages.
- If no supported answer is found, the bot directs the user to @MiaA_01t.
- Updated /start greeting.

Railway: add `ALLOWED_ROOT_PAGE_ID=3621748974` (a default is also built into app.py).
Start command: `python app.py`.
