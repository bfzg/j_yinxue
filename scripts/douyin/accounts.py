"""
账号管理

accounts.json 是编辑入口（面板改完写回这里），sqlite 是运行态。
新增账号只需要一个主页链接，或者裸的 sec_user_id。
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
import config
import pipeline_db as db

_SEC_RE = re.compile(r"MS4wLjAB[A-Za-z0-9_\-]{20,}")


def parse_sec_user_id(raw: str) -> Optional[str]:
    """从主页链接或裸 ID 里取出 sec_user_id"""
    raw = (raw or "").strip()
    if not raw:
        return None
    m = _SEC_RE.search(raw)
    if m:
        return m.group(0)
    if raw.startswith("http"):
        return None
    return raw if len(raw) > 20 else None


def make_slug(name: str, sec_user_id: str) -> str:
    """生成 ASCII 目录名。中文名做路径容易踩坑，取短哈希兜底"""
    ascii_part = re.sub(r"[^a-zA-Z0-9]+", "-", name or "").strip("-").lower()[:24]
    tail = hashlib.md5(sec_user_id.encode()).hexdigest()[:6]
    return f"{ascii_part}-{tail}" if ascii_part else f"acc-{tail}"


def load() -> list[dict]:
    if not config.ACCOUNTS_FILE.exists():
        return []
    data = json.loads(config.ACCOUNTS_FILE.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else data.get("accounts", [])


def save(accounts: list[dict]):
    config.ACCOUNTS_FILE.write_text(
        json.dumps(accounts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sync_to_db(conn=None) -> list[dict]:
    """把 json 里的账号写进 sqlite（json 是唯一编辑入口）"""
    own = conn is None
    conn = conn or db.connect()
    for acc in load():
        if not acc.get("sec_user_id"):
            continue
        if not acc.get("slug"):
            acc["slug"] = make_slug(acc.get("name", ""), acc["sec_user_id"])
        db.upsert_account(conn, acc)
    conn.commit()
    rows = db.list_accounts(conn)
    if own:
        conn.close()
    return rows


def add(raw: str, name: str = "", style: str = "", max_items: int = 0,
        enabled: bool = True) -> dict:
    sec = parse_sec_user_id(raw)
    if not sec:
        raise ValueError("无法从输入里解析出 sec_user_id，请粘贴完整的抖音主页链接")
    accounts = load()
    for acc in accounts:
        if acc.get("sec_user_id") == sec:
            raise ValueError(f"该账号已存在: {acc.get('name') or sec}")
    acc = {
        "sec_user_id": sec,
        "slug": make_slug(name, sec),
        "name": name or sec[:16],
        "style": style,
        "enabled": enabled,
        "max_items": int(max_items or 0),
    }
    accounts.append(acc)
    save(accounts)
    return acc


def update(sec_user_id: str, **fields) -> dict:
    accounts = load()
    target = next((a for a in accounts if a.get("sec_user_id") == sec_user_id), None)
    if target is None:
        raise ValueError(f"账号不存在: {sec_user_id}")
    for key in ("name", "style", "slug", "max_items", "enabled"):
        if key in fields and fields[key] is not None:
            target[key] = fields[key]
    target["max_items"] = max(0, int(target.get("max_items") or 0))
    target["enabled"] = bool(target.get("enabled", True))
    if not target.get("slug"):
        target["slug"] = make_slug(target.get("name", ""), sec_user_id)
    save(accounts)
    return target


def remove(sec_user_id: str) -> bool:
    accounts = load()
    kept = [a for a in accounts if a.get("sec_user_id") != sec_user_id]
    if len(kept) == len(accounts):
        return False
    save(kept)
    return True


def find(sec_user_id: str) -> Optional[dict]:
    for acc in load():
        if acc.get("sec_user_id") == sec_user_id:
            return acc
    return None


def set_cookie(raw: str) -> int:
    """面板里手动粘贴新 Cookie（换一个不常用的号，防封）。返回写入长度"""
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("Cookie 为空")
    if "=" not in raw:
        raise ValueError("看起来不像 Cookie，应包含 key=value 形式")
    header = "# 直接粘贴浏览器里的整串 Cookie（一行或多行均可，# 开头为注释）\n"
    config.COOKIE_FILE.write_text(header + raw + "\n", encoding="utf-8")
    return len(raw)


def cookie_preview() -> dict:
    """给面板展示 Cookie 状态，不回显敏感全文"""
    if not config.COOKIE_FILE.exists():
        return {"exists": False, "length": 0, "has_ttwid": False,
                "file": str(config.COOKIE_FILE)}
    try:
        cookie = config.get_cookie()
    except Exception as e:
        return {"exists": True, "length": 0, "has_ttwid": False, "error": str(e),
                "file": str(config.COOKIE_FILE)}
    return {
        "exists": True,
        "length": len(cookie),
        "has_ttwid": "ttwid=" in cookie,
        "has_sessionid": "sessionid=" in cookie,
        "head": cookie[:24] + "...",
        "file": str(config.COOKIE_FILE),
    }


def enabled_accounts() -> list[dict]:
    return [a for a in load() if a.get("enabled", True)]


if __name__ == "__main__":
    for r in sync_to_db():
        print(f"{r['name']:<14} max_items={r['max_items']:<5} items={r['item_count']:<5}"
              f" columns={r['column_count']}")
