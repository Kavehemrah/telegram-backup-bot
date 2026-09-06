# Telegram Folder Backup

A lightweight Windows-friendly desktop utility for backing up files from a local folder to Telegram.

The application provides a simple Tkinter interface for selecting a backup folder, discovering Telegram chats, sending pending files, viewing activity, and running a daily backup schedule.

## Features

- Manual backup of all pending files
- Selective backup of individual files
- Incremental backup based on file modification time
- Daily scheduled backup at a configurable `HH:MM` time
- Telegram connection test
- Telegram chat discovery through `getUpdates`
- Upload progress and activity log
- Local backup history
- Configuration stored in `.env`
- No third-party database required

## Requirements

- Python 3.10+
- A Telegram bot token from `@BotFather`
- A Telegram chat where the bot is allowed to send documents
- `requests`

Tkinter is part of the standard Python distribution on Windows. On Linux, install the platform package provided by your distribution if Tkinter is not already available.

## Installation

Clone the repository and create a virtual environment:

```bash
git clone https://github.com/Kavehemrah/telegram-backup-bot.git
cd telegram-backup-bot
python -m venv .venv
```

Activate it on Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Copy `.env.example` to `.env`, then fill in the values. The application can also create/update `.env` from its UI.

Run:

```bash
python backup_bot.py
```

## Configuration

```dotenv
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
BACKUP_FOLDER=C:\\Backups\\MyProject
SCHEDULE_TIME=23:00
```

Do not commit `.env`. It is intentionally ignored by Git.

## How incremental backup works

The application keeps a local `backup_history.json` file. Each successful upload records the file path, its modification timestamp, and the upload time. A file is considered pending again when its modification timestamp changes.

This is intentionally a lightweight local state mechanism rather than a full backup database.

## Telegram chat discovery

The **Load chats** action uses Telegram's `getUpdates` method. To make a chat discoverable, send a message to the bot or otherwise generate an update that contains the target chat, then load the chats again.

For production deployments, a fixed `TELEGRAM_CHAT_ID` is preferable to relying on discovery every time.

## Security notes

- Never publish your bot token.
- Keep `.env` outside version control.
- Treat `backup_history.json` as local application state; it may contain local file paths.
- The bot must have permission to send documents to the selected chat.

## Project structure

```text
telegram-backup-bot/
├── backup_bot.py
├── .env.example
├── .gitignore
├── LICENSE
├── README.md
└── requirements.txt
```

The current application intentionally remains small and dependency-light. The next architectural step is to separate the backup engine, Telegram client, persistence, scheduler, and UI into independent modules without changing user-facing behavior.

## Roadmap

- Separate core backup logic from the Tkinter UI
- Add automated unit tests for backup state and file selection
- Improve scheduler reliability
- Prevent concurrent backup jobs
- Add retry/backoff handling for transient Telegram/network failures
- Improve handling of renamed or deleted files
- Add packaging for Windows
- Add GitHub Actions quality checks

## License

MIT. See `LICENSE`.
