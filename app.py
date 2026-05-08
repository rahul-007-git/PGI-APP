from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from flask_jwt_extended import (
    JWTManager,
    create_access_token,
    jwt_required,
    get_jwt_identity
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash

import os
import datetime


# ─────────────────────────────────────────────
# LOAD ENV
# ─────────────────────────────────────────────
load_dotenv()

# ─────────────────────────────────────────────
# APP CONFIG
# ─────────────────────────────────────────────
app = Flask(__name__)
CORS(app)

app.config["JWT_SECRET_KEY"]            = os.getenv("JWT_SECRET", "pgi-secret-key-2025")
app.config["JWT_ACCESS_TOKEN_EXPIRES"]  = datetime.timedelta(hours=24)

db_url = os.getenv("DATABASE_URL", "sqlite:///taskflow.db")
app.config["SQLALCHEMY_DATABASE_URI"]   = db_url

if db_url.startswith("postgresql"):
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "connect_args": {"sslmode": "require"},
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

jwt = JWTManager(app)
db  = SQLAlchemy(app)



# ─────────────────────────────────────────────
# MODELS
# ─────────────────────────────────────────────

class User(db.Model):
    __tablename__ = "users"
    id          = db.Column(db.String(100), primary_key=True)
    email       = db.Column(db.String(200), unique=True, nullable=False)
    password    = db.Column(db.String(300), nullable=False)
    role        = db.Column(db.String(50),  nullable=False)
    name        = db.Column(db.String(200), nullable=False)
    initials    = db.Column(db.String(10))
    team        = db.Column(db.String(100))
    specialty   = db.Column(db.String(100))
    phone       = db.Column(db.String(30))


class Task(db.Model):
    __tablename__ = "tasks"
    id          = db.Column(db.String(100), primary_key=True)
    title       = db.Column(db.String(300), nullable=False)
    desc        = db.Column(db.Text)
    assignedTo  = db.Column(db.String(100))
    assignedBy  = db.Column(db.String(100))
    status      = db.Column(db.String(50))
    priority    = db.Column(db.String(50))
    due         = db.Column(db.String(50))
    msg         = db.Column(db.Text)
    createdAt   = db.Column(db.String(100))
    proof_text        = db.Column(db.Text)
    proof_link        = db.Column(db.Text)
    rejection_reason  = db.Column(db.Text)


class Message(db.Model):
    __tablename__ = "messages"
    id          = db.Column(db.String(100), primary_key=True)
    from_name   = db.Column(db.String(200))
    fromId      = db.Column(db.String(100))
    text        = db.Column(db.Text)
    time        = db.Column(db.String(100))
    # 'all' = everyone channel, or team id like 'design', 'dev', etc.
    channel     = db.Column(db.String(100), nullable=False, server_default='all')


class Attendance(db.Model):
    __tablename__ = "attendance"
    id          = db.Column(db.Integer, primary_key=True)
    userId      = db.Column(db.String(100), nullable=False)
    status      = db.Column(db.String(50),  nullable=False)
    # YYYY-MM-DD — one record per user per day. No record = absent.
    date        = db.Column(db.String(20),  nullable=False)


class MorningMessage(db.Model):
    __tablename__ = "morning_messages"
    id          = db.Column(db.Integer, primary_key=True)
    text        = db.Column(db.Text)
    from_name   = db.Column(db.String(200))
    time        = db.Column(db.String(100))
    date        = db.Column(db.String(20))   # YYYY-MM-DD


class Notification(db.Model):
    """
    Push-style notifications stored per user.
    Polled by frontend every 30 seconds.
    """
    __tablename__ = "notifications"
    id          = db.Column(db.Integer, primary_key=True)
    userId      = db.Column(db.String(100), nullable=False)
    # 'type' and 'read' are reserved/special names in SQLAlchemy/Python;
    # use aliased Python attrs mapped to the same DB columns.
    ntype       = db.Column("type",  db.String(50))   # 'task'|'proof'|'morning'|'message'
    title       = db.Column(db.String(300))
    body        = db.Column(db.Text)
    is_read     = db.Column("read",  db.Boolean, default=False)
    createdAt   = db.Column(db.String(100))


class Rating(db.Model):
    __tablename__ = "ratings"
    id         = db.Column(db.Integer, primary_key=True)
    userId     = db.Column(db.String(100), nullable=False)
    score      = db.Column(db.Integer,     nullable=False)   # 1-10
    date       = db.Column(db.String(20),  nullable=False)   # YYYY-MM-DD
    note       = db.Column(db.Text)


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def now_str():
    return datetime.datetime.now().strftime("%I:%M %p")

def today_str():
    return datetime.datetime.now().strftime("%Y-%m-%d")

def make_notif(userId, ntype, title, body):
    n = Notification(
        userId=userId, ntype=ntype,
        title=title, body=body,
        is_read=False,
        createdAt=datetime.datetime.now().strftime("%b %d %I:%M %p")
    )
    db.session.add(n)


# ─────────────────────────────────────────────
# CREATE / MIGRATE TABLES
# ─────────────────────────────────────────────

with app.app_context():
    db.create_all()

    # Auto-migrate: add new columns to existing tables without losing data
    with db.engine.connect() as conn:
        migrations = [
            "ALTER TABLE messages    ADD COLUMN channel   VARCHAR(100) DEFAULT 'all'",
            "ALTER TABLE attendance  ADD COLUMN date      VARCHAR(20)",
            "ALTER TABLE morning_messages ADD COLUMN date VARCHAR(20)",
        ]
        for sql in migrations:
            try:
                conn.execute(text(sql))
                conn.commit()
            except Exception:
                # Column already exists — skip
                conn.rollback()


# ─────────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────────

@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "message": "TaskFlow Backend Running"})


