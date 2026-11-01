from datetime import datetime
import sqlite3
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Smart Classroom Attendance")

DB_NAME = "attendance.db"

# Unknown devices are kept in memory. They will clear when FastAPI restarts.
unknown_devices = {}

DAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday",
             "Friday", "Saturday", "Sunday"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================================================
# DATABASE HELPERS AND INITIALIZATION
# =========================================================

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create new tables and safely add timetable_id to old databases."""
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            device_mac TEXT UNIQUE NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id TEXT NOT NULL,
            node_id TEXT NOT NULL,
            device_mac TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            status TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS attendance_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_time TEXT NOT NULL,
            end_time TEXT,
            duration_minutes REAL,
            status TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS timetable (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_name TEXT NOT NULL,
            subject TEXT NOT NULL,
            instructor TEXT,
            day_of_week INTEGER NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            room TEXT,
            active INTEGER NOT NULL DEFAULT 1
        )
    """)

    # Migrate an existing database without deleting its data.
    session_columns = {
        row["name"]
        for row in cur.execute(
            "PRAGMA table_info(attendance_sessions)"
        ).fetchall()
    }
    if "timetable_id" not in session_columns:
        cur.execute("""
            ALTER TABLE attendance_sessions
            ADD COLUMN timetable_id INTEGER
        """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_attendance_student_timestamp
        ON attendance(student_id, timestamp)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_timetable_day_time
        ON timetable(day_of_week, start_time, end_time, active)
    """)

    conn.commit()
    conn.close()


init_db()


def normalize_mac(value):
    return value.strip().upper() if isinstance(value, str) else ""


def timetable_dict(row):
    if row is None:
        return None
    return {
        "id": row["id"],
        "class_name": row["class_name"],
        "subject": row["subject"],
        "instructor": row["instructor"],
        "day_of_week": row["day_of_week"],
        "day": DAY_NAMES[row["day_of_week"]],
        "start_time": row["start_time"],
        "end_time": row["end_time"],
        "room": row["room"],
        "active": bool(row["active"]),
    }


def find_current_timetable(cursor, now):
    """Find the first active timetable entry covering the current local time."""
    day_number = now.weekday()
    current_time = now.strftime("%H:%M")
    return cursor.execute("""
        SELECT *
        FROM timetable
        WHERE active = 1
          AND day_of_week = ?
          AND start_time <= ?
          AND end_time > ?
        ORDER BY start_time, id
        LIMIT 1
    """, (day_number, current_time, current_time)).fetchone()


# =========================================================
# ROOT / HEALTH
# =========================================================

@app.get("/")
def root():
    return {
        "status": "online",
        "project": "Smart Classroom Attendance",
    }


# =========================================================
# REGISTERED STUDENTS
# =========================================================

@app.post("/api/register")
def register_student(data: dict):
    student_id = str(data.get("student_id", "")).strip()
    name = str(data.get("name", "")).strip()
    device_mac = normalize_mac(data.get("device_mac"))

    if not student_id or not name or not device_mac:
        raise HTTPException(
            status_code=400,
            detail="student_id, name and device_mac are required",
        )

    conn = get_db()
    try:
        conn.execute("""
            INSERT INTO students (student_id, name, device_mac)
            VALUES (?, ?, ?)
        """, (student_id, name, device_mac))
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=409,
            detail="Student ID or device MAC is already registered",
        )
    finally:
        conn.close()

    # Remove the MAC from the unknown list after successful registration.
    unknown_devices.pop(device_mac, None)

    print(
        f"[REGISTERED] {student_id} | {name} | {device_mac}"
    )
    return {
        "status": "registered",
        "student_id": student_id,
        "name": name,
        "device_mac": device_mac,
    }


@app.get("/api/students")
def get_students():
    conn = get_db()
    rows = conn.execute("""
        SELECT student_id, name, device_mac
        FROM students
        ORDER BY student_id
    """).fetchall()
    conn.close()
    return [dict(row) for row in rows]


# =========================================================
# TIMETABLE MANAGEMENT
# =========================================================

@app.post("/api/timetable")
def add_timetable_entry(data: dict):
    class_name = str(data.get("class_name", "")).strip()
    subject = str(data.get("subject", "")).strip()
    instructor = str(data.get("instructor", "")).strip() or None
    room = str(data.get("room", "")).strip() or None
    start_time = str(data.get("start_time", "")).strip()
    end_time = str(data.get("end_time", "")).strip()

    day_value = data.get("day_of_week", data.get("day"))
    if isinstance(day_value, str):
        day_value = DAYS.get(day_value.strip().lower(), day_value)

    try:
        day_value = int(day_value)
        if day_value not in range(7):
            raise ValueError("weekday out of range")
        datetime.strptime(start_time, "%H:%M")
        datetime.strptime(end_time, "%H:%M")
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail=(
                "Provide day/day_of_week as a weekday name or 0-6 "
                "(Monday=0), and times in HH:MM format."
            ),
        )

    if not class_name or not subject:
        raise HTTPException(
            status_code=400,
            detail="class_name and subject are required",
        )
    if start_time >= end_time:
        raise HTTPException(
            status_code=400,
            detail="end_time must be later than start_time",
        )

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO timetable
        (class_name, subject, instructor, day_of_week,
         start_time, end_time, room, active)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1)
    """, (
        class_name, subject, instructor, day_value,
        start_time, end_time, room,
    ))
    conn.commit()

    entry_id = cur.lastrowid
    row = cur.execute(
        "SELECT * FROM timetable WHERE id = ?", (entry_id,)
    ).fetchone()
    conn.close()

    return {
        "status": "created",
        "timetable": timetable_dict(row),
    }


