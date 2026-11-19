import hashlib
import os
import secrets
import time
import uuid

from functools import wraps
from pathlib import Path

import click

from flask import (
    Flask,
    g,
    jsonify,
    request,
    send_from_directory,
)

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from werkzeug.exceptions import HTTPException
from werkzeug.security import (
    check_password_hash,
    generate_password_hash,
)

from .assessment import assess, validate_clinical

from .models import (
    db,
    Staff,
    LoginSession,
    LoginAttempt,
    QueueLock,
    Draft,
    Patient,
    Doctor,
    Bed,
    Audit,
)


def now():
    return int(time.time() * 1000)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class APIError(Exception):
    def __init__(self, message, status=400):
        self.message = message
        self.status = status


def body():
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        raise APIError("A JSON object is required.")

    return data


def text(data, key, limit, required=True):
    value = data.get(key, "")

    if (
        not isinstance(value, str)
        or len(value) > limit
        or (required and not value.strip())
    ):
        raise APIError(
            f"{key} must be text, "
            f"{1 if required else 0}–{limit} characters."
        )

    return value.strip()


def locked():
    # Serialize shared queue/resource operations across workers.
    result = db.session.execute(
        update(QueueLock)
        .where(QueueLock.id == 1)
        .values(version=QueueLock.version + 1)
    )

    if result.rowcount != 1:
        raise APIError(
            "Database has not been initialized.", 503
        )


def audit(action, patient=None, details=None):
    db.session.add(
        Audit(
            staff_id=g.staff.id,
            patient_id=patient,
            action=action,
            occurred_at=now(),
            details=details or {},
        )
    )


def patient_json(patient):
    return {
        "id": patient.id,
        "name": patient.name,
        "age": patient.age,
        "gender": patient.gender,
        "condition": patient.condition,
        "disease": patient.disease,
        "severity": patient.severity,
        "status": patient.status,
        "arrival": patient.arrival,
        "doctor": patient.doctor,
        "bed": patient.bed,
        "version": patient.version,
        "wait": max(
            0,
            (
                (patient.treatment_at or now())
                - patient.arrival
            ) // 60000,
        ),
        "vitals": patient.vitals,
        "warningSigns": patient.warning_signs,
        "severityReview": patient.review,
        "treatmentAt": patient.treatment_at,
        "completedAt": patient.completed_at,
    }


