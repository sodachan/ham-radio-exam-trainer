#!/usr/bin/env python3
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import ssl
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import bcrypt
from fastapi import Cookie, Depends, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DB_PATH = Path(os.getenv("DATABASE_PATH", DATA_DIR / "app.db"))
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8002"))
SESSION_COOKIE = "ham_exam_session"
SESSION_DAYS = 14
PROMPT_VERSION = "ham-explain-v1"
DEFAULT_API_PATH = "/openai/v1/chat/completions"
TIMEOUT_SECONDS = 90

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.modelarts-maas.com")
LLM_API_PATH = os.getenv("LLM_API_PATH", DEFAULT_API_PATH)
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-v3.2")
SESSION_SECRET = os.getenv("SESSION_SECRET", secrets.token_urlsafe(32))
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0") == "1"

with (ROOT / "questions.json").open("r", encoding="utf-8") as file:
    QUESTION_BANK = json.load(file)
QUESTIONS_BY_TYPE = {question["type"]: question for question in QUESTION_BANK["questions"]}

app = FastAPI(title="Ham Exam Trainer")


class Credentials(BaseModel):
    username: str
    password: str


class RegisterPayload(Credentials):
    invite_code: str


class ProgressPayload(BaseModel):
    answers: dict
    wrong: list


class ExplainPayload(BaseModel):
    question_type: str
    force: bool = False


def utc_now():
    return datetime.now(timezone.utc)