@app.get("/api/timetable")
def get_timetable(day: str | None = None):
    conn = get_db()

    if day is not None:
        day_number = DAYS.get(day.strip().lower())
        if day_number is None:
            conn.close()
            raise HTTPException(
                status_code=400,
                detail="day must be Monday through Sunday",
            )
        rows = conn.execute("""
            SELECT * FROM timetable
            WHERE day_of_week = ?
            ORDER BY start_time, id
        """, (day_number,)).fetchall()
    else:
        rows = conn.execute("""
            SELECT * FROM timetable
            ORDER BY day_of_week, start_time, id
        """).fetchall()

    conn.close()
    return [timetable_dict(row) for row in rows]


@app.delete("/api/timetable/{entry_id}")
def delete_timetable_entry(entry_id: int):
    conn = get_db()
    cursor = conn.execute(
        "DELETE FROM timetable WHERE id = ?", (entry_id,)
    )
    conn.commit()
    deleted = cursor.rowcount
    conn.close()

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="Timetable entry not found",
        )
    return {"status": "deleted", "id": entry_id}


@app.get("/api/timetable/current")
def get_current_timetable():
    now = datetime.now()
    conn = get_db()
    row = find_current_timetable(conn.cursor(), now)
    conn.close()

    return {
        "scheduled_class": timetable_dict(row),
        "checked_at": now.isoformat(),
    }


# =========================================================
# ESP32 DEVICE DETECTION
# =========================================================

@app.post("/api/node")
def receive_node(data: dict):
    node_id = str(data.get("node_id", "UNKNOWN_NODE")).strip()
    device_mac = normalize_mac(data.get("device_mac"))
    timestamp = datetime.now().isoformat()

    if not device_mac:
        raise HTTPException(
            status_code=400,
            detail="No device MAC received",
        )

    conn = get_db()
    student = conn.execute("""
        SELECT student_id, name
        FROM students
        WHERE device_mac = ?
    """, (device_mac,)).fetchone()

    if student is None:
        conn.close()
        unknown_devices[device_mac] = {
            "mac": device_mac,
            "node_id": node_id,
            "timestamp": timestamp,
        }
        return {
            "status": "unknown_device",
            "device_mac": device_mac,
            "node_id": node_id,
            "timestamp": timestamp,
        }

    # If the MAC is registered, clear any stale unknown-device entry.
    unknown_devices.pop(device_mac, None)

    conn.execute("""
        INSERT INTO attendance
        (student_id, node_id, device_mac, timestamp, status)
        VALUES (?, ?, ?, ?, 'PRESENT')
    """, (
        student["student_id"],
        node_id,
        device_mac,
        timestamp,
    ))
    conn.commit()
    conn.close()

    print(
        f"[PRESENT] Student ID: {student['student_id']} | "
        f"Name: {student['name']} | MAC: {device_mac} | "
        f"Node: {node_id} | Time: {timestamp}"
    )

    return {
        "status": "present",
        "student_id": student["student_id"],
        "name": student["name"],
        "node_id": node_id,
        "timestamp": timestamp,
    }


@app.get("/api/unknown-devices")
def get_unknown_devices():
    # Also filter out MACs that may have been registered since first detection.
    conn = get_db()
    registered_rows = conn.execute(
        "SELECT device_mac FROM students"
    ).fetchall()
    conn.close()
    registered_macs = {normalize_mac(row["device_mac"]) for row in registered_rows}

    for mac in list(unknown_devices.keys()):
        if normalize_mac(mac) in registered_macs:
            unknown_devices.pop(mac, None)

    return list(unknown_devices.values())


