# telegram_news_bot

Telegram bot that tracks 관심 종목 and sends a simple market report on demand.

## Requirements
- Python 3.13+
- Telegram bot token
- OpenAI API key (for news summaries)

## Setup
1) Create a `.env` file in the project root:

```env
TELEGRAM_BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN
OPENAI_API_KEY=YOUR_OPENAI_API_KEY
# Optional: preconfigured chat id for internal testing
TELEGRAM_CHAT_ID=
# Optional: skip loading KRX listing CSV
SKIP_KRX_LISTING=false
```

2) Install dependencies (example using uv):

```bash
uv sync
```

If you are not using uv, install with pip:

```bash
pip install -r <(python -m piptools compile pyproject.toml)
```

3) (Optional) Provide KRX listing CSV:

The bot expects `file/data_0147_20260105.csv` to map Korean names to tickers.
If you do not have this file, set `SKIP_KRX_LISTING=true` in `.env`.

## Run
```bash
python main.py
```

## Commands (Telegram)
- `/help` - show help message
- `/add <ticker or Korean name>` - add to 관심 종목
- `/remove <ticker or Korean name>` - remove from 관심 종목
- `/list` - show 관심 종목 list
- `/report` - generate a report for 관심 종목

## Data files
- `interest_stocks.json` stores 관심 종목 per chat id.

## Notes
- Only one bot instance should run at a time. Running multiple instances with the same token will cause a `Conflict: terminated by other getUpdates request` error.
