"""
流程数据库 - 整条链路唯一的真源

账号 / 作品元数据 / 栏目 / 处理进度全部落在这里。
面板读它，导出器读它，重跑靠 aweme_id 幂等，绝不重复插入。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).parent))
import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    sec_user_id     TEXT PRIMARY KEY,
    slug            TEXT UNIQUE,
    name            TEXT,
    style           TEXT,
    enabled         INTEGER DEFAULT 1,
    max_items       INTEGER DEFAULT 0,
    nickname        TEXT,
    aweme_count     INTEGER,
    mix_count       INTEGER,
    follower_count  INTEGER,
    scanned_items   INTEGER DEFAULT 0,
    last_scan_at    TEXT,
    last_cursor     INTEGER DEFAULT 0,
    scan_note       TEXT
);

CREATE TABLE IF NOT EXISTS videos (
    aweme_id        TEXT PRIMARY KEY,
    sec_user_id     TEXT,
    title           TEXT,
    desc_raw        TEXT,
    create_time     INTEGER,
    duration_ms     INTEGER,
    mix_id          TEXT,
    mix_name        TEXT,
    ep_no           INTEGER,
    ep_total        INTEGER,
    chapters        TEXT,
    kind            TEXT DEFAULT 'video',
    digg_count      INTEGER DEFAULT 0,
    comment_count   INTEGER DEFAULT 0,
    share_count     INTEGER DEFAULT 0,
    cover_url       TEXT,
    source_url      TEXT,
    column_id       TEXT,
    episode_no      INTEGER,
    column_source   TEXT,
    stage           TEXT DEFAULT 'scanned',
    error           TEXT,
    audio_path      TEXT,
    transcript_path TEXT,
    article_path    TEXT,
    audio_url       TEXT,
    article_url       TEXT,
    asr_seconds     INTEGER DEFAULT 0,
    scanned_at      TEXT,
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS columns (
    column_id       TEXT PRIMARY KEY,
    sec_user_id     TEXT,
    name            TEXT,
    slug            TEXT,
    source          TEXT,
    description     TEXT,
    episode_total   INTEGER DEFAULT 0,
    sort            INTEGER DEFAULT 0,
    locked          INTEGER DEFAULT 0,
    cover_url       TEXT,
    cover_url_cloud TEXT,
    cover_file_id   TEXT,
    cover_source_url TEXT,
    cover_pushed_at TEXT,
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT,
    level       TEXT,
    scope       TEXT,
    ref_id      TEXT,
    message     TEXT
);

CREATE INDEX IF NOT EXISTS idx_videos_account ON videos(sec_user_id);
CREATE INDEX IF NOT EXISTS idx_videos_stage   ON videos(stage);
CREATE INDEX IF NOT EXISTS idx_videos_column  ON videos(column_id);
CREATE INDEX IF NOT EXISTS idx_events_ref     ON events(ref_id);
"""

# 处理阶段：scanned -> audio -> transcript -> article -> published（失败记 failed）
STAGES = ["scanned", "audio", "transcript", "article", "published", "failed", "skipped"]


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# 老库缺列时自动补齐，改表结构不用删库重扫
_MIGRATIONS = {
    "videos": ["audio_url TEXT", "article_url TEXT",
               "audio_file_id TEXT", "article_file_id TEXT",
               "cloud_pushed_at TEXT",
               # 后台点「下架」要能扛住下一次上架：本地 stage 保持 published，
               # 只靠 offline=1 把它挡在推送范围之外，否则元数据一同步就又亮了
               "offline INTEGER DEFAULT 0", "offline_at TEXT"],
    "accounts": ["scan_note TEXT"],
    "columns": ["cover_url_cloud TEXT", "cover_file_id TEXT",
                "cover_source_url TEXT", "cover_pushed_at TEXT"],
}


def _migrate(conn):
    for table, adds in _MIGRATIONS.items():
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for decl in adds:
            if decl.split()[0] not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {decl}")


def connect() -> sqlite3.Connection:
    config.DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(config.DB_FILE), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    return conn


def log_event(conn, level: str, scope: str, ref_id: str, message: str):
    conn.execute(
        "INSERT INTO events(ts, level, scope, ref_id, message) VALUES(?,?,?,?,?)",
        (now(), level, scope, ref_id, (message or "")[:500]),
    )
    conn.commit()


# ---------- accounts ----------