# ─────────────────────────────────────────────
# SEED  (only creates founder — add real users via /api/users POST)
# ─────────────────────────────────────────────

@app.route("/api/seed-update")
def seed_update():

    # ─────────────────────────────
    # CREATE OR UPDATE FOUNDER
    # ─────────────────────────────

    founder = db.session.get(User, "founder")

    if not founder:

        founder = User(
            id="founder",
            email="lokeshlaven@gmail.com",
            password=generate_password_hash("founder2025"),
            role="founder",
            name="Laven Lokesh B",
            initials="LL"
        )

        db.session.add(founder)

    else:

        founder.email = "lokeshlaven@gmail.com"
        founder.password = generate_password_hash("founder2025")
        founder.name = "Laven Lokesh B"
        founder.initials = "LL"

    # ─────────────────────────────
    # EMPLOYEES
    # ─────────────────────────────

    employees = [

        dict(
            id="u_abinash",
            email="abinashbolt@gmail.com",
            name="Abinash R",
            initials="AR",
            role="employee",
            team="technical"
        ),

        dict(
            id="u_rahul",
            email="mail2rahul.mk@gmail.com",
            name="Rahul M",
            initials="RM",
            role="employee",
            team="technical"
        ),

        dict(
            id="u_amitesh",
            email="amitesh4122005@gmail.com",
            name="Amitesh M",
            initials="AM",
            role="employee",
            team="technical"
        ),

        dict(
            id="u_sadhana",
            email="trainings.pgi@gmail.com",
            name="Sadhana M",
            initials="SM",
            role="employee",
            team="bizdev"
        ),

        dict(
            id="u_prassanna",
            email="kpkkumar1619@gmail.com",
            name="Prassanna Kumar K",
            initials="PK",
            role="employee",
            team="content"
        ),
    ]

    created = []

    for emp in employees:

        existing = db.session.get(User, emp["id"])

        if not existing:

            new_user = User(
                id=emp["id"],
                email=emp["email"],
                password=generate_password_hash("emp2025"),
                role=emp["role"],
                name=emp["name"],
                initials=emp["initials"],
                team=emp["team"]
            )

            db.session.add(new_user)

            created.append(emp["name"])

    db.session.commit()

    return jsonify({
        "success": True,
        "message": "Database seeded successfully",
        "created_employees": created
    })