# =========================================================
# ATTENDANCE HISTORY
# =========================================================

@app.get("/api/attendance")
def get_attendance():
    conn = get_db()
    rows = conn.execute("""
        SELECT
            a.student_id,
            COALESCE(s.name, '') AS name,
            a.node_id,
            a.device_mac,
            a.timestamp,
            a.status
        FROM attendance AS a
        LEFT JOIN students AS s
          ON s.student_id = a.student_id
        ORDER BY a.id DESC
    """).fetchall()
    conn.close()
    return [dict(row) for row in rows]


# =========================================================
# ATTENDANCE SESSION START
# =========================================================

@app.post("/api/session/start")
def start_session(data: dict | None = None):
    data = data or {}
    now = datetime.now()
    conn = get_db()
    cur = conn.cursor()

    existing = cur.execute("""
        SELECT id, start_time, timetable_id
        FROM attendance_sessions
        WHERE status = 'ACTIVE'
        ORDER BY id DESC
        LIMIT 1
    """).fetchone()

    if existing:
        scheduled = None
        if existing["timetable_id"] is not None:
            scheduled = cur.execute(
                "SELECT * FROM timetable WHERE id = ?",
                (existing["timetable_id"],),
            ).fetchone()
        conn.close()
        return {
            "status": "already_active",
            "session_id": existing["id"],
            "start_time": existing["start_time"],
            "timetable_id": existing["timetable_id"],
            "scheduled_class": timetable_dict(scheduled),
        }

    # Caller may explicitly choose a timetable entry.
    timetable_id = data.get("timetable_id")
    scheduled = None

    if timetable_id is not None:
        scheduled = cur.execute("""
            SELECT * FROM timetable
            WHERE id = ? AND active = 1
        """, (timetable_id,)).fetchone()

        if scheduled is None:
            conn.close()
            raise HTTPException(
                status_code=404,
                detail="Active timetable entry not found",
            )
    else:
        # Otherwise automatically match today's day and current local time.
        scheduled = find_current_timetable(cur, now)
        if scheduled is not None:
            timetable_id = scheduled["id"]

    start_time = now.isoformat()
    cur.execute("""
        INSERT INTO attendance_sessions
        (start_time, status, timetable_id)
        VALUES (?, 'ACTIVE', ?)
    """, (start_time, timetable_id))

    session_id = cur.lastrowid
    conn.commit()
    conn.close()

    return {
        "status": "started",
        "session_id": session_id,
        "start_time": start_time,
        "timetable_id": timetable_id,
        "scheduled_class": timetable_dict(scheduled),
        "schedule_match": scheduled is not None,
    }


# =========================================================
# ATTENDANCE SESSION END
# =========================================================

@app.post("/api/session/end")
def end_session():
    conn = get_db()
    cur = conn.cursor()

    session = cur.execute("""
        SELECT id, start_time
        FROM attendance_sessions
        WHERE status = 'ACTIVE'
        ORDER BY id DESC
        LIMIT 1
    """).fetchone()

    if session is None:
        conn.close()
        raise HTTPException(
            status_code=400,
            detail="No active session",
        )

    end_time = datetime.now()
    start_time = datetime.fromisoformat(session["start_time"])
    duration_minutes = (
        end_time - start_time
    ).total_seconds() / 60

    cur.execute("""
        UPDATE attendance_sessions
        SET end_time = ?,
            duration_minutes = ?,
            status = 'COMPLETED'
        WHERE id = ?
    """, (
        end_time.isoformat(),
        duration_minutes,
        session["id"],
    ))

    conn.commit()
    conn.close()

    return {
        "status": "completed",
        "session_id": session["id"],
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "duration_minutes": round(duration_minutes, 2),
    }


# =========================================================
# CURRENT SESSION STATUS
# =========================================================

@app.get("/api/session/current")
def current_session():
    conn = get_db()
    row = conn.execute("""
        SELECT
            ses.id,
            ses.start_time,
            ses.end_time,
            ses.duration_minutes,
            ses.status,
            ses.timetable_id,
            t.class_name,
            t.subject,
            t.instructor,
            t.day_of_week,
            t.start_time AS class_start_time,
            t.end_time AS class_end_time,
            t.room,
            t.active AS timetable_active
        FROM attendance_sessions AS ses
        LEFT JOIN timetable AS t
          ON t.id = ses.timetable_id
        WHERE ses.status = 'ACTIVE'
        ORDER BY ses.id DESC
        LIMIT 1
    """).fetchone()
    conn.close()

    if row is None:
        return {"active": False}

    scheduled_class = None
    if row["timetable_id"] is not None and row["class_name"] is not None:
        scheduled_class = {
            "id": row["timetable_id"],
            "class_name": row["class_name"],
            "subject": row["subject"],
            "instructor": row["instructor"],
            "day_of_week": row["day_of_week"],
            "day": DAY_NAMES[row["day_of_week"]],
            "start_time": row["class_start_time"],
            "end_time": row["class_end_time"],
            "room": row["room"],
            "active": bool(row["timetable_active"]),
        }

    return {
        "active": True,
        "session_id": row["id"],
        "start_time": row["start_time"],
        "end_time": row["end_time"],
        "duration_minutes": row["duration_minutes"],
        "status": row["status"],
        "timetable_id": row["timetable_id"],
        "scheduled_class": scheduled_class,
    }