def upsert_account(conn, acc: dict):
    conn.execute(
        """INSERT INTO accounts(sec_user_id, slug, name, style, enabled, max_items)
           VALUES(?,?,?,?,?,?)
           ON CONFLICT(sec_user_id) DO UPDATE SET
             slug=excluded.slug, name=excluded.name, style=excluded.style,
             enabled=excluded.enabled, max_items=excluded.max_items""",
        (acc["sec_user_id"], acc.get("slug"), acc.get("name"), acc.get("style"),
         1 if acc.get("enabled", True) else 0, int(acc.get("max_items") or 0)),
    )


def list_accounts(conn) -> list[dict]:
    rows = conn.execute(
        """SELECT a.*,
                  (SELECT COUNT(*) FROM videos v WHERE v.sec_user_id = a.sec_user_id) AS item_count,
                  (SELECT COUNT(*) FROM videos v WHERE v.sec_user_id = a.sec_user_id
                     AND v.stage IN ('audio','transcript','article','published')) AS processed_count,
                  (SELECT COUNT(*) FROM columns c WHERE c.sec_user_id = a.sec_user_id) AS column_count
           FROM accounts a ORDER BY a.name"""
    ).fetchall()
    return [dict(r) for r in rows]


# ---------- videos ----------

def upsert_video(conn, v: dict):
    """按 aweme_id 幂等写入元数据。只更新元数据列，不覆盖已生成的产物与阶段"""
    conn.execute(
        """INSERT INTO videos(
             aweme_id, sec_user_id, title, desc_raw, create_time, duration_ms, mix_id, mix_name,
             ep_no, ep_total, chapters, kind, digg_count, comment_count, share_count, cover_url,
             source_url, scanned_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(aweme_id) DO UPDATE SET
             title=excluded.title,
             desc_raw=excluded.desc_raw,
             create_time=excluded.create_time,
             duration_ms=excluded.duration_ms,
             mix_id=excluded.mix_id,
             mix_name=excluded.mix_name,
             ep_no=excluded.ep_no,
             ep_total=excluded.ep_total,
             chapters=excluded.chapters,
             kind=excluded.kind,
             digg_count=excluded.digg_count,
             comment_count=excluded.comment_count,
             share_count=excluded.share_count,
             cover_url=excluded.cover_url,
             source_url=excluded.source_url,
             scanned_at=excluded.scanned_at""",
        (v["aweme_id"], v["sec_user_id"], v.get("title"), v.get("desc_raw"),
         v.get("create_time"), v.get("duration_ms"), v.get("mix_id"), v.get("mix_name"),
         v.get("ep_no"), v.get("ep_total"),
         json.dumps(v.get("chapters") or [], ensure_ascii=False),
         v.get("kind", "video"),
         v.get("digg_count", 0), v.get("comment_count", 0), v.get("share_count", 0),
         v.get("cover_url"), v.get("source_url"), now()),
    )


def set_stage(conn, aweme_id: str, stage: str, error: str = "", **fields):
    cols = ["stage", "updated_at", "error"]
    vals: list[Any] = [stage, now(), error or None]
    for key, val in fields.items():
        cols.append(key)
        vals.append(val)
    vals.append(aweme_id)
    conn.execute(
        "UPDATE videos SET " + ", ".join(f"{c}=?" for c in cols) + " WHERE aweme_id=?",
        vals,
    )
    conn.commit()


def get_video(conn, aweme_id: str) -> Optional[dict]:
    row = conn.execute("SELECT * FROM videos WHERE aweme_id=?", (aweme_id,)).fetchone()
    return dict(row) if row else None


def list_videos(conn, where: str = "", params: tuple = (), order: str = "create_time DESC",
                limit: int = 500, offset: int = 0) -> list[dict]:
    sql = "SELECT * FROM videos"
    if where:
        sql += f" WHERE {where}"
    sql += f" ORDER BY {order} LIMIT ? OFFSET ?"
    return [dict(r) for r in conn.execute(sql, tuple(params) + (limit, offset)).fetchall()]


def count_videos(conn, where: str = "", params: tuple = ()) -> int:
    sql = "SELECT COUNT(*) AS c FROM videos"
    if where:
        sql += f" WHERE {where}"
    return conn.execute(sql, params).fetchone()["c"]


# ---------- columns ----------

