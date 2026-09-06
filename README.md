# Telegram Folder Backup

A lightweight Windows-friendly desktop utility for backing up multiple local folders to Telegram.

## Features

- Multiple independent Backup Jobs
- One local folder per job
- Manual and daily scheduled backups
- Selective and incremental backup behavior
- Telegram chat discovery and connection test
- Optional `General` destination or dedicated Forum Topic per job
- Automatic creation of a main Topic and a separate History Topic
- Versioned backups: when a file changes, the previous Telegram message is copied to History and the current Topic keeps only the latest version
- Deleted local files are preserved in the History Topic with a deletion record
- Previous Telegram versions are moved with `copyMessage`; the application does not download and re-upload them
- Local manifest storing Telegram message IDs and file versions
- Retry/backoff for transient Telegram/network failures
- Protection against concurrent backup runs

## Requirements

- Python 3.10+
- A Telegram bot token from `@BotFather`
- `requests`
- Tkinter (included with the standard Windows Python distribution)

For Topic mode, the destination must be a Telegram forum supergroup. The bot must have permission to manage topics; it also needs message deletion permission if the main Topic is to contain only the latest versions. Telegram's Bot API supports `createForumTopic`, `message_thread_id`, and `copyMessage`, so historical versions can be copied server-side without downloading the file again.

## Installation

```bash
git clone https://github.com/Kavehemrah/telegram-backup-bot.git
cd telegram-backup-bot
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python backup_bot.py
```

## Configuration

The UI manages Backup Jobs. Each job contains:

- folder
- Telegram chat
- destination: `topic` or `general`
- main Topic name
- History Topic name
- daily schedule
- enabled/disabled state

Existing `.env` settings from earlier versions are migrated to a single Backup Job on first launch.

Generated local state is stored in `backup_jobs.json` and `backup_manifest.json`; both are ignored by Git.

## Versioned Topic behavior

For a job configured for Topic mode, the application creates two Topics:

```text
Finance
├── invoice.pdf       <- latest version only
├── salary.xlsx
└── contract.pdf

Finance History
├── invoice.pdf       <- previous versions
├── salary.xlsx
└── deleted-file.pdf  <- preserved after local deletion
```

When `invoice.pdf` changes, the previous message is copied to the History Topic using Telegram's `copyMessage` API. The old message in the main Topic is then deleted when the bot has the required permission, and the new local file is uploaded once to the main Topic. The old file bytes are never downloaded to the computer for this transition.

## Security notes

- Never publish the bot token.
- Keep `.env` outside version control.
- Generated JSON state can contain local file paths and Telegram message IDs.
- For real access separation, use separate private groups rather than relying on Topic-level permissions; Telegram Topics do not provide independent per-topic member ACLs.

## Project structure

```text
telegram-backup-bot/
├── backup_bot.py
├── backup_jobs.py
├── telegram_forum.py
├── tests/
│   ├── test_backup_state.py
│   ├── test_forum_and_jobs.py
│   └── test_telegram_requests.py
├── .env.example
├── .gitignore
├── LICENSE
├── README.md
└── requirements.txt
```

The project intentionally remains small. The backup engine, persistent jobs, Telegram Forum operations, and UI are separated only where the new functionality requires it.

## Roadmap

- Windows packaging (`.exe`)
- Restore selected file/version from Telegram
- Better backup reports and retention policies
- Optional private-group destinations for sensitive jobs

## License

MIT. See `LICENSE`.
