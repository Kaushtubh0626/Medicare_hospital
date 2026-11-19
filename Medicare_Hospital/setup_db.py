"""One-step setup: create tables, doctors, beds and demo staff accounts.

    python setup_db.py

Demo passwords are for local development ONLY. Change them (flask
reset-password) or create real accounts before any real use.
"""
from sqlalchemy import select

from werkzeug.security import generate_password_hash

from backend.python import create_app
from backend.models import db, Staff, QueueLock, Doctor, Bed

DEMO_STAFF = [
    ("admin", "Admin User", "admin", "admin-demo-pass-1"),
    ("nurse", "Nurse Demo", "nurse", "nurse-demo-pass-1"),
    ("doctor", "Dr. Demo", "physician", "doctor-demo-pass-1"),
    ("reception", "Reception Demo", "receptionist", "reception-demo-pass-1"),
]


def setup(app, demo=True):
    with app.app_context():
        db.create_all()
        if not db.session.get(QueueLock, 1):
            db.session.add(QueueLock(id=1, version=0))
            for name in ["Dr. Meera Kapoor", "Dr. Arjun Mehta",
                         "Dr. Sara Khan", "Dr. Rohan Verma"]:
                db.session.add(Doctor(name=name))
            for _ in range(8):
                db.session.add(Bed())
        if demo:
            for username, name, role, password in DEMO_STAFF:
                exists = db.session.scalar(
                    select(Staff).where(Staff.username == username))
                if not exists:
                    db.session.add(Staff(
                        username=username, name=name, role=role,
                        password_hash=generate_password_hash(password)))
        db.session.commit()


if __name__ == "__main__":
    setup(create_app())
    print("Database ready. Demo logins (username / password):")
    for username, _, role, password in DEMO_STAFF:
        print(f"  {username:10} {password:22} ({role})")
    print("\nStart the app with:  python run.py")
