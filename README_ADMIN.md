# Admin Panel (Django)

## Setup
```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver 0.0.0.0:8000
# open http://localhost:8000/admin/  -> Events / Advertisements / Premium users
```

## Import from Google Sheets
```bash
python manage.py sync_from_sheets   # imports 508 events + 1 ad (clears & reimports)
```
The bot now reads from `db.sqlite3` (not Google Sheets). Run the command again whenever the sheet changes, or use the **Sync from Google Sheet** action inside Django admin (list view → select any row → Actions).

## Premium users
`core_premiumuser` table: `telegram_id`, `full_name`, `email`, `phone`.
Manage in **Admin → Premium users**. `Week`/`Month` buttons appear only to premium users + `ADMIN_IDS` from `.env`.

## Deploy on VPS
Add second systemd unit for Django:
```bash
sudo tee /etc/systemd/system/day-in-history-admin.service > /dev/null <<'EOF'
[Unit]
Description=Day in History Django Admin
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
User=www-data
WorkingDirectory=/var/www/day-in-history
ExecStart=/var/www/day-in-history/venv/bin/gunicorn admin_site.wsgi:application --bind 127.0.0.1:8001
Restart=always
EnvironmentFile=/var/www/day-in-history/.env
[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload && sudo systemctl enable --now day-in-history-admin
# put nginx in front of :8001 if needed
```
