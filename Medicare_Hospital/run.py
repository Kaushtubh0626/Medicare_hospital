import os

from backend.python import create_app
from setup_db import setup

app = create_app()
setup(app)  # creates tables/doctors/beds + staff logins if missing (idempotent)

if __name__ == "__main__":
    app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "5000")),
    )
