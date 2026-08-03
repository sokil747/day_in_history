# Day in History — Telegram Bot

Telegram bot that shows the record for today's date from a Google Sheet after clicking the inline button "День в Історії".

## Setup

1. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

2. Create your `.env` from the example:

   ```bash
   cp .env.example .env
   ```

3. Create a Google service account:
   - Go to [Google Cloud Console](https://console.cloud.google.com) → create a project (or select existing).
   - Enable **Google Sheets API** for the project.
   - Go to **IAM & Admin → Service Accounts** → **Create Service Account** → grant no extra roles.
   - Create a key: open the service account → **Keys** → **Add Key** → **Create new key** → **JSON** → save as `credentials/service_account.json`.
   - Share your spreadsheet with the service account email (the one shown in the service account details, e.g. `bot@...iam.gserviceaccount.com`) as **Editor** (or Viewer).
   - Put the file at the path you set in `GOOGLE_CREDENTIALS_PATH` (default `./credentials/service_account.json`).

4. Create your Telegram bot with [@BotFather](https://t.me/BotFather), copy the token into `BOT_TOKEN` in `.env`.

5. Make sure your sheet's first column contains dates in `dd/mm/yyyy` format (e.g. `03/08/2026`) and the history text is in the following columns.

## Run

```bash
python bot.py
```

The bot shows a "День в Історії" inline button. When clicked, it finds the row whose date equals today (`dd/mm/yyyy`) and replies with the record text.

## Deploy on a VPS as a systemd service

1. On the VPS, install the bot:

   ```bash
   sudo apt update && sudo apt install -y git python3 python3-venv
   git clone <YOUR_REPO_URL> /opt/day_in_history
   cd /opt/day_in_history
   python3 -m venv venv
   venv/bin/pip install -r requirements.txt
   ```

2. Create the config files. The credentials folder is kept but its content is **not** in the repo — copy it to the VPS:

   ```bash
   mkdir -p /opt/day_in_history/credentials
   # scp from your machine:
   # scp credentials/service_account.json user@vps:/opt/day_in_history/credentials/
   cp .env.example /opt/day_in_history/.env
   # edit /opt/day_in_history/.env and set BOT_TOKEN (GOOGLE_CREDENTIALS_PATH
   # should point to the file you copied, e.g. ./credentials/service_account.json)
   ```

3. Create the systemd unit:

   ```bash
   sudo tee /etc/systemd/system/day-in-history.service > /dev/null <<'EOF'
   [Unit]
   Description=Day in History Telegram Bot
   After=network-online.target
   Wants=network-online.target

   [Service]
   Type=simple
   User=www-data
   WorkingDirectory=/opt/day_in_history
   ExecStart=/opt/day_in_history/venv/bin/python bot.py
   Restart=always
   RestartSec=5
   EnvironmentFile=/opt/day_in_history/.env

   [Install]
   WantedBy=multi-user.target
   EOF
   ```

4. Make the files readable by the service user and start it:

   ```bash
   sudo chown -R www-data:www-data /opt/day_in_history
   sudo chmod 600 /opt/day_in_history/.env
   sudo systemctl daemon-reload
   sudo systemctl enable --now day-in-history
   ```

5. Check the service:

   ```bash
   sudo systemctl status day-in-history
   sudo journalctl -u day-in-history -f
   ```

To stop/restart later:

```bash
sudo systemctl restart day-in-history
sudo systemctl stop day-in-history
```
