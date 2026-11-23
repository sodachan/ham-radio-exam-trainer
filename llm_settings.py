"""Private provider settings and calls to the configured model service."""
import json
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from fastapi import HTTPException

SETTINGS_PATH = Path(os.getenv("HAM_LLM_SETTINGS_PATH",
    Path.home() / ".config" / "ham-radio-exam-trainer" / "llm.sqlite3"))
BASES = {"openai": "https://api.openai.com/v1", "deepseek": "https://api.deepseek.com"}


@dataclass(frozen=True)
class Config:
    provider: str
    model: str
    key: str = field(repr=False)
    url: str

    @property
    def cache_model(self):
        return self.model if self.provider == "legacy" else f"{self.provider}:{self.model}"


def read_profiles(include_models=False):
    if not SETTINGS_PATH.exists():
        return {}
    with connect() as conn:
        conn.execute("BEGIN")
        profiles = {r["provider"]: dict(r) for r in conn.execute("SELECT * FROM profiles")}
        if include_models:
            for profile in profiles.values():
                profile["models"] = [profile["model"]]
            for provider, model in conn.execute("SELECT provider, model FROM saved_models ORDER BY model"):
                if model != profiles[provider]["model"]:
                    profiles[provider]["models"].append(model)
        return profiles


def connect():
    SETTINGS_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.close(os.open(SETTINGS_PATH, os.O_CREAT | os.O_RDWR, 0o600))
    conn = sqlite3.connect(SETTINGS_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS profiles (
        provider TEXT PRIMARY KEY, key TEXT NOT NULL, model TEXT NOT NULL, active INTEGER NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS saved_models (
        provider TEXT NOT NULL, model TEXT NOT NULL, PRIMARY KEY(provider, model))""")
    return conn


def activate(provider, model, key=None, available=None, reuse_key=False):
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        previous = conn.execute("SELECT key, model FROM profiles WHERE provider=?", (provider,)).fetchone()
        if key is None:
            if not previous or (model != previous["model"] and not conn.execute(
                    "SELECT 1 FROM saved_models WHERE provider=? AND model=?", (provider, model)).fetchone()):
                raise HTTPException(409, "该模型配置已更改，请刷新。")
            key = previous["key"]
        elif reuse_key and (not previous or key != previous["key"]):
            raise HTTPException(409, "该密钥已更改，请刷新。")
        if previous:
            conn.execute("INSERT OR IGNORE INTO saved_models VALUES(?,?)", (provider, previous["model"]))
        if previous and previous["key"] != key:
            saved = conn.execute("SELECT model FROM saved_models WHERE provider=?", (provider,)).fetchall()
            conn.executemany("DELETE FROM saved_models WHERE provider=? AND model=?",
                             [(provider, row["model"]) for row in saved if row["model"] not in available])
        conn.execute("UPDATE profiles SET active=0 WHERE active=1")
        conn.execute("INSERT OR REPLACE INTO profiles VALUES(?,?,?,1)", (provider, key, model))


def delete_provider(provider):
    with connect() as conn:
        conn.execute("DELETE FROM saved_models WHERE provider=?", (provider,))
        conn.execute("DELETE FROM profiles WHERE provider=?", (provider,))


def active_config(fallback):
    for row in read_profiles().values():
        if row["active"]:
            provider = row["provider"]
            endpoint = "/responses" if provider == "openai" else "/chat/completions"
            return Config(provider, row["model"], row["key"], BASES[provider] + endpoint)
    return fallback


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def upstream(url, key, body=None):
    if not url.startswith("https://"):
        raise HTTPException(500, "模型接口必须使用 HTTPS。")
    if not key or not key.isascii() or any(c.isspace() or not c.isprintable() for c in key):
        raise HTTPException(400, "请填写有效的 API Key。")
    req = Request(url, data=None if body is None else json.dumps(body).encode(),
                  headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        with build_opener(NoRedirect()).open(req, timeout=20 if body is None else 90) as resp:
            data = json.load(resp)
    except HTTPError as error:
        hint = {401: "请核对 API Key。", 403: "请检查账号权限或服务地区。", 429: "请检查额度或稍后重试。",
                400: "请确认所选模型支持文本解析。", 402: "请检查账户余额。"}.get(error.code, "请稍后重试。")
        raise HTTPException(502, f"模型服务返回 {error.code}。{hint}") from None
    except (URLError, OSError, ValueError):
        raise HTTPException(502, "模型服务连接失败或返回了无效数据。") from None
    if not isinstance(data, dict):
        raise HTTPException(502, "模型服务返回了无效数据。")
    return data


def model_ids(provider, key):
    rows = upstream(BASES[provider] + "/models", key).get("data")
    if not isinstance(rows, list):
        raise HTTPException(502, "模型列表格式无效。")
    ids = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            continue
        model = row["id"]
        if "text" not in row.get("output_modalities", ["text"]):
            continue
        if provider == "openai" and any(word in model for word in
                ("embedding", "moderation", "whisper", "tts", "dall-e", "gpt-image", "realtime", "audio", "transcribe", "sora")):
            continue
        ids.add(model)
    return sorted(ids)


def complete(config, messages):
    if config.provider == "openai":
        reply = upstream(config.url, config.key, {"model": config.model, "input": messages,
                         "store": False, "max_output_tokens": 4000})
        text = "\n".join(part["text"] for item in reply.get("output", []) if item.get("type") == "message"
                         for part in item.get("content", []) if part.get("type") == "output_text" and part.get("text"))
    else:
        body = {"model": config.model, "messages": messages, "max_tokens": 4000}
        if config.provider == "legacy":
            body.update(temperature=0.2, max_tokens=900)
        choices = upstream(config.url, config.key, body).get("choices") or []
        text = choices[0].get("message", {}).get("content") if choices else None
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(502, "模型未返回解析正文，请检查模型或重试。")
    return text.strip()
