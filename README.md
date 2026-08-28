# Whitech Helper v8 — Fast FAQ

Routing: `question -> FAST FAQ -> Confluence -> OpenAI`.

Fast FAQ answers require no OpenAI/Confluence request and are stored in `faq.json`.
Each topic contains many question variants, keywords and an approved answer.

Included:
- grade growth
- Weekly Global Report + clarification for generic weekly report
- dismissal process + direct Jira dismissal form
- VPN Jira form
- extra equipment Jira form + remote employee condition
- onboarding

FAQ matcher is conservative. If confidence is low, the existing Confluence + OpenAI flow runs.

Hard Confluence fallback scope remains `ancestor=3621748974`. No Jira API is called.

Logs:
`FAST_FAQ hit id=... score=...`
or
`FAST_FAQ miss best_score=...; using Confluence`

Expected startup:
`Starting Whitech Helper v8-fast-faq; ...`
