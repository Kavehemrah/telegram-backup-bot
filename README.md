# Telegram Folder Backup

A lightweight Windows-friendly desktop utility for backing up multiple local folders to Telegram.

## Features

- Multiple independent Backup Jobs
- Persistent `ACTIVE` / `PAUSED` state per Job
- One local Folder Identity per folder, independent from Job lifecycle
- A folder keeps its Telegram Topic mapping after its Job is deleted
- Re-adding the same folder reuses its previous Telegram Topic
- Manual and daily scheduled backups; scheduler runs only active Jobs
- `Entire folder` or `Selected files` backup mode
- Persistent file selection per Job; new files are not automatically selected in selective mode
- Independent `Replace files` and `History` settings
- One central History Topic for the whole destination forum
- One current/live Topic per folder
- Versioned backups and deleted-file history
- Retry/backoff for transient Telegram/network failures
- Protection against concurrent backup runs
- PySide6 product UI organized into Dashboard, Jobs, and Help & Settings tabs
- Bilingual UI and Help: Persian / English
- Dedicated selectable Chat and File dialogs
- Daily Scheduler with visible next-run status
- Optional Windows auto-start through Windows Task Scheduler
- Persistent missed-job policy after Windows startup

## Requirements

- Python 3.10+
- A Telegram bot token from `@BotFather`
- `requests`
- `PySide6` 6.8+

For Topic mode, the destination must be a Telegram forum supergroup. The Bot needs permission to manage Topics and delete messages when replacement is enabled.

## Installation

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run_backup_ui.py
```

`run_backup_ui.py` is the desktop entry point. The current product UI is `backup_ui_product.py`; `backup_bot.py` remains the backup engine and Telegram operation layer.

## First-time setup

Open `Help & Settings` and follow the Quick Start Guide:

1. Create a Bot with `@BotFather` and copy the Token.
2. Create a Telegram Supergroup and enable Topics.
3. Add the Bot and grant the permissions required for Topic management and message deletion.
4. Send a message in the group so it appears in `getUpdates`.
5. Use `Load chats` and select the destination Chat.
6. Create a Job, choose the folder, backup mode, and daily time.
7. Create/connect the Job Topic once.
8. Start Scheduler and optionally enable Windows auto-start.

The Setup Status panel reports whether the Bot Token, Chat, and active Jobs are configured.

## Scheduling

Set a Job time such as `12:00` and enable `Enabled for Scheduler`. The built-in Scheduler checks every 10 seconds and runs only enabled Jobs. The Dashboard shows the next scheduled run.

The application can register itself with Windows Task Scheduler so it starts automatically when the current Windows user logs in. This means the built-in Scheduler is available after each login without requiring the user to start the application manually.

The Help & Settings tab provides three missed-job policies for a PC that was off at the scheduled time:

- `Never`: skip a missed schedule.
- `Run on first check`: run the missed Job on the first Scheduler check after startup.
- `Only within delay window`: run it only when the configured delay has not expired.

The application stores the last successful Job run locally to prevent duplicate same-day executions.

## Selective backup

Choose `Selected files` and use `Select / manage files`. Each file has a real Qt checkbox, plus Search, Select All, Clear, and explicit confirmation. The selected file paths are stored per Job. A newly created file is not automatically selected later.

## Safety around Job editing

Selecting a different folder while an existing Job is selected no longer silently changes that Job. The UI asks whether to edit the selected Job, create a new Job, or cancel.

Deleting a Job does not delete its Folder Identity, manifest, or Telegram Topic.

## Telegram chat selection

`Load chats` reads available chats from Telegram `getUpdates`. The application opens a dedicated dialog and the user explicitly chooses the destination Chat.

## Testing

```powershell
python -m unittest discover -s tests -v
```

The CI workflow compiles the source and runs the test suite on Python 3.11 and 3.12.

## Security notes

- Never publish the Bot Token.
- Keep `.env` outside version control.
- Generated JSON state can contain local file paths and Telegram message IDs.
- Telegram Topics do not provide independent member ACLs; use separate private groups for real access separation.

## Project structure

```text
telegram-backup-bot/
├── backup_bot.py
├── backup_jobs.py
├── backup_ui.py
├── backup_ui_v2.py
├── backup_ui_product.py
├── run_backup_ui.py
├── telegram_forum.py
├── tests/
├── .env.example
├── .gitignore
├── LICENSE
├── README.md
└── requirements.txt
```

The project intentionally remains small. The UI is separated from the backup engine, persistence remains lightweight, and Windows integration is handled from the desktop application.

## Roadmap

- Windows packaging (`.exe`) with a polished installer
- Restore selected file/version from Telegram
- Better backup reports and retention policies

## License

MIT. See `LICENSE`.
