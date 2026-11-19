from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Staff(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    name = db.Column(db.String(80), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)


class LoginSession(db.Model):
    token_hash = db.Column(db.String(64), primary_key=True)
    staff_id = db.Column(
        db.Integer, db.ForeignKey("staff.id"), nullable=False
    )
    csrf = db.Column(db.String(64), nullable=False)
    expires = db.Column(db.BigInteger, nullable=False)


class LoginAttempt(db.Model):
    key = db.Column(db.String(64), primary_key=True)
    failures = db.Column(db.Integer, nullable=False, default=0)
    window = db.Column(db.BigInteger, nullable=False)


class QueueLock(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    version = db.Column(db.Integer, nullable=False, default=0)


class Draft(db.Model):
    id = db.Column(db.String(36), primary_key=True)
    staff_id = db.Column(
        db.Integer, db.ForeignKey("staff.id"), nullable=False
    )
    clinical = db.Column(db.JSON, nullable=False)
    emergency = db.Column(db.Boolean, nullable=False, default=False)
    expires = db.Column(db.BigInteger, nullable=False)
    patient_id = db.Column(
        db.Integer, db.ForeignKey("patient.id"), unique=True
    )


class Patient(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    age = db.Column(db.Integer, nullable=False)
    gender = db.Column(db.String(30), nullable=False)
    condition = db.Column(db.String(1000), nullable=False)
    disease = db.Column(db.String(120), nullable=False, default="")
    severity = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="waiting")
    arrival = db.Column(db.BigInteger, nullable=False)
    treatment_at = db.Column(db.BigInteger)
    completed_at = db.Column(db.BigInteger)
    vitals = db.Column(db.JSON, nullable=False)
    warning_signs = db.Column(db.JSON, nullable=False)
    review = db.Column(db.JSON, nullable=False)
    doctor = db.Column(db.Integer, db.ForeignKey("doctor.id"))
    bed = db.Column(db.Integer, db.ForeignKey("bed.id"))
    version = db.Column(db.Integer, nullable=False, default=1)


class Doctor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)


class Bed(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    active = db.Column(db.Boolean, nullable=False, default=True)


class Audit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    staff_id = db.Column(
        db.Integer, db.ForeignKey("staff.id"), nullable=False
    )
    patient_id = db.Column(db.Integer, db.ForeignKey("patient.id"))
    action = db.Column(db.String(40), nullable=False)
    occurred_at = db.Column(db.BigInteger, nullable=False)
    details = db.Column(db.JSON, nullable=False)