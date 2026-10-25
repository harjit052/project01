import hashlib
import hmac
import json
import os
import time
from datetime import date, datetime
from pathlib import Path

import psycopg
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse


ROOT = Path(__file__).resolve().parent
app = FastAPI(title="Outpass Request System")


def database_url():
    value = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")
    if not value:
        raise HTTPException(503, "Database is not configured. Set DATABASE_URL in Vercel.")
    return value


def connect():
    return psycopg.connect(database_url(), connect_timeout=5)


def ensure_schema(connection):
    connection.execute("""
        CREATE TABLE IF NOT EXISTS outpass_requests (
            id BIGSERIAL PRIMARY KEY,
            pass_type TEXT NOT NULL,
            requester_name TEXT NOT NULL,
            out_date DATE NOT NULL,
            out_time TIME NOT NULL,
            in_date DATE NOT NULL,
            in_time TIME NOT NULL,
            purpose TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Pending'
                CHECK (status IN ('Pending', 'Approved', 'Rejected')),
            submitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)


def serialize(row):
    return {
        "id": row[0],
        "passType": row[1],
        "studentName": row[2],
        "outDate": row[3].strftime("%d/%m/%Y"),
        "outTime": row[4].strftime("%I:%M %p"),
        "inDate": row[5].strftime("%d/%m/%Y"),
        "inTime": row[6].strftime("%I:%M %p"),
        "purpose": row[7],
        "status": row[8],
        "submitted": row[9].strftime("%d %b %Y, %I:%M %p"),
    }


def admin_password():
    password = os.environ.get("OUTPASS_ADMIN_PASSWORD")
    if not password:
        raise HTTPException(503, "Admin access is not configured. Set OUTPASS_ADMIN_PASSWORD in Vercel.")
    return password


def make_admin_token(password):
    expires = int(time.time()) + 8 * 60 * 60
    signature = hmac.new(password.encode(), str(expires).encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{signature}"


def admin_authenticated(request):
    token = request.cookies.get("outpass_admin_session", "")
    try:
        expires_text, signature = token.split(".", 1)
        expires = int(expires_text)
        if expires < int(time.time()):
            return False
        expected = hmac.new(admin_password().encode(), expires_text.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected)
    except (ValueError, HTTPException):
        return False


@app.get("/")
def home():
    return FileResponse(ROOT / "new.html", media_type="text/html")


@app.get("/api/admin-session")
def admin_session(request: Request):
    return {"authenticated": admin_authenticated(request)}


@app.get("/api/outings")
def get_outings():
    with connect() as connection:
        ensure_schema(connection)
        rows = connection.execute("""
            SELECT id, pass_type, requester_name, out_date, out_time, in_date,
                   in_time, purpose, status, submitted_at
            FROM outpass_requests ORDER BY submitted_at DESC, id DESC
        """).fetchall()
    return {"outings": [serialize(row) for row in rows]}


@app.post("/api/gate-pass")
async def create_outpass(request: Request):
    try:
        body = await request.json()
        required = ("passType", "studentName", "outDate", "outTime", "inDate", "inTime", "purpose")
        if any(not str(body.get(key, "")).strip() for key in required):
            raise HTTPException(400, "All fields are required.")
        out_date = date.fromisoformat(body["outDate"])
        in_date = date.fromisoformat(body["inDate"])
        out_time = datetime.strptime(body["outTime"], "%H:%M").time()
        in_time = datetime.strptime(body["inTime"], "%H:%M").time()
        if (in_date, in_time) < (out_date, out_time):
            raise HTTPException(400, "Return date and time must be after departure.")
    except HTTPException:
        raise
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        raise HTTPException(400, "Enter valid departure and return dates and times.")

    with connect() as connection:
        ensure_schema(connection)
        row = connection.execute("""
            INSERT INTO outpass_requests
                (pass_type, requester_name, out_date, out_time, in_date, in_time, purpose)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id, pass_type, requester_name, out_date, out_time, in_date,
                      in_time, purpose, status, submitted_at
        """, (
            body["passType"].strip(), body["studentName"].strip(), out_date,
            out_time, in_date, in_time, body["purpose"].strip(),
        )).fetchone()
    return JSONResponse({"outing": serialize(row)}, status_code=201)


@app.post("/api/admin-login")
async def admin_login(request: Request):
    password = admin_password()
    try:
        submitted = str((await request.json()).get("password", ""))
    except (ValueError, json.JSONDecodeError):
        raise HTTPException(400, "Invalid login request.")
    if not hmac.compare_digest(submitted, password):
        raise HTTPException(401, "Incorrect admin password.")
    response = JSONResponse({"authenticated": True})
    response.set_cookie(
        "outpass_admin_session", make_admin_token(password), httponly=True,
        secure=bool(os.environ.get("VERCEL")), samesite="strict", max_age=8 * 60 * 60,
        path="/",
    )
    return response


@app.post("/api/admin-logout")
def admin_logout():
    response = JSONResponse({"authenticated": False})
    response.delete_cookie("outpass_admin_session", httponly=True, samesite="strict", path="/")
    return response


@app.patch("/api/outings/{outing_id}")
async def review_outpass(outing_id: int, request: Request):
    if not admin_authenticated(request):
        raise HTTPException(401, "Admin login required.")
    try:
        status = (await request.json()).get("status")
    except (ValueError, json.JSONDecodeError):
        raise HTTPException(400, "Invalid request.")
    if status not in ("Approved", "Rejected"):
        raise HTTPException(400, "Status must be Approved or Rejected.")
    with connect() as connection:
        ensure_schema(connection)
        row = connection.execute("""
            UPDATE outpass_requests SET status = %s WHERE id = %s
            RETURNING id, pass_type, requester_name, out_date, out_time, in_date,
                      in_time, purpose, status, submitted_at
        """, (status, outing_id)).fetchone()
    if row is None:
        raise HTTPException(404, "Request not found.")
    return {"outing": serialize(row)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
