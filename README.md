# Whitech Helper v7.2.1 Empty Guard

Hotfix for:
`TelegramBadRequest: Bad Request: message text is empty`

The bot now has two guards:
1. Immediately after OpenAI output extraction.
2. Immediately before sending/editing a Telegram message.

If OpenAI returns empty output, the user receives the normal Mia fallback instead of the bot crashing.

All v7.2 recall/search improvements remain unchanged.
Expected startup:
`Starting Whitech Helper v7.2.1-empty-guard; ...`
