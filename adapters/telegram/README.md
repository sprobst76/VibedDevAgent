# Telegram Adapter (P1)

Aufgaben:
- Commands: `approve`, `stop`, `status`
- Gleiche State-Guards wie Matrix
- Zugriff nur fuer erlaubte Chat-IDs

Implementiert in:
- `adapters/telegram/commands.py` (Command-Parser)
- `adapters/telegram/controller.py` (Control-Flow mit Engine-Guards)
- `adapters/telegram/config.py` (Token + allowed chat IDs aus Env)