def upsert_column(conn, col: dict):
    conn.execute(
        """INSERT INTO columns(column_id, sec_user_id, name, slug, source, description,
                               episode_total, sort, locked, cover_url, updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(column_id) DO UPDATE SET
             sec_user_id=excluded.sec_user_id,
             slug=CASE WHEN columns.locked = 1 THEN columns.slug ELSE excluded.slug END,
             source=excluded.source,
             description=CASE WHEN columns.locked = 1 THEN columns.description
                              ELSE excluded.description END,
             episode_total=excluded.episode_total,
             sort=excluded.sort,
             cover_url=excluded.cover_url,
             updated_at=excluded.updated_at""",
        (col["column_id"], col.get("sec_user_id"), col.get("name"), col.get("slug"),
         col.get("source"), col.get("description"), col.get("episode_total", 0),
         col.get("sort", 0), 1 if col.get("locked") else 0, col.get("cover_url"), now()),
    )


def list_columns(conn, sec_user_id: str = None) -> list[dict]:
    sql = """SELECT c.*,
                    (SELECT COUNT(*) FROM videos v WHERE v.column_id = c.column_id) AS n_videos,
                    (SELECT MIN(v.episode_no) FROM videos v WHERE v.column_id = c.column_id) AS min_ep,
                    (SELECT MAX(v.episode_no) FROM videos v WHERE v.column_id = c.column_id) AS max_ep,
                    (SELECT SUM(CASE WHEN v.stage IN ('article','published') THEN 1 ELSE 0 END)
                       FROM videos v WHERE v.column_id = c.column_id) AS n_articles,
                    (SELECT SUM(CASE WHEN v.stage = 'published'
                                          AND IFNULL(v.offline, 0) = 0
                                     THEN 1 ELSE 0 END)
                       FROM videos v WHERE v.column_id = c.column_id) AS n_published,
                    (SELECT SUM(CASE WHEN v.stage = 'published'
                                          AND IFNULL(v.offline, 0) = 1
                                     THEN 1 ELSE 0 END)
                       FROM videos v WHERE v.column_id = c.column_id) AS n_offline,
                    (SELECT SUM(v.duration_ms) FROM videos v WHERE v.column_id = c.column_id) AS total_ms
             FROM columns c"""
    params: tuple = ()
    if sec_user_id:
        sql += " WHERE c.sec_user_id = ?"
        params = (sec_user_id,)
    sql += " ORDER BY c.sort, c.name"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def set_column_cover(conn, column_id: str, *, cloud_url: str, file_id: str = "",
                     source_url: str = ""):
    """
    合集封面的云端结果：永久直链 + fileID + 当初抓的那张原图地址

    原图地址一并存着，下次发布就能判断抖音那边换没换封面，没换直接跳过，
    不必再抓一遍再传一遍。
    """
    conn.execute(
        "UPDATE columns SET cover_url_cloud=?, cover_file_id=?, "
        "cover_source_url=?, cover_pushed_at=? WHERE column_id=?",
        (cloud_url, file_id, source_url, now(), column_id),
    )
    conn.commit()


def set_offline(conn, ids: list[str], offline: bool = True):
    """记下面板的上架/下架意图，本地阶段不动，只挡推送范围"""
    for aweme_id in [str(one) for one in ids if one]:
        conn.execute("UPDATE videos SET offline=?, offline_at=? WHERE aweme_id=?",
                     (1 if offline else 0, now(), aweme_id))
    conn.commit()


def delete_column(conn, column_id: str):
    conn.execute("UPDATE videos SET column_id=NULL, episode_no=NULL,"
                 " column_source=NULL WHERE column_id=?", (column_id,))
    conn.execute("DELETE FROM columns WHERE column_id=?", (column_id,))
    conn.commit()


# ---------- 汇总 ----------

def stats(conn) -> dict:
    out: dict[str, Any] = {"stages": {}, "accounts": 0, "columns": 0, "videos": 0,
                           "total_hours": 0.0, "asr_seconds": 0}
    out["accounts"] = conn.execute("SELECT COUNT(*) c FROM accounts").fetchone()["c"]
    out["columns"] = conn.execute("SELECT COUNT(*) c FROM columns").fetchone()["c"]
    out["videos"] = conn.execute("SELECT COUNT(*) c FROM videos").fetchone()["c"]
    for r in conn.execute("SELECT stage, COUNT(*) c, SUM(duration_ms) ms"
                          " FROM videos GROUP BY stage"):
        out["stages"][r["stage"] or "unknown"] = {
            "n": r["c"], "hours": round((r["ms"] or 0) / 3600000, 1)}
    out["total_hours"] = round(sum(v["hours"] for v in out["stages"].values()), 1)
    out["asr_seconds"] = conn.execute(
        "SELECT COALESCE(SUM(asr_seconds),0) s FROM videos").fetchone()["s"]
    return out


if __name__ == "__main__":
    c = connect()
    print(json.dumps(stats(c), ensure_ascii=False, indent=2))