# ─────────────────────────────────────────────
# AUTH — LOGIN
# ─────────────────────────────────────────────

@app.route("/api/login", methods=["POST"])
def login():
    data     = request.get_json()
    email    = data.get("email", "").strip().lower()
    password = data.get("password", "")
    role     = data.get("role")

    user = User.query.filter(
        db.func.lower(User.email) == email,
        User.role == role
    ).first()

    if not user:
        return jsonify({"error": "User not found"}), 404

    if not check_password_hash(user.password, password):
        return jsonify({"error": "Invalid password"}), 401

    token = create_access_token(identity=user.id)
    return jsonify({
        "token": token,
        "user": {
            "id": user.id, "email": user.email, "role": user.role,
            "name": user.name, "initials": user.initials,
            "team": user.team, "specialty": user.specialty
        }
    })


# ─────────────────────────────────────────────
# CURRENT USER
# ─────────────────────────────────────────────

@app.route("/api/me")
@jwt_required()
def me():
    user = db.session.get(User, get_jwt_identity())
    if not user:
        return jsonify({"error": "User not found"}), 404
    return jsonify({
        "id": user.id, "email": user.email, "role": user.role,
        "name": user.name, "initials": user.initials,
        "team": user.team, "specialty": user.specialty
    })


# ─────────────────────────────────────────────
# USERS — LIST, CREATE, DELETE
# ─────────────────────────────────────────────

@app.route("/api/users")
@jwt_required()
def get_users():
    return jsonify([{
        "id": u.id, "email": u.email, "role": u.role,
        "name": u.name, "initials": u.initials,
        "team": u.team, "specialty": u.specialty,
        "phone": u.phone
    } for u in User.query.all()])


@app.route("/api/users", methods=["POST"])
@jwt_required()
def create_user():
    caller = db.session.get(User, get_jwt_identity())
    if caller.role != "founder":
        return jsonify({"error": "Only founder can create users"}), 403

    data = request.get_json()
    email = data.get("email", "").strip().lower()

    if User.query.filter(db.func.lower(User.email) == email).first():
        return jsonify({"error": "Email already exists"}), 400

    name = data.get("name", "").strip()
    initials = data.get("initials") or "".join(w[0].upper() for w in name.split()[:2])

    user = User(
        id=data.get("id") or ("u" + str(int(datetime.datetime.now().timestamp() * 1000))),
        email=email,
        password=generate_password_hash(data.get("password", "emp2025")),
        role=data.get("role", "employee"),
        name=name,
        initials=initials,
        team=data.get("team"),
        specialty=data.get("specialty"),
        phone=data.get("phone", "").strip() or None
    )
    db.session.add(user)
    db.session.commit()
    return jsonify({"success": True, "user": {
        "id": user.id, "name": user.name, "email": user.email,
        "role": user.role, "initials": user.initials,
        "team": user.team, "specialty": user.specialty,
        "phone": user.phone
    }})