# =========================================================
# ACTIVE SESSION ATTENDANCE
# =========================================================

@app.get("/api/session/attendance")
def session_attendance():
    conn = get_db()
    cur = conn.cursor()

    session = cur.execute("""
        SELECT id, start_time, timetable_id
        FROM attendance_sessions
        WHERE status = 'ACTIVE'
        ORDER BY id DESC
        LIMIT 1
    """).fetchone()

    if session is None:
        conn.close()
        return {"active": False, "students": []}

    start_time_text = session["start_time"]
    start_time = datetime.fromisoformat(start_time_text)
    elapsed_seconds = max(
        (datetime.now() - start_time).total_seconds(), 0
    )

    students = cur.execute("""
        SELECT student_id, name, device_mac
        FROM students
        ORDER BY student_id
    """).fetchall()

    results = []

    for student in students:
        # Count unique five-second buckets. If both ESP32 nodes see the
        # same student within a bucket, that bucket is counted once.
        detection_row = cur.execute("""
            SELECT COUNT(*) AS bucket_count
            FROM (
                SELECT CAST(
                    (julianday(timestamp) - julianday(?)) * 86400 / 5
                    AS INTEGER
                ) AS bucket
                FROM attendance
                WHERE student_id = ?
                  AND timestamp >= ?
                GROUP BY bucket
            )
        """, (
            start_time_text,
            student["student_id"],
            start_time_text,
        )).fetchone()

        detections = detection_row["bucket_count"] or 0
        detected_seconds = min(detections * 5, elapsed_seconds)

        if elapsed_seconds > 0:
            percentage = detected_seconds / elapsed_seconds * 100
        else:
            percentage = 0

        percentage = min(percentage, 100)
        status = "PRESENT" if percentage >= 60 else "LOW PRESENCE"

        results.append({
            "student_id": student["student_id"],
            "name": student["name"],
            "device_mac": student["device_mac"],
            "detections": detections,
            "presence_percentage": round(percentage, 1),
            "status": status,
        })

    scheduled = None
    if session["timetable_id"] is not None:
        scheduled = cur.execute(
            "SELECT * FROM timetable WHERE id = ?",
            (session["timetable_id"],),
        ).fetchone()

    conn.close()

    return {
        "active": True,
        "session_id": session["id"],
        "timetable_id": session["timetable_id"],
        "scheduled_class": timetable_dict(scheduled),
        "elapsed_minutes": round(elapsed_seconds / 60, 2),
        "students": results,
    }


# =========================================================
# COMPLETED OR HISTORICAL SESSION ATTENDANCE
# =========================================================

@app.get("/api/session/{session_id}/attendance")
def get_session_attendance_history(session_id: int):
    conn = get_db()
    session = conn.execute("""
        SELECT id, start_time, end_time, status, timetable_id
        FROM attendance_sessions
        WHERE id = ?
    """, (session_id,)).fetchone()

    if session is None:
        conn.close()
        raise HTTPException(
            status_code=404,
            detail="Session not found",
        )

    end_time = session["end_time"] or datetime.now().isoformat()

    rows = conn.execute("""
        SELECT
            a.student_id,
            s.name,
            a.device_mac,
            a.node_id,
            MIN(a.timestamp) AS first_seen,
            MAX(a.timestamp) AS last_seen,
            COUNT(*) AS detection_count
        FROM attendance AS a
        JOIN students AS s
          ON s.student_id = a.student_id
        WHERE a.timestamp >= ?
          AND a.timestamp <= ?
        GROUP BY a.student_id, s.name, a.device_mac, a.node_id
        ORDER BY s.name
    """, (session["start_time"], end_time)).fetchall()

    conn.close()

    return {
        "session_id": session["id"],
        "status": session["status"],
        "timetable_id": session["timetable_id"],
        "students": [dict(row) for row in rows],
    }
