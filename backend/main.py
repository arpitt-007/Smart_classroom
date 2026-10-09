from fastapi import FastAPI
import time
from fastapi.middleware.cors import CORSMiddleware
import sqlite3
from datetime import datetime


# =========================================================
# APP
# =========================================================

app = FastAPI(title="Smart Classroom Attendance")


# =========================================================
# UNKNOWN DEVICES
# =========================================================

unknown_devices = {}


# =========================================================
# CORS
# =========================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================================================
# DATABASE
# =========================================================

DB_NAME = "attendance.db"


def get_db():
    return sqlite3.connect(DB_NAME)


# =========================================================
# INITIALIZE DATABASE
# =========================================================

def init_db():

    conn = get_db()
    cursor = conn.cursor()

    # -----------------------------------------------------
    # Registered students
    # -----------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            device_mac TEXT UNIQUE NOT NULL
        )
    """)

    # -----------------------------------------------------
    # Attendance records
    # -----------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id TEXT NOT NULL,
            node_id TEXT NOT NULL,
            device_mac TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            status TEXT NOT NULL
        )
    """)

    # -----------------------------------------------------
    # Attendance sessions
    # -----------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS attendance_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_time TEXT NOT NULL,
            end_time TEXT,
            duration_minutes REAL,
            status TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


init_db()


# =========================================================
# ROOT
# =========================================================

@app.get("/")
def root():

    return {
        "status": "online",
        "project": "Smart Classroom Attendance"
    }


# =========================================================
# REGISTER STUDENT
# =========================================================

@app.post("/api/register")
def register_student(data: dict):

    student_id = data.get("student_id")
    name = data.get("name")
    device_mac = data.get("device_mac")

    if not student_id or not name or not device_mac:

        return {
            "status": "error",
            "message": "student_id, name and device_mac are required"
        }

    # Normalize MAC address
    device_mac = device_mac.strip().upper()

    conn = get_db()
    cursor = conn.cursor()

    try:

        cursor.execute("""
            INSERT INTO students
            (student_id, name, device_mac)
            VALUES (?, ?, ?)
        """, (
            student_id,
            name,
            device_mac
        ))

        conn.commit()

    except sqlite3.IntegrityError:

        conn.close()

        return {
            "status": "error",
            "message": "Student ID or device MAC already registered"
        }

    conn.close()


    # =====================================================
    # IMPORTANT FIX
    #
    # Remove the device from the unknown-device list
    # immediately after successful registration.
    # =====================================================

    unknown_devices.pop(device_mac, None)


    print("\n================================")
    print("       STUDENT REGISTERED")
    print("================================")
    print("Student ID :", student_id)
    print("Name       :", name)
    print("Device MAC :", device_mac)
    print("================================")


    return {
        "status": "registered",
        "student_id": student_id,
        "name": name,
        "device_mac": device_mac
    }


# =========================================================
# ESP32 NODE DETECTION
# =========================================================

@app.post("/api/node")
def receive_node(data: dict):

    node_id = data.get("node_id")
    device_mac = data.get("device_mac")

    timestamp = datetime.now().isoformat()


    if not device_mac:

        return {
            "status": "error",
            "message": "No device MAC received"
        }


    # Normalize MAC
    device_mac = device_mac.strip().upper()


    conn = get_db()
    cursor = conn.cursor()


    # =====================================================
    # FIND STUDENT USING MAC
    # =====================================================

    cursor.execute("""
        SELECT student_id, name
        FROM students
        WHERE device_mac = ?
    """, (device_mac,))


    student = cursor.fetchone()


    # =====================================================
    # REGISTERED STUDENT
    # =====================================================

    if student:

        student_id = student[0]
        name = student[1]


        # -------------------------------------------------
        # IMPORTANT FIX
        #
        # If this MAC was previously unknown but is now
        # registered, remove it from unknown_devices.
        # -------------------------------------------------

        unknown_devices.pop(device_mac, None)


        # -------------------------------------------------
        # Record attendance
        # -------------------------------------------------

        cursor.execute("""
            INSERT INTO attendance
            (student_id, node_id, device_mac, timestamp, status)
            VALUES (?, ?, ?, ?, ?)
        """, (
            student_id,
            node_id,
            device_mac,
            timestamp,
            "PRESENT"
        ))


        conn.commit()
        conn.close()


        print("\n================================")
        print("       STUDENT PRESENT")
        print("================================")
        print("Student ID :", student_id)
        print("Name       :", name)
        print("Device MAC :", device_mac)
        print("Node       :", node_id)
        print("Time       :", timestamp)
        print("Status     : PRESENT")
        print("================================")


        return {
            "status": "present",
            "student_id": student_id,
            "name": name,
            "node_id": node_id,
            "timestamp": timestamp
        }


    # =====================================================
    # UNKNOWN DEVICE
    # =====================================================

    else:

        conn.close()


        # -------------------------------------------------
        # Store/update unknown device
        # -------------------------------------------------

        unknown_devices[device_mac] = {
            "mac": device_mac,
            "node_id": node_id,
            "timestamp": timestamp
        }


        print("\n================================")
        print("       UNKNOWN DEVICE")
        print("================================")
        print("MAC :", device_mac)
        print("Node:", node_id)
        print("Time:", timestamp)
        print("================================")


        return {
            "status": "unknown_device",
            "device_mac": device_mac,
            "node_id": node_id,
            "timestamp": timestamp
        }


# =========================================================
# UNKNOWN DEVICES
# =========================================================

@app.get("/api/unknown-devices")
def get_unknown_devices():

    return list(unknown_devices.values())


# =========================================================
# VIEW REGISTERED STUDENTS
# =========================================================

