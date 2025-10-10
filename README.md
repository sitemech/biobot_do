# Telegram бот для DigitalOcean AI Agent

Проект содержит асинхронного Telegram-бота, который перенаправляет сообщения пользователей в DigitalOcean AI Agent и отправляет ответы обратно в чат.

## Возможности

- Команда `/start` создаёт новую сессию с агентом и выводит краткую справку.
- Команда `/new` сбрасывает текущий диалог и открывает новую сессию у агента.
- Любые текстовые сообщения пересылаются DigitalOcean AI Agent; ответы выводятся напрямую в Telegram.
- Автоматическое повторное создание сессии при первом сообщении в чате.

## Требования

- Python 3.11+
- Зарегистрированный Telegram-бот (BotFather) и токен `TELEGRAM_BOT_TOKEN`.
- Активный DigitalOcean AI Agent и персональный API-ключ `DO_API_KEY` с доступом к AI Agents.
- Идентификатор агента `DO_AGENT_ID` (UUID), который можно посмотреть в панели DigitalOcean или через API.

## Быстрый старт

1. Склонируйте репозиторий и перейдите в каталог проекта.
2. Создайте виртуальное окружение и установите зависимости:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. Создайте файл `.env`, скопировав шаблон:

   ```bash
   cp .env.example .env
   ```

   Затем отредактируйте `.env`, подставив собственные значения:

   ```ini
   TELEGRAM_BOT_TOKEN=ваш_бот_токен
   DO_API_KEY=ваш_do_api_key
   DO_AGENT_ID=uuid_агента
   # Необязательно: DO_API_BASE_URL=https://api.digitalocean.com/v2/ai
   # Необязательно: DO_API_TIMEOUT=30
   # Необязательно: DO_API_RATE_QPS=0.2  # ограничивает количество запросов в секунду
   # Необязательно: DO_API_RATE_BURST=2   # сколько запросов разрешено "залпом"
   # Необязательно: DO_API_RATE_COOLDOWN=10  # пауза (сек.) после ошибки 429
   ```

4. Запустите бота:

   ```bash
   python main.py
   ```

   Можно передать путь к альтернативному `.env` файлу аргументом: `python main.py path/to/.env`.

После запуска бот начнёт polling и отвечать на входящие сообщения. Убедитесь, что вебхук у бота отключён (`deleteWebhook`), иначе polling работать не будет.

## Развёртывание на DigitalOcean

- Создайте Droplet (например, Ubuntu 22.04) или используйте App Platform.
- Установите зависимости (Python, virtualenv) и скопируйте код.
- Настройте переменные окружения через `export` или `.env`.
- Запустите бота как systemd-сервис либо через процесс-менеджер (например, `pm2`, `supervisor`).

Пример unit-файла systemd (`/etc/systemd/system/telegram-ai-bot.service`):

```ini
[Unit]
Description=Telegram bot for DigitalOcean AI Agent
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/telegram-ai-bot
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/telegram-ai-bot/.venv/bin/python /opt/telegram-ai-bot/main.py /opt/telegram-ai-bot/.env
Restart=on-failure
User=bot
Group=bot

[Install]
WantedBy=multi-user.target
```

После добавления файла выполните:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now telegram-ai-bot.service
```

## Обработка ошибок

Если DigitalOcean API временно недоступен или возвращает ошибку, бот отправит пользователю уведомление и запишет подробности в лог.

### Как избежать превышения лимитов DigitalOcean

DigitalOcean ограничивает частоту обращений к агенту, особенно при использовании публичных эндпоинтов (`*.agents.do-ai.run`). В бот встроен адаптивный токен-бакет, который автоматически ставит паузу после получения ответа `429 Too Many Requests` и учитывает заголовок `Retry-After`, если он передан. Чтобы дополнительно снизить нагрузку:

- Уменьшите значение `DO_API_RATE_QPS` (например, до `0.2`, чтобы не превышать один запрос каждые ~5 секунд).
- Установите небольшой `DO_API_RATE_BURST` (1–2), чтобы запретить быстрые серии запросов.
- Настройте `DO_API_RATE_COOLDOWN`, чтобы гарантированно ждать несколько секунд после срабатывания лимита, даже если в ответе нет `Retry-After`.

Если вы используете DigitalOcean App Platform или Function, убедитесь, что для агента включён план, позволяющий требуемое количество запросов. В панели DigitalOcean в разделе **AI > Agents > Usage** можно отследить оставшийся лимит и при необходимости перейти на более высокий тариф.

## Лицензия

Проект распространяется под лицензией MIT (при необходимости обновите раздел).
