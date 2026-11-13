# Security

This is a preview export. It contains **no credentials, cookies, tokens, chat databases, or model weights**.

## Do not expose

- `family/` dashboard (8899): no authentication by default
- `remote_agent`/computer-use endpoints (8765): local control surface
- BEAM gateway debug endpoints (8920): `/debug/*` may trigger outbound actions
- QQ / OneBot tokens: use environment variables, never commit

## Before running

1. Replace all `REPLACE_ME` placeholders with environment variables.
2. Keep services bound to `127.0.0.1`.
3. Review automatic publishing paths (B站 comments/dynamics, QZone) before enabling.
4. Do not commit `data/`, `*.log`, `*.bak*`, `*.sqlite3`, credentials, or screenshots.

## Reporting

Open an issue without secrets; do not paste tokens or private chat logs.