def db():
    DATA_DIR.mkdir(exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def execute(query, params=()):
    with db() as connection:
        connection.execute(query, params)
        connection.commit()


def one(query, params=()):
    with db() as connection:
        return connection.execute(query, params).fetchone()


def all_rows(query, params=()):
    with db() as connection:
        return connection.execute(query, params).fetchall()


def hash_value(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_password(password):
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password, hashed):
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


def session_hash(token):
    return hmac.new(SESSION_SECRET.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()


def init_db():
    DATA_DIR.mkdir(exist_ok=True)
    with db() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS invite_codes (
                code_hash TEXT PRIMARY KEY,
                used_by INTEGER,
                created_at TEXT NOT NULL,
                used_at TEXT,
                FOREIGN KEY (used_by) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS progress (
                user_id INTEGER PRIMARY KEY,
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS llm_cache (
                question_type TEXT NOT NULL,
                model TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (question_type, model, prompt_version)
            );
            """
        )
        connection.commit()

    env_codes = [code.strip() for code in os.getenv("INVITE_CODES", "").split(",") if code.strip()]
    for code in env_codes:
        execute(
            "INSERT OR IGNORE INTO invite_codes (code_hash, created_at) VALUES (?, ?)",
            (hash_value(code), utc_now().isoformat()),
        )

    user_count = one("SELECT COUNT(*) AS count FROM users")["count"]
    invite_count = one("SELECT COUNT(*) AS count FROM invite_codes WHERE used_by IS NULL")["count"]
    if user_count == 0 and invite_count == 0:
        code = f"ham-{secrets.token_urlsafe(8)}"
        execute(
            "INSERT INTO invite_codes (code_hash, created_at) VALUES (?, ?)",
            (hash_value(code), utc_now().isoformat()),
        )
        print(f"Initial invite code: {code}")


def current_user(session_token: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE)):
    if not session_token:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    token_hash = session_hash(session_token)
    row = one(
        """
        SELECT users.id, users.username, sessions.expires_at
        FROM sessions
        JOIN users ON users.id = sessions.user_id
        WHERE sessions.token_hash = ?
        """,
        (token_hash,),
    )
    if not row:
        raise HTTPException(status_code=401, detail="Invalid session.")
    if datetime.fromisoformat(row["expires_at"]) <= utc_now():
        execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
        raise HTTPException(status_code=401, detail="Session expired.")
    return {"id": row["id"], "username": row["username"]}


def set_session_cookie(response: Response, user_id: int):
    token = secrets.token_urlsafe(32)
    expires_at = utc_now() + timedelta(days=SESSION_DAYS)
    execute(
        "INSERT INTO sessions (token_hash, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
        (session_hash(token), user_id, expires_at.isoformat(), utc_now().isoformat()),
    )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        max_age=SESSION_DAYS * 24 * 60 * 60,
    )


def chat_completions_url():
    base = LLM_BASE_URL.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/{LLM_API_PATH.lstrip('/')}"


def build_messages(question):
    return [
        {
            "role": "system",
            "content": "\n".join([
                "你是业余无线电考试辅导老师。",
                "请用简体中文帮助考生理解题目，而不是只背答案。",
                "输出必须包含这些小标题：本题考点、正确答案、选项辨析、助记口诀、易混提醒。",
                "解释要准确、简洁，避免编造题库之外的法规条文细节。",
            ]),
        },
        {
            "role": "user",
            "content": "\n".join([
                f"知识点：{question['category']}",
                f"题型：{'多选' if question['multi'] else '单选'}",
                f"题号：{question['type']}",
                f"题干：{question['question']}",
                "选项：",
                *[f"{key}. {value}" for key, value in question["options"].items()],
                f"正确答案：{''.join(question['answer'])}",
                "请解释为什么正确答案成立，其他选项为什么不选，并给一个便于记忆的口诀或联想。",
            ]),
        },
    ]


def call_llm(question):
    if not LLM_API_KEY:
        raise HTTPException(status_code=500, detail="Server missing LLM_API_KEY.")
    target = chat_completions_url()
    parsed = urlparse(target)
    if parsed.scheme != "https":
        raise HTTPException(status_code=500, detail="LLM_BASE_URL must resolve to an https URL.")

    body = json.dumps({
        "model": LLM_MODEL,
        "messages": build_messages(question),
        "temperature": 0.2,
        "max_tokens": 900,
    }).encode("utf-8")
    request = Request(
        target,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {LLM_API_KEY}",
        },
        method="POST",
    )
    try:
        context = ssl.create_default_context()
        with urlopen(request, timeout=TIMEOUT_SECONDS, context=context) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        text = error.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=error.code, detail=text or error.reason)
    except URLError as error:
        raise HTTPException(status_code=502, detail=f"Upstream request failed: {error.reason}")

    content = payload.get("choices", [{}])[0].get("message", {}).get("content")
    if not content:
        raise HTTPException(status_code=502, detail="LLM response missing choices[0].message.content.")
    return content.strip()


@app.on_event("startup")
def startup():
    init_db()


@app.get("/")
def login_page():
    return FileResponse(ROOT / "login.html")


@app.get("/app")
def app_index(session_token: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE)):
    if not session_token:
        return RedirectResponse(url="/", status_code=302)
    token_hash = session_hash(session_token)
    row = one(
        """
        SELECT users.id, users.username, sessions.expires_at
        FROM sessions
        JOIN users ON users.id = sessions.user_id
        WHERE sessions.token_hash = ?
        """,
        (token_hash,),
    )
    if not row or datetime.fromisoformat(row["expires_at"]) <= utc_now():
        return RedirectResponse(url="/", status_code=302)
    return FileResponse(ROOT / "index.html")


@app.post("/api/auth/register")
def register(payload: RegisterPayload, response: Response):
    username = payload.username.strip()
    if len(username) < 3 or len(payload.password) < 6:
        raise HTTPException(status_code=400, detail="Username must be at least 3 chars and password at least 6 chars.")
    invite = one("SELECT code_hash, used_by FROM invite_codes WHERE code_hash = ?", (hash_value(payload.invite_code.strip()),))
    if not invite or invite["used_by"]:
        raise HTTPException(status_code=400, detail="Invalid or used invite code.")
    try:
        with db() as connection:
            cursor = connection.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                (username, hash_password(payload.password), utc_now().isoformat()),
            )
            user_id = cursor.lastrowid
            connection.execute(
                "UPDATE invite_codes SET used_by = ?, used_at = ? WHERE code_hash = ?",
                (user_id, utc_now().isoformat(), invite["code_hash"]),
            )
            connection.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="Username already exists.")
    set_session_cookie(response, user_id)
    return {"username": username}


@app.post("/api/auth/login")
def login(payload: Credentials, response: Response):
    row = one("SELECT id, username, password_hash FROM users WHERE username = ?", (payload.username.strip(),))
    if not row or not verify_password(payload.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    set_session_cookie(response, row["id"])
    return {"username": row["username"]}


@app.post("/api/auth/logout")
def logout(response: Response, session_token: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE)):
    if session_token:
        execute("DELETE FROM sessions WHERE token_hash = ?", (session_hash(session_token),))
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@app.get("/api/me")
def me(user=Depends(current_user)):
    return {"username": user["username"]}


@app.get("/api/progress")
def get_progress(user=Depends(current_user)):
    row = one("SELECT payload FROM progress WHERE user_id = ?", (user["id"],))
    if not row:
        return {"answers": {}, "wrong": []}
    return json.loads(row["payload"])


@app.put("/api/progress")
def put_progress(payload: ProgressPayload, user=Depends(current_user)):
    data = {"answers": payload.answers, "wrong": payload.wrong}
    execute(
        """
        INSERT INTO progress (user_id, payload, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET payload = excluded.payload, updated_at = excluded.updated_at
        """,
        (user["id"], json.dumps(data, ensure_ascii=False), utc_now().isoformat()),
    )
    return {"ok": True}


@app.post("/api/llm/explain")
def explain(payload: ExplainPayload, user=Depends(current_user)):
    question = QUESTIONS_BY_TYPE.get(payload.question_type)
    if not question:
        raise HTTPException(status_code=404, detail="Question not found.")
    cached = one(
        "SELECT content, created_at FROM llm_cache WHERE question_type = ? AND model = ? AND prompt_version = ?",
        (payload.question_type, LLM_MODEL, PROMPT_VERSION),
    )
    if cached and not payload.force:
        return {
            "cached": True,
            "model": LLM_MODEL,
            "generatedAt": cached["created_at"],
            "content": cached["content"],
        }
    content = call_llm(question)
    created_at = utc_now().isoformat()
    execute(
        """
        INSERT INTO llm_cache (question_type, model, prompt_version, content, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(question_type, model, prompt_version)
        DO UPDATE SET content = excluded.content, created_at = excluded.created_at
        """,
        (payload.question_type, LLM_MODEL, PROMPT_VERSION, content, created_at),
    )
    return {"cached": False, "model": LLM_MODEL, "generatedAt": created_at, "content": content}


@app.get("/{path:path}")
def static_file(path: str):
    target = (ROOT / path).resolve()
    if not str(target).startswith(str(ROOT)) or not target.is_file():
        return FileResponse(ROOT / "index.html")
    return FileResponse(target)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)
