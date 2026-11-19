# Medicare — Emergency Queue (Frontend + Flask Backend)

Hospital emergency-department prioritisation dashboard. The browser UI talks to a
Flask/SQLAlchemy API; patients, queue order, doctors, beds, sessions and audit
records are all stored in the database.

> Educational prototype. Severity suggestions are simple keyword/vital rules, not a
> validated clinical triage protocol. Staff must review every decision.

## Quick start

```sh
pip install -r requirements.txt
python setup_db.py        # creates the DB, 4 doctors, 8 beds and demo logins
python run.py             # open http://127.0.0.1:5000
```

Demo logins (local development only):

| Username    | Password                | Role         | Can do                                     |
|-------------|-------------------------|--------------|--------------------------------------------|
| `nurse`     | `nurse-demo-pass-1`     | nurse        | register, assign, discharge                |
| `doctor`    | `doctor-demo-pass-1`    | physician    | register, assign, discharge                |
| `reception` | `reception-demo-pass-1` | receptionist | view the dashboard only                    |
| `admin`     | `admin-demo-pass-1`     | admin        | view, manage doctors/beds/staff, audit log |

Change these before any real use: `flask --app run reset-password` and
`flask --app run create-staff` (passwords must be 12+ characters).

## Layout

```
backend/python.py      Flask app: auth, dashboard, assessments, queue, admin, CLI
backend/models.py      SQLAlchemy models
backend/assessment.py  Server-side severity rules (authoritative)
frontend/              index.html, style.css, app.js, assessment.js (served by Flask)
run.py                 Start the server
setup_db.py            Create tables + seed data + demo staff
tests/                 pytest API tests, node assessment tests
```

## How frontend and backend connect

- Flask serves the frontend at `/` and the JSON API under `/api/*` (same origin, so
  no CORS setup is needed).
- Login sets an HttpOnly session cookie; every write also sends `X-CSRF-Token`.
- While registering a patient, the form shows an instant preview from `assessment.js`,
  then `POST /api/assessments` returns the authoritative server suggestion and a draft.
  `POST /api/patients` re-validates it: emergencies cannot be downgraded, overrides need
  a reason, and the reviewer identity comes from the login, not from form fields.
- The dashboard refreshes from `GET /api/dashboard` every 15 seconds.

## Configuration (environment variables)

| Variable        | Default              | Purpose                                |
|-----------------|----------------------|----------------------------------------|
| `DATABASE_URL`  | `sqlite:///pulse.db` | e.g. `mysql+pymysql://user:pw@host/db` |
| `COOKIE_SECURE` | `0`                  | set to `1` behind HTTPS                |
| `HOST`, `PORT`  | `127.0.0.1`, `5000`  | bind address                           |

For production use a WSGI server (e.g. `gunicorn run:app`) behind HTTPS.

## Tests

```sh
python -m pytest tests/test_api.py   # backend, auth, queue flow
node tests/assessment.cjs            # severity rules
```