@app.route("/api/users/<user_id>", methods=["DELETE"])
@jwt_required()
def delete_user(user_id):
    caller = db.session.get(User, get_jwt_identity())
    if caller.role != "founder":
        return jsonify({"error": "Only founder can delete users"}), 403
    user = db.session.get(User, user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404
    db.session.delete(user)
    db.session.commit()
    return jsonify({"success": True})


# ─────────────────────────────────────────────
# TASKS — GET, CREATE, UPDATE, DELETE
# ─────────────────────────────────────────────

def task_to_dict(t):
    proof = {"text": t.proof_text, "link": t.proof_link} if (t.proof_text or t.proof_link) else None
    return {
        "id": t.id, "title": t.title, "desc": t.desc,
        "assignedTo": t.assignedTo, "assignedBy": t.assignedBy,
        "status": t.status, "priority": t.priority,
        "due": t.due, "msg": t.msg, "createdAt": t.createdAt,
        "proof": proof,
        "rejection_reason": t.rejection_reason
    }


@app.route("/api/tasks")
@jwt_required()
def get_tasks():
    uid  = get_jwt_identity()
    user = db.session.get(User, uid)
    if not user:
        return jsonify({"error": "User not found"}), 404
    tasks = Task.query.all() if user.role == "founder" else Task.query.filter_by(assignedTo=uid).all()
    return jsonify([task_to_dict(t) for t in tasks])


@app.route("/api/tasks", methods=["POST"])
@jwt_required()
def create_task():
    # Always start with a clean session state
    db.session.rollback()

    uid     = get_jwt_identity()
    founder = db.session.get(User, uid)
    if not founder:
        return jsonify({"error": "Founder account not found"}), 404
    if founder.role != "founder":
        return jsonify({"error": "Only founder can assign tasks"}), 403

    data        = request.get_json() or {}
    assignee_id = data.get("assignedTo", "").strip()

    if not assignee_id:
        return jsonify({"error": "Please select a team member to assign the task to"}), 400

    # Verify assignee exists in DB
    assignee = db.session.get(User, assignee_id)
    if not assignee:
        return jsonify({"error": "Selected team member not found. Please refresh and try again."}), 404

    try:
        task = Task(
            id        = "t" + str(int(datetime.datetime.now().timestamp() * 1000)),
            title     = (data.get("title") or "").strip(),
            desc      = (data.get("desc")  or "").strip(),
            assignedTo= assignee_id,
            assignedBy= uid,
            status    = "pending",
            priority  = data.get("priority", "medium"),
            due       = data.get("due") or "TBD",
            msg       = (data.get("msg") or "").strip(),
            createdAt = datetime.datetime.now().strftime("%b %d, %Y %I:%M %p")
        )
        db.session.add(task)

        # Add notification for assignee (strip emojis from title/body for DB safety)
        make_notif(
            userId=assignee_id, ntype="task",
            title="New Task Assigned",
            body='"' + task.title + '" was assigned to you by ' + founder.name + '. Due: ' + (task.due or "TBD")
        )

        db.session.commit()
        return jsonify({"success": True, "task": task_to_dict(task)})

    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()          # logs full error to terminal / server logs
        err_msg = str(e)
        return jsonify({"error": "Database error while creating task: " + err_msg}), 500


@app.route("/api/tasks/<task_id>/status", methods=["PATCH"])
@jwt_required()
def update_task_status(task_id):
    task = db.session.get(Task, task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    data = request.get_json()
    task.status = data.get("status")
    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/tasks/<task_id>/proof", methods=["POST"])
@jwt_required()
def submit_proof(task_id):
    uid  = get_jwt_identity()
    task = db.session.get(Task, task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404

    data             = request.get_json()
    task.status      = "submitted"
    task.proof_text  = data.get("text")
    task.proof_link  = data.get("link")

    # Notify founder about proof submission
    submitter = db.session.get(User, uid)
    founder   = User.query.filter_by(role="founder").first()
    if founder:
        make_notif(
            userId=founder.id, ntype="proof",
            title="Proof Submitted",
            body=submitter.name + ' submitted proof for "' + task.title + '"'
        )

    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/tasks/<task_id>/verify", methods=["POST"])
@jwt_required()
def verify_task(task_id):
    uid  = get_jwt_identity()
    user = db.session.get(User, uid)
    if user.role != "founder":
        return jsonify({"error": "Only founder can verify"}), 403

    task = db.session.get(Task, task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404

    action = request.get_json().get("action")
    reason = request.get_json().get("reason", "").strip()

    if action == "approve":
        task.status = "completed"
        task.rejection_reason = None
        make_notif(
            userId=task.assignedTo, ntype="task",
            title="Task Completed!",
            body='Your task "' + task.title + '" was approved and marked complete!'
        )
    elif action == "reject":
        task.status = "rejected"
        task.proof_text = None
        task.proof_link = None
        task.rejection_reason = reason if reason else None
        notif_body = 'Your proof for "' + task.title + '" was rejected. Please resubmit.'
        if reason:
            notif_body += ' Reason: ' + reason
        make_notif(
            userId=task.assignedTo, ntype="task",
            title="Proof Rejected",
            body=notif_body
        )

    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/tasks/<task_id>", methods=["DELETE"])
@jwt_required()
def delete_task(task_id):
    user = db.session.get(User, get_jwt_identity())
    if user.role != "founder":
        return jsonify({"error": "Only founder can delete tasks"}), 403
    task = db.session.get(Task, task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    db.session.delete(task)
    db.session.commit()
    return jsonify({"success": True})


# ─────────────────────────────────────────────
# MESSAGES — GET BY CHANNEL, SEND
# ─────────────────────────────────────────────

@app.route("/api/messages")
@jwt_required()
def get_messages():
    channel = request.args.get("channel", "all")
    msgs    = Message.query.filter_by(channel=channel).order_by(Message.id.asc()).all()
    return jsonify([{
        "id": m.id, "from": m.from_name, "fromId": m.fromId,
        "text": m.text, "time": m.time, "channel": m.channel
    } for m in msgs])


@app.route("/api/messages", methods=["POST"])
@jwt_required()
def send_message():
    uid  = get_jwt_identity()
    user = db.session.get(User, uid)
    data = request.get_json()
    channel = data.get("channel", "all")

    msg = Message(
        id        = "m" + str(int(datetime.datetime.now().timestamp() * 1000)),
        from_name = user.name,
        fromId    = uid,
        text      = data.get("text", "").strip(),
        time      = now_str(),
        channel   = channel
    )
    db.session.add(msg)
    db.session.commit()
    return jsonify({"success": True, "message": {
        "id": msg.id, "from": msg.from_name, "fromId": msg.fromId,
        "text": msg.text, "time": msg.time, "channel": msg.channel
    }})


# Message count endpoint — used by frontend to detect new messages without full fetch
@app.route("/api/messages/count")
@jwt_required()
def message_counts():
    channels_param = request.args.get("channels", "all")
    channels = channels_param.split(",")
    result = {}
    for ch in channels:
        result[ch.strip()] = Message.query.filter_by(channel=ch.strip()).count()
    return jsonify(result)


# ─────────────────────────────────────────────
# MORNING MESSAGE
# ─────────────────────────────────────────────

@app.route("/api/morning-message", methods=["GET"])
@jwt_required()
def get_morning_message():
    msg = MorningMessage.query.filter_by(date=today_str()).order_by(MorningMessage.id.desc()).first()
    if not msg:
        return jsonify({})
    return jsonify({"text": msg.text, "from": msg.from_name, "time": msg.time})


@app.route("/api/morning-message", methods=["POST"])
@jwt_required()
def post_morning_message():
    uid  = get_jwt_identity()
    user = db.session.get(User, uid)
    data = request.get_json()

    msg = MorningMessage(
        text=data.get("text"), from_name=user.name,
        time=now_str(), date=today_str()
    )
    db.session.add(msg)

    # Notify all non-founder users on the website
    message_text = str(data.get("text", ""))
    for u in User.query.filter(User.role != "founder").all():
        make_notif(
            userId=u.id, ntype="morning",
            title="Morning Message",
            body=message_text[:120]
        )

    db.session.commit()
    return jsonify({"success": True})


# ─────────────────────────────────────────────
# RATINGS  (founder only, one per user per day)
# ─────────────────────────────────────────────

@app.route("/api/ratings", methods=["GET"])
@jwt_required()
def get_ratings():
    caller = db.session.get(User, get_jwt_identity())
    if caller.role != "founder":
        return jsonify({"error": "Forbidden"}), 403

    # today's ratings keyed by userId
    today_ratings = {
        r.userId: {"score": r.score, "note": r.note}
        for r in Rating.query.filter_by(date=today_str()).all()
    }

    # last 30 days history for graph — list of {date, userId, score}
    from sqlalchemy import desc as sa_desc
    thirty_days_ago = (datetime.datetime.utcnow() - datetime.timedelta(days=29)).strftime("%Y-%m-%d")
    history = [
        {"date": r.date, "userId": r.userId, "score": r.score}
        for r in Rating.query
            .filter(Rating.date >= thirty_days_ago)
            .order_by(Rating.date)
            .all()
    ]

    return jsonify({"today": today_ratings, "history": history})


@app.route("/api/ratings", methods=["POST"])
@jwt_required()
def save_rating():
    caller = db.session.get(User, get_jwt_identity())
    if caller.role != "founder":
        return jsonify({"error": "Forbidden"}), 403

    data   = request.get_json()
    uid    = data.get("userId")
    score  = int(data.get("score", 0))
    note   = data.get("note", "").strip()

    if not uid or score < 1 or score > 10:
        return jsonify({"error": "Invalid data"}), 400

    # Upsert — one rating per user per day
    existing = Rating.query.filter_by(userId=uid, date=today_str()).first()
    if existing:
        existing.score = score
        existing.note  = note or existing.note
    else:
        db.session.add(Rating(userId=uid, score=score, note=note, date=today_str()))

    db.session.commit()
    return jsonify({"success": True})


# ─────────────────────────────────────────────
# ATTENDANCE  (date-based, defaults to absent)
# ─────────────────────────────────────────────

@app.route("/api/attendance", methods=["GET"])
@jwt_required()
def get_attendance():
    today = today_str()
    # Today's records
    records = {a.userId: a.status for a in Attendance.query.filter_by(date=today).all()}

    # Return for all non-founder users — absent by default
    users = User.query.filter(User.role != "founder").all()
    return jsonify([{
        "userId": u.id,
        "name": u.name,
        "initials": u.initials,
        "role": u.role,
        "team": u.team,
        "status": records.get(u.id, "absent"),
        "date": today
    } for u in users])


@app.route("/api/attendance", methods=["PATCH"])
@jwt_required()
def mark_attendance():
    data   = request.get_json()
    uid    = data.get("userId")
    status = data.get("status")
    today  = today_str()

    att = Attendance.query.filter_by(userId=uid, date=today).first()
    if att:
        att.status = status
    else:
        db.session.add(Attendance(userId=uid, status=status, date=today))

    db.session.commit()
    return jsonify({"success": True, "status": status, "date": today})


# ─────────────────────────────────────────────
# NOTIFICATIONS
# ─────────────────────────────────────────────

@app.route("/api/notifications")
@jwt_required()
def get_notifications():
    uid    = get_jwt_identity()
    notifs = Notification.query.filter_by(userId=uid, is_read=False)\
                               .order_by(Notification.id.desc())\
                               .limit(30).all()
    return jsonify([{
        "id": n.id, "type": n.ntype,
        "title": n.title, "body": n.body,
        "createdAt": n.createdAt
    } for n in notifs])


@app.route("/api/notifications/read-all", methods=["POST"])
@jwt_required()
def mark_all_read():
    uid = get_jwt_identity()
    Notification.query.filter_by(userId=uid, is_read=False).update({"is_read": True})
    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/notifications/<int:nid>/read", methods=["POST"])
@jwt_required()
def mark_one_read(nid):
    uid   = get_jwt_identity()
    notif = db.session.get(Notification, nid)
    if notif and notif.userId == uid:
        notif.is_read = True
        db.session.commit()
    return jsonify({"success": True})


# ─────────────────────────────────────────────
# HOME
# ─────────────────────────────────────────────

@app.errorhandler(500)
def internal_error(e):
    db.session.rollback()
    return jsonify({"error": "Internal server error: " + str(e)}), 500

@app.errorhandler(Exception)
def unhandled_exception(e):
    db.session.rollback()
    import traceback
    traceback.print_exc()
    return jsonify({"error": "Unexpected error: " + str(e)}), 500

@app.route("/")
def home():
    return send_file("taskflow_v3.html")


# ─────────────────────────────────────────────
# RUN
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 50)
    print("  TaskFlow — Plant Green Inertia")
    print("  http://localhost:5000")
    print("=" * 50)
    app.run(debug=True, host="0.0.0.0", port=5000)