def create_app(config=None):
    app = Flask(__name__, static_folder=None)

    app.config.update(
        SQLALCHEMY_DATABASE_URI=os.environ.get(
            "DATABASE_URL", "sqlite:///pulse.db"
        ),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SQLALCHEMY_ENGINE_OPTIONS={
            "pool_pre_ping": True,
        },
        MAX_CONTENT_LENGTH=32 * 1024,
        COOKIE_SECURE=os.environ.get(
            "COOKIE_SECURE", "0"
        ) == "1",
        SESSION_HOURS=8,
    )

    if config:
        app.config.update(config)

    if app.config["SQLALCHEMY_DATABASE_URI"].startswith("mysql"):
        app.config["SQLALCHEMY_ENGINE_OPTIONS"][
            "isolation_level"
        ] = "READ COMMITTED"

    db.init_app(app)

    frontend = (
        Path(__file__).resolve().parent.parent
        / "frontend"
    )

    app.config["DUMMY_PASSWORD_HASH"] = generate_password_hash(
        secrets.token_urlsafe(24)
    )

    @app.after_request
    def security_headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"

        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self'; "
            "object-src 'none'; "
            "base-uri 'none'; "
            "frame-ancestors 'none'; "
            "form-action 'self'"
        )

        if app.config["COOKIE_SECURE"]:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000"
            )

        return response

    @app.errorhandler(APIError)
    @app.errorhandler(ValueError)
    def bad_request(error):
        db.session.rollback()

        return (
            jsonify(
                error=getattr(error, "message", str(error))
            ),
            getattr(error, "status", 400),
        )

    @app.errorhandler(HTTPException)
    def http_error(error):
        db.session.rollback()

        return jsonify(
            error=error.description
        ), error.code

    @app.errorhandler(IntegrityError)
    @app.errorhandler(OperationalError)
    def database_error(error):
        db.session.rollback()

        app.logger.error(
            "Database operation failed (%s)",
            type(error).__name__,
        )

        return jsonify(
            error=(
                "Database unavailable or conflicting operation. "
                "Refresh before retrying."
            )
        ), 503

    def auth(*roles):
        def decorator(function):
            @wraps(function)
            def wrapper(*args, **kwargs):
                token = request.cookies.get(
                    "pulse_session", ""
                )

                login = (
                    db.session.get(
                        LoginSession, digest(token)
                    )
                    if token else None
                )

                staff = (
                    db.session.get(
                        Staff, login.staff_id
                    )
                    if login else None
                )

                if (
                    not login
                    or login.expires < now()
                    or not staff
                    or not staff.active
                ):
                    raise APIError(
                        "Sign in required.", 401
                    )

                if roles and staff.role not in roles:
                    raise APIError(
                        "Your role cannot perform this action.",
                        403,
                    )

                if request.method not in (
                    "GET", "HEAD", "OPTIONS"
                ):
                    supplied = request.headers.get(
                        "X-CSRF-Token", ""
                    )

                    if not secrets.compare_digest(
                        supplied, login.csrf
                    ):
                        raise APIError(
                            "Missing or invalid CSRF token.",
                            403,
                        )

                g.staff = staff
                g.login = login

                return function(*args, **kwargs)

            return wrapper

        return decorator

    def identity(staff, csrf):
        return {
            "id": staff.id,
            "username": staff.username,
            "name": staff.name,
            "role": staff.role,
            "csrfToken": csrf,
        }

    # Health

    @app.get("/api/health")
    def health():
        lock_id = db.session.scalar(
            select(QueueLock.id)
            .where(QueueLock.id == 1)
        )

        if lock_id is None:
            raise APIError(
                "Database has not been initialized.", 503
            )

        return jsonify(
            status="ok",
            service="pulse",
        )

    # Authentication

    @app.post("/api/auth/login")
    def login():
        if request.headers.get(
            "X-Pulse-Client"
        ) != "dashboard":
            raise APIError(
                "X-Pulse-Client: dashboard header required.",
                403,
            )

        data = body()

        username = text(
            data, "username", 80
        ).lower()

        password = data.get("password")

        if (
            not isinstance(password, str)
            or not 1 <= len(password) <= 256
        ):
            raise APIError(
                "Password must contain 1–256 characters."
            )

        locked()

        attempt_key = digest(username)

        attempt = db.session.get(
            LoginAttempt, attempt_key
        )

        if not attempt:
            attempt = LoginAttempt(
                key=attempt_key,
                failures=0,
                window=now(),
            )
            db.session.add(attempt)

        if now() - attempt.window > 15 * 60000:
            attempt.failures = 0
            attempt.window = now()

        if attempt.failures >= 10:
            db.session.commit()

            raise APIError(
                "Too many failed attempts. "
                "Retry after 15 minutes.",
                429,
            )

        staff = db.session.scalar(
            select(Staff).where(
                Staff.username == username
            )
        )

        password_hash = (
            staff.password_hash
            if staff
            else app.config["DUMMY_PASSWORD_HASH"]
        )

        valid = check_password_hash(
            password_hash, password
        )

        if not valid or not staff or not staff.active:
            attempt.failures += 1
            db.session.commit()

            raise APIError(
                "Invalid username or password.", 401
            )

        attempt.failures = 0

        old_token = request.cookies.get(
            "pulse_session"
        )

        if old_token:
            old_session = db.session.get(
                LoginSession, digest(old_token)
            )

            if old_session:
                db.session.delete(old_session)

        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)

        db.session.add(
            LoginSession(
                token_hash=digest(token),
                staff_id=staff.id,
                csrf=csrf,
                expires=(
                    now()
                    + app.config["SESSION_HOURS"]
                    * 3600000
                ),
            )
        )

        g.staff = staff
        audit("login")

        response = jsonify(
            identity(staff, csrf)
        )

        db.session.commit()

        response.set_cookie(
            "pulse_session",
            token,
            httponly=True,
            secure=app.config["COOKIE_SECURE"],
            samesite="Strict",
            max_age=app.config["SESSION_HOURS"] * 3600,
        )

        return response

    @app.get("/api/auth/me")
    @auth()
    def me():
        return jsonify(
            identity(g.staff, g.login.csrf)
        )

    @app.post("/api/auth/logout")
    @auth()
    def logout():
        audit("logout")

        db.session.delete(g.login)
        db.session.commit()

        response = jsonify(ok=True)

        response.delete_cookie(
            "pulse_session",
            secure=app.config["COOKIE_SECURE"],
            httponly=True,
            samesite="Strict",
        )

        return response

    # Dashboard

    @app.get("/api/dashboard")
    @auth()
    def dashboard():
        locked()

        patients = db.session.scalars(
            select(Patient).order_by(
                Patient.severity,
                Patient.arrival,
                Patient.id,
            )
        ).all()

        treating = [
            patient
            for patient in patients
            if patient.status == "treatment"
        ]

        doctors = db.session.scalars(
            select(Doctor).order_by(Doctor.id)
        ).all()

        beds = db.session.scalars(
            select(Bed).order_by(Bed.id)
        ).all()

        result = {
            "patients": [
                patient_json(patient)
                for patient in patients
            ],
            "doctors": [
                {
                    "id": doctor.id,
                    "name": doctor.name,
                    "active": doctor.active,
                    "busy": any(
                        patient.doctor == doctor.id
                        for patient in treating
                    ),
                }
                for doctor in doctors
            ],
            "beds": [
                {
                    "id": bed.id,
                    "active": bed.active,
                    "busy": any(
                        patient.bed == bed.id
                        for patient in treating
                    ),
                }
                for bed in beds
            ],
        }

        db.session.commit()

        return jsonify(result)

    # Assessment drafts

    @app.post("/api/assessments")
    @auth("nurse", "physician")
    def assessment():
        data = body()
        clinical = validate_clinical(data)
        result = assess(clinical)

        locked()

        draft_id = data.get("draftId")

        draft = (
            db.session.get(Draft, draft_id)
            if isinstance(draft_id, str)
            else None
        )

        if draft_id and (
            not draft
            or draft.staff_id != g.staff.id
            or draft.expires < now()
            or draft.patient_id
        ):
            raise APIError(
                "Assessment draft is unavailable. "
                "Start a new registration.",
                409,
            )

        if not draft:
            draft = Draft(
                id=str(uuid.uuid4()),
                staff_id=g.staff.id,
                emergency=False,
                expires=now() + 8 * 3600000,
            )
            db.session.add(draft)

        draft.clinical = clinical

        draft.emergency = (
            draft.emergency or result["emergency"]
        )

        if draft.emergency:
            if not result["emergency"]:
                result["reasons"].insert(
                    0,
                    "An earlier emergency finding remains "
                    "active for this registration.",
                )

            result["severity"] = 1
            result["emergency"] = True

        result["draftId"] = draft.id
        result["emergencyLatched"] = draft.emergency

        db.session.commit()

        return jsonify(result)

    def review(data, result, latched=False):
        severity = data.get("severity")

        if (
            isinstance(severity, bool)
            or not isinstance(severity, int)
            or severity not in (1, 2, 3, 4)
        ):
            raise APIError(
                "Severity must be an integer from 1 to 4."
            )

        if data.get("review") is not True:
            raise APIError(
                "Staff must explicitly confirm severity review."
            )

        reason = text(
            data, "overrideReason", 500, False
        )

        if (
            latched or result["emergency"]
        ) and severity != 1:
            raise APIError(
                "Critical emergency findings cannot be downgraded."
            )

        if (
            severity != result["severity"]
            and not reason
        ):
            raise APIError(
                "An override reason is required."
            )

        return {
            "suggested": result["severity"],
            "confirmed": severity,
            "reasons": result["reasons"],
            "emergencyLatched": (
                latched or result["emergency"]
            ),
            "reviewer": g.staff.name,
            "staffId": g.staff.id,
            "role": g.staff.role,
            "overrideReason": reason,
            "confirmedAt": now(),
            "ruleVersion": result["ruleVersion"],
        }

    # Patient registration

    @app.post("/api/patients")
    @auth("nurse", "physician")
    def register():
        data = body()

        name = text(data, "name", 80)

        disease = text(
            data, "disease", 120, False
        )

        gender = data.get("gender")

        if gender not in (
            "Female",
            "Male",
            "Other",
            "Prefer not to say",
        ):
            raise APIError("Invalid gender.")

        clinical = validate_clinical(data)

        if not clinical["symptoms"]:
            raise APIError(
                "Describe the presenting symptoms."
            )

        locked()

        draft_id = data.get("draftId")

        draft = (
            db.session.get(Draft, draft_id)
            if isinstance(draft_id, str)
            else None
        )

        if (
            not draft
            or draft.staff_id != g.staff.id
            or draft.expires < now()
        ):
            raise APIError(
                "Create a server assessment before registration.",
                409,
            )

        if draft.patient_id:
            existing = db.session.get(
                Patient, draft.patient_id
            )

            return jsonify(
                patient_json(existing)
            ), 200

        if draft.clinical != clinical:
            raise APIError(
                "Clinical details changed. "
                "Reassess before confirmation.",
                409,
            )

        result = assess(clinical)

        if draft.emergency:
            result["severity"] = 1

        record = review(
            data, result, draft.emergency
        )

        patient = Patient(
            name=name,
            age=clinical["age"],
            gender=gender,
            condition=clinical["symptoms"],
            disease=disease,
            severity=data["severity"],
            arrival=now(),
            vitals=clinical["vitals"],
            warning_signs=clinical["warnings"],
            review=record,
        )

        db.session.add(patient)
        db.session.flush()

        draft.patient_id = patient.id

        audit(
            "register",
            patient.id,
            {
                "review": record,
                "warningsReviewed": (
                    clinical["warningsReviewed"]
                ),
            },
        )

        response = patient_json(patient)

        db.session.commit()

        return jsonify(response), 201

    # Queue assignment

    @app.post("/api/queue/assign-next")
    @auth("nurse", "physician")
    def assign():
        locked()

        patient = db.session.scalar(
            select(Patient)
            .where(Patient.status == "waiting")
            .order_by(
                Patient.severity,
                Patient.arrival,
                Patient.id,
            )
            .limit(1)
        )

        if not patient:
            raise APIError(
                "No patients are waiting.", 409
            )

        treating = db.session.scalars(
            select(Patient).where(
                Patient.status == "treatment"
            )
        ).all()

        busy_doctors = [
            item.doctor for item in treating
        ]

        busy_beds = [
            item.bed for item in treating
        ]

        doctor = db.session.scalar(
            select(Doctor)
            .where(
                Doctor.active.is_(True),
                Doctor.id.not_in(busy_doctors),
            )
            .order_by(Doctor.id)
            .limit(1)
        )

        bed = db.session.scalar(
            select(Bed)
            .where(
                Bed.active.is_(True),
                Bed.id.not_in(busy_beds),
            )
            .order_by(Bed.id)
            .limit(1)
        )

        if not doctor or not bed:
            raise APIError(
                "No available doctor and bed pair.",
                409,
            )

        patient.doctor = doctor.id
        patient.bed = bed.id
        patient.status = "treatment"
        patient.treatment_at = now()
        patient.version += 1

        audit(
            "assign",
            patient.id,
            {
                "doctor": doctor.id,
                "bed": bed.id,
            },
        )

        response = patient_json(patient)

        db.session.commit()

        return jsonify(response)

    # Discharge

    @app.post(
        "/api/patients/<int:patient_id>/discharge"
    )
    @auth("nurse", "physician")
    def discharge(patient_id):
        locked()

        patient = db.session.get(
            Patient, patient_id
        )

        if not patient:
            raise APIError(
                "Patient not found.", 404
            )

        if patient.status != "treatment":
            raise APIError(
                "Only patients in treatment can be discharged.",
                409,
            )

        patient.status = "completed"
        patient.completed_at = now()
        patient.version += 1

        audit(
            "discharge",
            patient.id,
            {
                "doctor": patient.doctor,
                "bed": patient.bed,
            },
        )

        response = patient_json(patient)

        db.session.commit()

        return jsonify(response)

    # Reassessment

    @app.post(
        "/api/patients/<int:patient_id>/reassess"
    )
    @auth("nurse", "physician")
    def reassess(patient_id):
        data = body()
        clinical = validate_clinical(data)

        if not clinical["symptoms"]:
            raise APIError(
                "Describe the presenting symptoms."
            )

        result = assess(clinical)

        locked()

        patient = db.session.get(
            Patient, patient_id
        )

        if not patient:
            raise APIError(
                "Patient not found.", 404
            )

        if patient.status == "completed":
            raise APIError(
                "Discharged patients cannot be reassessed.",
                409,
            )

        if data.get("version") != patient.version:
            raise APIError(
                "Patient changed. Refresh before reassessing.",
                409,
            )

        latched = (
            patient.review.get(
                "emergencyLatched", False
            )
            or result["emergency"]
        )

        if latched:
            result["severity"] = 1

        record = review(
            data, result, latched
        )

        previous_severity = patient.severity

        patient.age = clinical["age"]
        patient.condition = clinical["symptoms"]
        patient.vitals = clinical["vitals"]
        patient.warning_signs = clinical["warnings"]
        patient.severity = data["severity"]
        patient.review = record
        patient.version += 1

        audit(
            "reassess",
            patient.id,
            {
                "previousSeverity": previous_severity,
                "review": record,
                "clinical": clinical,
            },
        )

        response = patient_json(patient)

        db.session.commit()

        return jsonify(response)

    # Audit records

    @app.get("/api/audit")
    @auth("admin")
    def audit_log():
        try:
            after = int(
                request.args.get("after", 0)
            )
        except ValueError:
            raise APIError(
                "after must be an integer."
            )

        records = db.session.scalars(
            select(Audit)
            .where(Audit.id > after)
            .order_by(Audit.id)
            .limit(100)
        ).all()

        return jsonify(
            items=[
                {
                    "id": record.id,
                    "staffId": record.staff_id,
                    "patientId": record.patient_id,
                    "action": record.action,
                    "occurredAt": record.occurred_at,
                    "details": record.details,
                }
                for record in records
            ]
        )

    # Resources

    @app.get("/api/resources")
    @auth()
    def resources():
        return dashboard.__wrapped__()

    @app.post("/api/resources/<kind>")
    @auth("admin")
    def add_resource(kind):
        data = body()

        locked()

        if kind == "doctors":
            item = Doctor(
                name=text(data, "name", 80)
            )
        elif kind == "beds":
            item = Bed()
        else:
            raise APIError(
                "Unknown resource.", 404
            )

        db.session.add(item)
        db.session.flush()

        audit(
            "resource_add",
            details={
                "kind": kind,
                "id": item.id,
            },
        )

        result = {
            "id": item.id,
            "active": True,
        }

        db.session.commit()

        return jsonify(result), 201

    @app.patch(
        "/api/resources/<kind>/<int:item_id>"
    )
    @auth("admin")
    def resource_status(kind, item_id):
        data = body()

        if not isinstance(
            data.get("active"), bool
        ):
            raise APIError(
                "active must be true or false."
            )

        locked()

        model = {
            "doctors": Doctor,
            "beds": Bed,
        }.get(kind)

        if model is None:
            raise APIError(
                "Unknown resource.", 404
            )

        item = db.session.get(
            model, item_id
        )

        if not item:
            raise APIError(
                "Resource not found.", 404
            )

        column = (
            Patient.doctor
            if kind == "doctors"
            else Patient.bed
        )

        occupied = db.session.scalar(
            select(Patient.id)
            .where(
                column == item_id,
                Patient.status == "treatment",
            )
            .limit(1)
        )

        if not data["active"] and occupied:
            raise APIError(
                "Cannot disable an occupied resource.",
                409,
            )

        item.active = data["active"]

        audit(
            "resource_status",
            details={
                "kind": kind,
                "id": item_id,
                "active": item.active,
            },
        )

        db.session.commit()

        return jsonify(ok=True)

    # Staff management

    @app.get("/api/staff")
    @auth("admin")
    def staff_list():
        staff = db.session.scalars(
            select(Staff).order_by(Staff.id)
        ).all()

        return jsonify(
            items=[
                {
                    "id": person.id,
                    "username": person.username,
                    "name": person.name,
                    "role": person.role,
                    "active": person.active,
                }
                for person in staff
            ]
        )

    @app.patch("/api/staff/<int:staff_id>")
    @auth("admin")
    def staff_status(staff_id):
        data = body()

        if not isinstance(
            data.get("active"), bool
        ):
            raise APIError(
                "active must be true or false."
            )

        if staff_id == g.staff.id:
            raise APIError(
                "Cannot change your own account activation."
            )

        locked()

        staff = db.session.get(
            Staff, staff_id
        )

        if not staff:
            raise APIError(
                "Staff not found.", 404
            )

        staff.active = data["active"]

        if not staff.active:
            db.session.query(
                LoginSession
            ).filter_by(
                staff_id=staff_id
            ).delete()

        audit(
            "staff_status",
            details={
                "staffId": staff_id,
                "active": staff.active,
            },
        )

        db.session.commit()

        return jsonify(ok=True)

    # Serve the existing frontend

    @app.get("/")
    def index():
        return send_from_directory(
            frontend, "index.html"
        )

    @app.get("/<path:filename>")
    def assets(filename):
        if filename not in {
            "app.js",
            "assessment.js",
            "style.css",
        }:
            raise APIError(
                "Not found.", 404
            )

        return send_from_directory(
            frontend, filename
        )

    # Command-line setup and maintenance

    @app.cli.command("init-db")
    def init_db():
        db.create_all()

        if not db.session.get(
            QueueLock, 1
        ):
            db.session.add(
                QueueLock(id=1, version=0)
            )

            for name in [
                "Dr. Meera Kapoor",
                "Dr. Arjun Mehta",
                "Dr. Sara Khan",
                "Dr. Rohan Verma",
            ]:
                db.session.add(
                    Doctor(name=name)
                )

            for _ in range(8):
                db.session.add(Bed())

        db.session.commit()

        click.echo(
            "Database initialized. "
            "Create staff with create-staff."
        )

    @app.cli.command("create-staff")
    @click.option(
        "--username", prompt=True
    )
    @click.option(
        "--name", prompt=True
    )
    @click.option(
        "--role",
        type=click.Choice([
            "admin",
            "nurse",
            "physician",
            "receptionist",
        ]),
        prompt=True,
    )
    @click.option(
        "--password",
        prompt=True,
        hide_input=True,
        confirmation_prompt=True,
    )
    def create_staff(
        username, name, role, password
    ):
        if (
            not 1 <= len(username.strip()) <= 80
            or not 1 <= len(name.strip()) <= 80
            or not 12 <= len(password) <= 256
        ):
            raise click.ClickException(
                "Use names of 1–80 characters and "
                "a password of 12–256 characters."
            )

        existing = db.session.scalar(
            select(Staff).where(
                Staff.username
                == username.strip().lower()
            )
        )

        if existing:
            raise click.ClickException(
                "Username already exists."
            )

        db.session.add(
            Staff(
                username=username.strip().lower(),
                name=name.strip(),
                role=role,
                password_hash=generate_password_hash(
                    password
                ),
            )
        )

        db.session.commit()

        click.echo(
            "Staff account created."
        )

    @app.cli.command("reset-password")
    @click.option(
        "--username", prompt=True
    )
    @click.option(
        "--password",
        prompt=True,
        hide_input=True,
        confirmation_prompt=True,
    )
    def reset_password(
        username, password
    ):
        if not 12 <= len(password) <= 256:
            raise click.ClickException(
                "Password must be 12–256 characters."
            )

        staff = db.session.scalar(
            select(Staff).where(
                Staff.username
                == username.strip().lower()
            )
        )

        if not staff:
            raise click.ClickException(
                "Staff not found."
            )

        staff.password_hash = (
            generate_password_hash(password)
        )

        db.session.query(
            LoginSession
        ).filter_by(
            staff_id=staff.id
        ).delete()

        db.session.commit()

        click.echo(
            "Password changed and sessions revoked."
        )

    @app.cli.command("cleanup-expired")
    def cleanup():
        db.session.query(
            LoginSession
        ).filter(
            LoginSession.expires < now()
        ).delete()

        db.session.query(
            Draft
        ).filter(
            Draft.expires < now(),
            Draft.patient_id.is_(None),
        ).delete()

        db.session.query(
            LoginAttempt
        ).filter(
            LoginAttempt.window
            < now() - 15 * 60000
        ).delete()

        db.session.commit()

        click.echo(
            "Expired sessions, unused drafts, "
            "and login attempts removed."
        )

    return app