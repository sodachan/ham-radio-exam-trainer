#!/usr/bin/env python3
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Optional
import llm_settings as llm

import bcrypt
from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Response, Request
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field, SecretStr


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DB_PATH = Path(os.getenv("DATABASE_PATH", DATA_DIR / "app.db"))
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8002"))
SESSION_COOKIE = "ham_exam_session"
SESSION_DAYS = 14
PROMPT_VERSION = "ham-explain-v4"
DEFAULT_API_PATH = "/openai/v1/chat/completions"
LLM_PRECACHE_ON_START = os.getenv("LLM_PRECACHE_ON_START", "1") == "1"

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.modelarts-maas.com")
LLM_API_PATH = os.getenv("LLM_API_PATH", DEFAULT_API_PATH)
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-v3.2")
SESSION_SECRET = os.getenv("SESSION_SECRET", secrets.token_urlsafe(32))
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0") == "1"

ADMIN_USERNAME = os.getenv("HAM_ADMIN_USERNAME", "").strip() or None
Provider = Literal["openai", "deepseek"]
Bank = Literal["A", "B", "C"]
BANK_FILES = ("questions.json", "questions-B.json", "questions-C.json")
PAGE_ASSETS = ("app.js", "admin.js", "styles.css", "topic-titles.js")
PUBLIC_FILES = {*PAGE_ASSETS, "questions.js", *BANK_FILES}
QUESTIONS_BY_TYPE = {}
for filename in BANK_FILES:
    with (ROOT / filename).open(encoding="utf-8") as file:
        for question in json.load(file)["questions"]:
            QUESTIONS_BY_TYPE.setdefault(question["type"], question)
            figure = question.get("figure")
            if figure and Path(figure).name == figure and figure.endswith(".jpg"):
                PUBLIC_FILES.add("figures/" + figure)
PRECACHE_LOCK = threading.Lock()
PRECACHE_STATE = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "current": None,
    "processed": 0,
    "generated": 0,
    "skipped": 0,
    "failed": 0,
    "last_error": None,
}

app = FastAPI(title="Ham Exam Trainer")


class Credentials(BaseModel):
    username: str
    password: str


class RegisterPayload(Credentials):
    invite_code: str


class ProgressPayload(BaseModel):
    answers: dict
    wrong: list


class PracticePosition(BaseModel):
    bank: Bank
    question: str = Field(max_length=64)
    scope: Literal["all", "upgrade"]
    category: str = Field(max_length=64)
    order: Literal["sequential", "random"]
    seed: int = Field(ge=0, le=2**32 - 1)
    selected: list[Literal["A", "B", "C", "D"]]
    updated: int = Field(ge=0, le=2**53 - 1)


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


