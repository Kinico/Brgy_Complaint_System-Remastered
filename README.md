# Barangay Complaint Management System (Remastered)

Django complaint management system with ML spam filtering, email verification, audit logs and admin/captain dashboards.

## Run locally

```bash
python -m venv venv
venv\Scripts\activate          # Windows  (Mac/Linux: source venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env         # Mac/Linux: cp .env.example .env, then fill in your values
python manage.py migrate
python manage.py runserver
```

## Database

* No `DATABASE_URL` in `.env` = local SQLite (`db.sqlite3`)
* `DATABASE_URL` set = Supabase PostgreSQL

## Secrets

Never commit `.env`. Keep real passwords only in `.env`, which is ignored by git.