@app.get("/api/students")
def get_students():

    conn = get_db()
    cursor = conn.cursor()


    cursor.execute("""
        SELECT student_id, name, device_mac
        FROM students
    """)


    rows = cursor.fetchall()

    conn.close()


    return [
        {
            "student_id": row[0],
            "name": row[1],
            "device_mac": row[2]
        }
        for row in rows
    ]


# =========================================================
# VIEW ATTENDANCE
# =========================================================

@app.get("/api/attendance")
def get_attendance():

    conn = get_db()
    cursor = conn.cursor()


    cursor.execute("""
        SELECT
            student_id,
            node_id,
            device_mac,
            timestamp,
            status
        FROM attendance
        ORDER BY id DESC
    """)


    rows = cursor.fetchall()

    conn.close()


    return [
        {
            "student_id": row[0],
            "node_id": row[1],
            "device_mac": row[2],
            "timestamp": row[3],
            "status": row[4]
        }
        for row in rows
    ]


# =========================================================
# START ATTENDANCE SESSION
# =========================================================

@app.post("/api/session/start")
def start_session():

    conn = get_db()
    cursor = conn.cursor()


    # Check if a session is already active

    cursor.execute("""
        SELECT id, start_time
        FROM attendance_sessions
        WHERE status = 'ACTIVE'
        ORDER BY id DESC
        LIMIT 1
    """)


    existing = cursor.fetchone()


    if existing:

        conn.close()

        return {
            "status": "already_active",
            "session_id": existing[0],
            "start_time": existing[1]
        }


    start_time = datetime.now().isoformat()


    cursor.execute("""
        INSERT INTO attendance_sessions
        (start_time, status)
        VALUES (?, ?)
    """, (
        start_time,
        "ACTIVE"
    ))


    session_id = cursor.lastrowid


    conn.commit()
    conn.close()


    return {
        "status": "started",
        "session_id": session_id,
        "start_time": start_time
    }


# =========================================================
# END ATTENDANCE SESSION
# =========================================================

@app.post("/api/session/end")
def end_session():

    conn = get_db()
    cursor = conn.cursor()


    cursor.execute("""
        SELECT id, start_time
        FROM attendance_sessions
        WHERE status = 'ACTIVE'
        ORDER BY id DESC
        LIMIT 1
    """)


    session = cursor.fetchone()


    if not session:

        conn.close()

        return {
            "status": "error",
            "message": "No active session"
        }


    session_id = session[0]
    start_time = datetime.fromisoformat(session[1])


    end_time = datetime.now()


    duration_seconds = (
        end_time - start_time
    ).total_seconds()


    duration_minutes = duration_seconds / 60


    cursor.execute("""
        UPDATE attendance_sessions
        SET
            end_time = ?,
            duration_minutes = ?,
            status = 'COMPLETED'
        WHERE id = ?
    """, (
        end_time.isoformat(),
        duration_minutes,
        session_id
    ))


    conn.commit()
    conn.close()


    return {
        "status": "completed",
        "session_id": session_id,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "duration_minutes": round(
            duration_minutes,
            2
        )
    }


# =========================================================
# CURRENT SESSION
# =========================================================

@app.get("/api/session/current")
def current_session():

    conn = get_db()
    cursor = conn.cursor()


    cursor.execute("""
        SELECT
            id,
            start_time,
            end_time,
            duration_minutes,
            status
        FROM attendance_sessions
        WHERE status = 'ACTIVE'
        ORDER BY id DESC
        LIMIT 1
    """)


    session = cursor.fetchone()


    conn.close()


    if not session:

        return {
            "active": False
        }


    return {
        "active": True,
        "session_id": session[0],
        "start_time": session[1],
        "end_time": session[2],
        "duration_minutes": session[3],
        "status": session[4]
    }


# =========================================================
# SESSION ATTENDANCE
# =========================================================

@app.get("/api/session/attendance")
def session_attendance():

    conn = get_db()
    cursor = conn.cursor()


    # Find active session

    cursor.execute("""
        SELECT id, start_time
        FROM attendance_sessions
        WHERE status = 'ACTIVE'
        ORDER BY id DESC
        LIMIT 1
    """)


    session = cursor.fetchone()


    if not session:

        conn.close()

        return {
            "active": False,
            "students": []
        }


    session_id = session[0]
    start_time = datetime.fromisoformat(session[1])


    now = datetime.now()


    elapsed_seconds = (
        now - start_time
    ).total_seconds()


    # Get registered students

    cursor.execute("""
        SELECT student_id, name, device_mac
        FROM students
    """)


    students = cursor.fetchall()


    results = []


    for student in students:

        student_id = student[0]
        name = student[1]
        device_mac = student[2]


        # Count detections since session began

        cursor.execute("""
            SELECT COUNT(*)
            FROM attendance
            WHERE student_id = ?
            AND timestamp >= ?
        """, (
            student_id,
            start_time.isoformat()
        ))


        detection_count = cursor.fetchone()[0]


        # ESP32 sends approximately every 5 seconds

        detected_seconds = detection_count * 5


        if elapsed_seconds > 0:

            percentage = (
                detected_seconds /
                elapsed_seconds
            ) * 100

        else:

            percentage = 0


        percentage = min(
            percentage,
            100
        )


        if percentage >= 60:

            status = "PRESENT"

        else:

            status = "LOW PRESENCE"


        results.append({
            "student_id": student_id,
            "name": name,
            "device_mac": device_mac,
            "detections": detection_count,
            "presence_percentage": round(
                percentage,
                1
            ),
            "status": status
        })


    conn.close()


    return {
        "active": True,
        "session_id": session_id,
        "elapsed_minutes": round(
            elapsed_seconds / 60,
            2
        ),
        "students": results
    }