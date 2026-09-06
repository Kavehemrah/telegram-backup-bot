# Telegram Folder Backup

A lightweight Windows-friendly desktop utility for backing up multiple local folders to Telegram.

## Features

- Multiple independent Backup Jobs
- Persistent `ACTIVE` / `PAUSED` state per job
- One local Folder Identity per folder, independent from its Job lifecycle
- A folder keeps its Telegram Topic mapping after its Job is deleted
- Re-adding the same folder reuses its previous Telegram Topic
- Manual and daily scheduled backups; scheduler runs only active jobs
- `Entire folder` or `Selected files` backup mode
- Persistent file selection per Job; new files are not automatically selected in selective mode
- Independent `Replace files` and `History` settings
- One central History Topic for the whole destination forum
- One current/live Topic per folder
- Versioned backups: when a file changes, its previous message can be copied to the central History Topic and the live message can be deleted when replacement is enabled
- Deleted local files can be preserved in the central History Topic
- Previous Telegram versions are moved with `copyMessage`; the application does not download and re-upload them
- Local manifest storing Telegram message IDs and file versions
- Retry/backoff for transient Telegram/network failures
- Protection against concurrent backup runs

## Requirements

- Python 3.10+
- A Telegram bot token from `@BotFather`
- `requests`
- Tkinter (included with the standard Windows Python distribution)

For Topic mode, the destination must be a Telegram forum supergroup. The bot needs permission to manage topics and message deletion if the live Topic should contain only the latest versions.

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

## Job configuration

Each Backup Job stores:

- folder and persistent Folder Identity
- Telegram chat
- destination: `topic` or `general`
- main Topic name
- daily schedule
- active/paused state
- backup mode: `Entire folder` or `Selected files`
- selected file paths when selective mode is used
- `Replace files`
- `History`

Deleting a Job does not delete its Folder Identity, manifest, or Telegram Topic. Creating a new Job for the same folder finds that identity and reuses its Topic ID.

## Topic behavior

The intended Topic layout is:

```text
Folder A
├── invoice.pdf       <- latest/current version
├── salary.xlsx
└── contract.pdf

Folder B
├── report.pdf
└── plan.xlsx

Backup History       <- one central history Topic
├── previous versions from Folder A
├── previous versions from Folder B
└── deleted files
```

When a current file changes:

1. If History is enabled, the previous Telegram message is copied to `Backup History` with metadata.
2. If Replace is enabled, the old live message is deleted.
3. The new local file is uploaded to the folder's live Topic.

This keeps the two switches independent. For example, Replace can be disabled while History remains enabled, in which case the old live message is retained and the previous version is also archived.

## Selective backup

Choose `Selected files` and tick only the files that the Job should back up. The selection is stored with that Job. A new file appearing later is not included until it is explicitly selected.

## Testing

Run the unit tests with:

```powershell
python -m unittest discover -s tests -v
```

The CI workflow also compiles the source and runs the test suite on Python 3.11 and 3.12.

## Security notes

- Never publish the bot token.
- Keep `.env` outside version control.
- Generated JSON state can contain local file paths and Telegram message IDs.
- Telegram Topics do not provide independent member ACLs; use separate private groups for real access separation.

## Project structure

```text
telegram-backup-bot/
├── backup_bot.py
├── backup_jobs.py
├── telegram_forum.py
├── tests/
├── .env.example
├── .gitignore
├── LICENSE
├── README.md
└── requirements.txt
```

The project intentionally remains small. The backup engine, persistent state, Telegram Forum operations, and UI are separated only where the functionality requires it.

## Roadmap

- Windows packaging (`.exe`)
- Restore selected file/version from Telegram
- Better backup reports and retention policies

## License

MIT. See `LICENSE`.
