# Whitech Helper v7.1 — No Limit

Changes:
- removed application-level `REQUEST_TIMEOUT_SECONDS`
- removed `asyncio.wait_for(...)`
- removed explicit OpenAI SDK timeout
- keeps v7 fast architecture: no AI search-planning call
- keeps hard Confluence scope `ancestor=3621748974`
- keeps Russian-only responses, sources, conversation context and @MiaA_01t fallback

Railway:
- `REQUEST_TIMEOUT_SECONDS` is no longer used and can be deleted from Variables.
- Start command: `python app.py`

Expected startup log:
`Starting Whitech Helper v7.1-no-limit; space=pmprod root=3621748974 ...`
