# Whitech Helper v8.1 — Fast Router

Fast layer now supports data-driven:
- `answer`
- `jira_link`
- `section_link`

Routing:
`question -> fast router -> Confluence/OpenAI fallback`

To add or edit most fast topics later, edit only `faq.json`.

Schema:
- id
- type
- enabled
- variants
- keywords
- aliases
- answer (for answer)
- title/url/intro/source (for links)

Includes expanded question variability and navigation links for traffic/FAME, polygraph, reporting, vacation, KDP, tools/access, legal, business trips, referrals, office, chats, LPR, templates, task setting, MFU, management, 1:1, iGaming, GGR/NGR, domains, PM materials, licenses, STUFF and more.

All Confluence section links were selected from pages returned by the hard allowed-tree query:
`space="pmprod" AND type=page AND ancestor=3621748974`.

Jira API is not called. Approved Jira links are static fast links.

## v8.2 Robust Router
- conversational FAQ normalization and lightweight Russian word-form handling
- expanded natural-language variants
- noisy retrieval words removed
- `contact` type supported by renderer for future verified contact entries
- richer diagnostics for empty OpenAI Responses output
- no unverified SysAdm/Retention contacts were hardcoded


## v8.3.1
- Raises the final Responses API output budget to 3000 tokens.
- Uses low reasoning effort for the internal Q&A answer step.
- Retries once with 5000 tokens only when status=incomplete and reason=max_output_tokens.
- Logs input/output/reasoning token usage.
- Adds typo/slang normalization for греид/грейд/grade and мидл/миддл/middle.