def llm_cache_count(config):
    return one(
        "SELECT COUNT(*) AS count FROM llm_cache WHERE model = ? AND prompt_version = ?",
        (config.cache_model, PROMPT_VERSION),
    )["count"]


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

            CREATE TABLE IF NOT EXISTS practice_positions (
                user_id INTEGER PRIMARY KEY REFERENCES users(id),
                payload TEXT NOT NULL,
                updated INTEGER NOT NULL
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
        connection.execute("BEGIN IMMEDIATE")
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(progress)")}
        legacy = bool(columns) and "bank" not in columns
        if legacy:
            connection.execute("ALTER TABLE progress RENAME TO progress_legacy")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS progress (
                user_id INTEGER NOT NULL REFERENCES users(id),
                bank TEXT NOT NULL,
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, bank)
            )
        """)
        if legacy:
            connection.execute("""
                INSERT INTO progress (user_id, bank, payload, updated_at)
                SELECT user_id, 'A', payload, updated_at FROM progress_legacy
            """)
            connection.execute("DROP TABLE progress_legacy")
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
                "你是业余无线电老师，用通俗的简体中文给初学者讲解眼前这道题。先点明正确选项，再从题干中挑出影响判断的条件，把条件怎样推出答案讲清楚。提到选项时写出关键含义，让读者能跟上推理。",
                "按题目需要展开：原理题讲清因果，必要时用一个小例子；计算题说明为什么用这个公式，再代入题中数值，保留单位；法规或事实题讲清所问的名称、主体、职责或适用条件，不编条文、历史原因或未提供的图示细节。",
                "多选题说明每个正确项为什么成立，可合并同一原理；问错误或例外时，指出哪句话违背了什么规则。挑与正确答案最容易混淆的实际选项，具体说明它错在哪一步或哪个词；没有值得比较的选项就略过。",
                "写成一至三个短段，通常 120～200 字。给必要的推理留足空间；要缩短时，先删重复结论、额外举例和口号。直接从题目切入，不抄整道题或整组选项。",
                "排版：首行写成“**答案：A、C**”（字母按本题填写），单独成段。空一行写 Markdown 标题“# 题目分析”，换行讲解。若有具体易错项，另起“# 易错点”一节。标题和正文分行，各段间空一行。",
                "计算题把关键算式独立成段，用 ×、÷、≈ 和普通单位，不用 LaTeX、表格或代码块。除答案外不整句加粗。",
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
                "题库给出的正确答案：" + "；".join(f"{key}. {question['options'][key]}" for key in question["answer"]),
                "请用约两段话讲透本题：把关键条件用在推理中，再点明最容易误判的一步。讲清就收笔。",
            ]),
        },
    ]


def llm_config():
    return llm.active_config(llm.Config("legacy", LLM_MODEL, LLM_API_KEY, chat_completions_url()))


def cached_explanation(question_type, config):
    return one(
        "SELECT content, created_at FROM llm_cache WHERE question_type = ? AND model = ? AND prompt_version = ?",
        (question_type, config.cache_model, PROMPT_VERSION),
    )


def save_explanation(question_type, content, config):
    created_at = utc_now().isoformat()
    execute(
        """
        INSERT INTO llm_cache (question_type, model, prompt_version, content, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(question_type, model, prompt_version)
        DO UPDATE SET content = excluded.content, created_at = excluded.created_at
        """,
        (question_type, config.cache_model, PROMPT_VERSION, content, created_at),
    )
    return created_at


def precache_status():
    with PRECACHE_LOCK:
        status = dict(PRECACHE_STATE)
    total = len(QUESTIONS_BY_TYPE)
    config = llm_config()
    cached = llm_cache_count(config)
    status.update({
        "enabled": LLM_PRECACHE_ON_START,
        "configured": bool(config.key),
        "model": config.model,
        "promptVersion": PROMPT_VERSION,
        "total": total,
        "cached": cached,
        "remaining": max(total - cached, 0),
    })
    return status


def update_precache_state(**values):
    with PRECACHE_LOCK:
        PRECACHE_STATE.update(values)


def run_precache(config):
    try:
        for question in QUESTIONS_BY_TYPE.values():
            if llm_config() != config:
                break
            with PRECACHE_LOCK:
                PRECACHE_STATE["current"] = question["type"]
            if cached_explanation(question["type"], config):
                with PRECACHE_LOCK:
                    PRECACHE_STATE["processed"] += 1
                    PRECACHE_STATE["skipped"] += 1
                continue
            try:
                content = llm.complete(config, build_messages(question))
                save_explanation(question["type"], content, config)
                with PRECACHE_LOCK:
                    PRECACHE_STATE["generated"] += 1
            except HTTPException as error:
                with PRECACHE_LOCK:
                    PRECACHE_STATE["failed"] += 1
                    PRECACHE_STATE["last_error"] = str(error.detail)
            except Exception as error:
                with PRECACHE_LOCK:
                    PRECACHE_STATE["failed"] += 1
                    PRECACHE_STATE["last_error"] = str(error)
            with PRECACHE_LOCK:
                PRECACHE_STATE["processed"] += 1
    finally:
        update_precache_state(running=False, finished_at=utc_now().isoformat(), current=None)


def start_precache():
    config = llm_config()
    if not config.key:
        raise HTTPException(status_code=500, detail="Server missing LLM_API_KEY.")
    with PRECACHE_LOCK:
        if PRECACHE_STATE["running"]:
            return False
        PRECACHE_STATE.update({
            "running": True,
            "started_at": utc_now().isoformat(),
            "finished_at": None,
            "current": None,
            "processed": 0,
            "generated": 0,
            "skipped": 0,
            "failed": 0,
            "last_error": None,
        })
        thread = threading.Thread(target=run_precache, args=(config,), daemon=True)
        thread.start()
    return True


@app.on_event("startup")
def startup():
    init_db()
    config = llm_config()
    if LLM_PRECACHE_ON_START and config.key and llm_cache_count(config) < len(QUESTIONS_BY_TYPE):
        start_precache()


def page(name):
    html = (ROOT / name).read_text(encoding="utf-8")
    for asset in PAGE_ASSETS:
        reference = f'"/{asset}"'
        if reference in html:
            path = ROOT / asset
            if path.resolve() != path:
                raise HTTPException(status_code=404)
            version = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
            html = html.replace(reference, f'"/assets/{version}/{asset}"')
    return HTMLResponse(html, headers={"Cache-Control": "private, no-store"})


@app.get("/login.html", include_in_schema=False)
@app.get("/")
def login_page():
    return page("login.html")


@app.get("/index.html", include_in_schema=False)
@app.get("/app")
def app_index(session_token: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE)):
    if not session_token:
        return RedirectResponse(url="/", status_code=302, headers={"Cache-Control": "private, no-store"})
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
        return RedirectResponse(url="/", status_code=302, headers={"Cache-Control": "private, no-store"})
    return page("index.html")


@app.post("/api/auth/register")
def register(payload: RegisterPayload, response: Response):
    username = payload.username.strip()
    if username == ADMIN_USERNAME:
        raise HTTPException(409, "该用户名已保留。")
    if len(username) < 3 or len(payload.password) < 6:
        raise HTTPException(status_code=400, detail="Username must be at least 3 chars and password at least 6 chars.")
    code_hash = hash_value(payload.invite_code.strip())
    if not one("SELECT 1 FROM invite_codes WHERE code_hash = ? AND used_by IS NULL", (code_hash,)):
        raise HTTPException(400, "邀请码无效或已使用。")
    password_hash = hash_password(payload.password)
    try:
        with db() as connection:
            cursor = connection.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                (username, password_hash, utc_now().isoformat()),
            )
            user_id = cursor.lastrowid
            consumed = connection.execute(
                "UPDATE invite_codes SET used_by = ?, used_at = ? WHERE code_hash = ? AND used_by IS NULL",
                (user_id, utc_now().isoformat(), code_hash),
            )
            if consumed.rowcount != 1:
                raise HTTPException(400, "邀请码无效或已使用。")
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
def me(response: Response, user=Depends(current_user)):
    response.headers["Cache-Control"] = "private, no-store"
    position = one("SELECT payload FROM practice_positions WHERE user_id=?", (user["id"],))
    recent = None if position else one(
        "SELECT bank FROM progress WHERE user_id=? ORDER BY updated_at DESC LIMIT 1", (user["id"],))
    return {"username": user["username"], "isAdmin": user["username"] == ADMIN_USERNAME,
            "position": json.loads(position["payload"]) if position else None,
            "recentBank": recent["bank"] if recent else None}


@app.put("/api/practice-position")
def save_practice_position(payload: PracticePosition, user=Depends(current_user)):
    execute("""
        INSERT INTO practice_positions(user_id, payload, updated) VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET payload=excluded.payload, updated=excluded.updated
        WHERE excluded.updated > practice_positions.updated
    """, (user["id"], payload.model_dump_json(), payload.updated))
    return {"ok": True}


def admin_user(user=Depends(current_user)):
    if user["username"] != ADMIN_USERNAME:
        raise HTTPException(403, "需要管理员权限。")
    return user


def admin_write(user=Depends(admin_user), x_admin_request: Optional[str] = Header(default=None)):
    if x_admin_request != "1":
        raise HTTPException(403, "无效的管理请求。")
    return user


class ModelRequest(BaseModel):
    provider: Provider
    api_key: Optional[SecretStr] = None


class SavedModel(BaseModel):
    provider: Provider
    model: str


class ModelSelection(ModelRequest):
    model: str


@app.exception_handler(RequestValidationError)
async def redact_admin_input(request: Request, exc: RequestValidationError):
    if request.url.path.startswith("/api/admin/"):
        return JSONResponse(status_code=422, content={"detail": "管理请求参数无效。"})
    return await request_validation_exception_handler(request, exc)


def provider_key(payload):
    supplied = payload.api_key.get_secret_value().strip() if payload.api_key else ""
    key = supplied or llm.read_profiles().get(payload.provider, {}).get("key")
    if not key:
        raise HTTPException(400, "请先输入该厂商的 API Key。")
    return key


@app.get("/admin")
def admin_page(user=Depends(admin_user)):
    return page("admin.html")


def settings_snapshot():
    profiles = llm.read_profiles(include_models=True)
    active = next((p for p, row in profiles.items() if row["active"]), "legacy")
    return {"active": active, "model": LLM_MODEL if active == "legacy" else profiles[active]["model"],
            "legacyConfigured": bool(LLM_API_KEY),
            "profiles": {p: {"model": row["model"], "models": row["models"]} for p, row in profiles.items()}}


@app.get("/api/admin/settings")
def admin_settings(response: Response, user=Depends(admin_user)):
    response.headers["Cache-Control"] = "no-store"
    return settings_snapshot()


@app.post("/api/admin/models")
def list_models(payload: ModelRequest, user=Depends(admin_write)):
    return {"models": llm.model_ids(payload.provider, provider_key(payload))}


@app.post("/api/admin/settings")
def set_model(payload: ModelSelection, user=Depends(admin_write)):
    key = provider_key(payload)
    available = llm.model_ids(payload.provider, key)
    if payload.model not in available:
        raise HTTPException(400, "请选择该密钥可用的模型。")
    llm.activate(payload.provider, payload.model, key, available,
                 reuse_key=not (payload.api_key and payload.api_key.get_secret_value().strip()))
    return settings_snapshot()


@app.post("/api/admin/settings/activate")
def activate_saved_model(payload: SavedModel, user=Depends(admin_write)):
    llm.activate(payload.provider, payload.model)
    return settings_snapshot()


@app.delete("/api/admin/settings/{provider}")
def remove_provider(provider: Provider, user=Depends(admin_write)):
    llm.delete_provider(provider)
    return settings_snapshot()


@app.post("/api/admin/invites")
def create_invite(response: Response, user=Depends(admin_write)):
    code = "ham-" + secrets.token_urlsafe(24)
    execute("INSERT INTO invite_codes (code_hash, created_at) VALUES (?, ?)",
            (hash_value(code), utc_now().isoformat()))
    response.headers["Cache-Control"] = "no-store"
    return {"code": code}


@app.get("/api/progress")
def get_progress(bank: Bank = "A", user=Depends(current_user)):
    row = one("SELECT payload FROM progress WHERE user_id = ? AND bank = ?", (user["id"], bank))
    if not row:
        return {"answers": {}, "wrong": []}
    return json.loads(row["payload"])


@app.put("/api/progress")
def put_progress(payload: ProgressPayload, bank: Bank = "A", user=Depends(current_user)):
    data = {"answers": payload.answers, "wrong": payload.wrong}
    execute(
        """
        INSERT INTO progress (user_id, bank, payload, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, bank) DO UPDATE SET payload = excluded.payload, updated_at = excluded.updated_at
        """,
        (user["id"], bank, json.dumps(data, ensure_ascii=False), utc_now().isoformat()),
    )
    return {"ok": True}


@app.post("/api/llm/explain")
def explain(payload: ExplainPayload, user=Depends(current_user)):
    question = QUESTIONS_BY_TYPE.get(payload.question_type)
    if not question:
        raise HTTPException(status_code=404, detail="Question not found.")
    config = llm_config()
    cached = cached_explanation(payload.question_type, config)
    if cached and not payload.force:
        return {
            "cached": True,
            "model": config.model,
            "generatedAt": cached["created_at"],
            "content": cached["content"],
        }
    content = llm.complete(config, build_messages(question))
    created_at = save_explanation(payload.question_type, content, config)
    return {"cached": False, "model": config.model, "generatedAt": created_at, "content": content}


@app.get("/api/llm/precache")
def get_precache(user=Depends(current_user)):
    return precache_status()


@app.post("/api/llm/precache")
def post_precache(user=Depends(admin_write)):
    started = start_precache()
    status = precache_status()
    status["started"] = started
    return status


@app.get("/assets/{version}/{name}")
def versioned_asset(version: str, name: str):
    if name not in PAGE_ASSETS:
        raise HTTPException(status_code=404)
    path = ROOT / name
    if path.resolve() != path or not path.is_file():
        raise HTTPException(status_code=404)
    content = path.read_bytes()
    if version != hashlib.sha256(content).hexdigest()[:16]:
        raise HTTPException(status_code=404)
    media = "text/css" if name.endswith(".css") else "text/javascript"
    return Response(content, media_type=media, headers={"Cache-Control": "public, max-age=31536000, immutable"})


@app.get("/{path:path}")
def static_file(path: str):
    if path not in PUBLIC_FILES:
        raise HTTPException(status_code=404)
    target = ROOT / path
    if target.resolve() != target or not target.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(target, headers={"Cache-Control": "no-cache"})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)
