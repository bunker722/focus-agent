# Focus Agent

Telegram-бот который не даёт распыляться.

## Переменные окружения

```
TELEGRAM_TOKEN=      # BotFather
ANTHROPIC_API_KEY=   # claude
OPENAI_API_KEY=      # TTS голос
ALLOWED_USER_ID=     # твой Telegram user ID (узнать у @userinfobot)
```

## Локально

```bash
pip install -r requirements.txt
python bot.py
```

## Railway

1. New project → Deploy from GitHub
2. Add variables выше
3. Start command: `python bot.py`

## Команды бота

- Любой текст → анализ идеи, ответ голосом
- `/output <что сделал>` → зафиксировать реальный результат
- `/status` → сколько дней без output
