# Security

This repository contains an **emotion loop framework preview**. It does not contain private chat data, account credentials, or generated media.

## Local-only surfaces

- `laya_emotion/emotion_server.py` (8901): no authentication
- `gateway/beam_gateway.py` (8920): no authentication; `/debug/*` may have side effects
- Both should bind to `127.0.0.1` only. Never expose them directly to the public internet.

## Secrets

- Use `BEAM_SECRETS_FILE` or environment variables for API keys.
- Never commit `gateway_secrets.json`, tokens, cookies, or `.env`.
- The repository uses placeholders such as `REPLACE_ME` and `%MODEL_HOME%`; replace them locally.

## Model files

- `laya_emotion/emotion_cls.pt` is a small classifier head, not a full model.
- The laya encoder is loaded from `LAYA_HOME`; its license and weights are not included here.

## If you deploy

1. Put a reverse proxy with authentication in front of 8901/8920.
2. Restrict access to localhost/firewall.
3. Add timeouts, rate limits, and logging.
4. Review the license before redistribution or commercial use.

## Reporting

Open an issue without secrets. Do not paste API keys or private logs.