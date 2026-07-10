# Turny Functional Site Package

This package is a sanitized TRMT-derived Flask/SQLite app for Turny.

## Included Scope

- Dashboard
- Daily 업무관리
- Survey > Class Status
- Survey > Vetting Status
- Calendar

The navigation is limited to the above scope. Other TRMT menus such as Automation, Dock preparation, Report, and Expenses are not exposed in the UI.

## Data Policy

- No live TRMT database is included.
- No uploaded files are included.
- `seed.sql` is intentionally empty.
- The app creates an empty SQLite database at first run: `instance/trmt.db`.
- A default local admin account is created by `app.py --init-db` or first run:
  - username: `admin`
  - password: `admin0424`
- Change this password immediately after first login.

## Quick Start

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python app.py --init-db
python app.py
```

Open:

- `http://SERVER:5000/dashboard`

If port 5000 is already occupied:

```bash
PORT=5080 python app.py
```

## Oracle Server Deploy Sketch

```bash
scp -i ~/ssh-key-2026-07-09/ssh-key-2026-07-09.key turny-functional-site-package.zip opc@ORACLE_HOST:/home/opc/
ssh -i ~/ssh-key-2026-07-09/ssh-key-2026-07-09.key opc@ORACLE_HOST
unzip turny-functional-site-package.zip
cd turny-functional-site
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python app.py --init-db
python app.py
```

Replace `ORACLE_HOST` and deployment directory with the actual Kim Seokjin Oracle server details.

## Smoke Test

After login, check:

- `/dashboard`
- `/`
- `/class-status`
- `/vetting-status`
- `/calendar`

Then create a test supervisor and vessel from the admin management UI, add a small Daily issue, add one calendar event, and verify the dashboard counts update.

## Notes

- Class Status PDF/image/xlsx parsing routes are included from the original app. AI extraction may require the original environment variables or provider keys if used.
- File upload directories are empty and created under `static/uploads/`.
- This package is intended as a clean starting point, not a data migration.
