"""
采集流水线管理面板（FastAPI + 单页前端）

端口 8766，只监听 127.0.0.1。四个区：
  1 账号管理    增删改 + 每账号抓取上限 + probe
  2 登录态      Cookie 粘贴/回写、体检红绿灯、扫码登录
  3 作品队列    筛选、勾选、批量「下载音频 → 转写 → 生成文章」
  4 栏目与上架  AI 自动分栏目、改名/锁定、导出前端数据、人工点上架

浏览器操作一律经 browser_scan.call 排队到同一个 Chrome 所在线程；
耗时任务丢后台线程，前端轮询 /api/jobs 看进度。
"""
from __future__ import annotations

import json
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

HERE = Path(__file__).parent
SCRIPTS_DIR = HERE.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import config  # noqa: E402
import pipeline_db as db  # noqa: E402

from fastapi import Body, FastAPI, HTTPException, Query  # noqa: E402
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,  # noqa: E402
                              PlainTextResponse)

app = FastAPI(title="抖音采集流水线面板", docs_url="/docs")

# 本地预览时 uni-app 的 H5 服务在另一个端口，得放行跨域
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

from fastapi.staticfiles import StaticFiles  # noqa: E402
app.mount("/static", StaticFiles(directory=str(HERE)), name="static")


@app.middleware("http")
async def no_store_ui(request, call_next):
    """面板改版频繁，HTML 和 app.js 一律不许缓存，免得点了按钮还是旧代码"""
    resp = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
    return resp

# ---------- 后台任务表 ----------

JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
MAX_JOBS = 60


def _job_update(job_id: str, **fields):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job:
            job.update(fields)
            job["updated_at"] = db.now()


def spawn(kind: str, fn: Callable[..., Any], *args, note: str = "",
          job_ref: Optional[dict] = None, **kwargs) -> dict:
    """
    起一个后台任务。fn 里如果要用浏览器，必须走 browser_scan.call，
    否则会撞上 "Playwright objects can only be used on the thread..."

    job_ref 传一个空 dict 时，线程启动前就把 job 填进去，
    长任务的 reporter 闭包可以拿 ref["id"] 实时回写进度，不存在竞态。
    """
    job_id = f"{kind}-{uuid.uuid4().hex[:8]}"
    job = {"id": job_id, "kind": kind, "status": "running", "note": note,
           "started_at": db.now(), "updated_at": db.now(), "elapsed": 0,
           "result": None, "error": "", "t0": time.time()}
    with JOBS_LOCK:
        JOBS[job_id] = job
        if len(JOBS) > MAX_JOBS:
            for key in sorted(JOBS, key=lambda k: JOBS[k]["t0"])[:len(JOBS) - MAX_JOBS]:
                if JOBS[key]["status"] != "running":
                    JOBS.pop(key, None)

    if job_ref is not None:
        job_ref.clear()
        job_ref.update(job)

    def _wrap():
        try:
            result = fn(*args, **kwargs)
            if isinstance(result, dict) and result.get("_note"):
                _job_update(job_id, note=result.pop("_note"))
            _job_update(job_id, status="ok", result=_plain(result),
                        elapsed=int(time.time() - job["t0"]))
        except Exception as e:  # noqa: BLE001
            _job_update(job_id, status="error", error=f"{type(e).__name__}: {e}"[:400],
                        elapsed=int(time.time() - job["t0"]))
            import traceback
            traceback.print_exc()

    threading.Thread(target=_wrap, daemon=True, name=f"job-{kind}").start()
    return job


def _plain(obj):
    try:
        return json.loads(json.dumps(obj, ensure_ascii=False, default=str))
    except Exception:
        return str(obj)


def job_list() -> list[dict]:
    with JOBS_LOCK:
        items = sorted(JOBS.values(), key=lambda j: j["t0"], reverse=True)
    out = []
    for j in items[:25]:
        d = {k: v for k, v in j.items() if k not in ("t0",)}
        d["elapsed"] = int(time.time() - j["t0"])
        out.append(d)
    return out


def running(kind_prefix: str = "") -> Optional[dict]:
    with JOBS_LOCK:
        for j in JOBS.values():
            if j["status"] == "running" and j["kind"].startswith(kind_prefix):
                return j
    return None


# ---------- 音频体积 / 音质档位 ----------

def _write_setting(key: str, value) -> None:
    """面板改的运行时开关落 settings.json，下次进程起来还在"""
    data = config._read_settings()
    data[key] = value
    config.SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                    encoding="utf-8")


def _audio_summary(conn) -> dict:
    """
    本地音频的体积账：现在多大、按当前档位全库多大、还有哪些能重编。
    全库预估用 videos.duration_ms（采集时就有），所以还没下载的集也算得出来。
    """
    prof = config.audio_profile()
    exts = {p["ext"] for p in config.AUDIO_PROFILES.values()}
    total = 0
    by_ext: dict[str, dict] = {}
    for f in sorted(config.AUDIO_DIR.iterdir()):
        if not f.is_file() or f.suffix.lstrip(".") not in exts or ".keep" in f.name:
            continue
        size = f.stat().st_size
        total += size
        d = by_ext.setdefault(f.suffix, {"count": 0, "bytes": 0})
        d["count"] += 1
        d["bytes"] += size

    row = conn.execute("SELECT COUNT(*) n, COALESCE(SUM(duration_ms),0) dur,"
                       " SUM(CASE WHEN audio_path LIKE '%.mp3' THEN 1 ELSE 0 END) legacy"
                       " FROM videos").fetchone()
    # 已下载音频的时长，用来算真实码率（全库时长只能用来做预估）
    got = conn.execute("SELECT COALESCE(SUM(duration_ms),0) FROM videos"
                       " WHERE audio_path IS NOT NULL AND audio_path != ''").fetchone()
    downloaded_ms = int(got[0] or 0)
    all_ms = int(row["dur"] or 0)
    legacy = int(row["legacy"] or 0)

    def kb_to_bytes(kbps: int) -> int:
        return int(all_ms / 1000 * kbps * 1000 / 8)

    cur_kbps = (round(total * 8 / (downloaded_ms / 1000) / 1000)
                if downloaded_ms and total else 0)  # kbps，按已下载音频的真实时长
    return {
        "profile": prof["name"],
        "label": prof["label"],
        "kbps": prof["kbps"],
        "ext": prof["ext"],
        "profiles": [{"name": k, "label": v["label"],
                      "short": v.get("short") or v["label"], "kbps": v["kbps"],
                      "ext": v["ext"], "note": v["note"]}
                     for k, v in config.AUDIO_PROFILES.items()],
        "files": sum(v["count"] for v in by_ext.values()),
        "bytes": total,
        "by_ext": by_ext,
        "episodes_total": int(row["n"] or 0),
        "hours_total": round(all_ms / 3600000, 1),
        "cur_kbps": cur_kbps,
        "files_hours": round(downloaded_ms / 3600000, 1),
        "avg_mb_now": round(total / max(sum(v["count"] for v in by_ext.values()), 1)
                            / 1048576, 1),
        "legacy_mp3": legacy,
        # 全库按各档位预估（含还没下载的集）
        "projection": {k: kb_to_bytes(v["kbps"])
                       for k, v in config.AUDIO_PROFILES.items()},
        "avg_mb_after": round(kb_to_bytes(prof["kbps"]) / max(row["n"] or 1, 1)
                              / 1048576, 1),
    }


@app.post("/api/audio/profile")
def api_audio_profile(body: dict = Body(...)):
    name = (body.get("profile") or "").strip()
    if name not in config.AUDIO_PROFILES:
        raise HTTPException(400, f"没有这个档位: {name}")
    config.AUDIO_PROFILE = name
    _write_setting("audio_profile", name)
    prof = config.audio_profile()
    return {"ok": True, "profile": name, "label": prof["label"],
            "ext": prof["ext"],
            "message": (f"新增音频改用 {prof['label']}（.{prof['ext']}）。"
                        f"存量文件点「瘦身存量」转换，不用重新下载")}


@app.post("/api/audio/compact")
def api_audio_compact(body: dict = Body(...)):
    """存量音频原地重编瘦身；dry_run=true 只算账不动文件"""
    import compress

    profile = (body.get("profile") or config.AUDIO_PROFILE).strip()
    if profile not in config.AUDIO_PROFILES:
        raise HTTPException(400, f"没有这个档位: {profile}")
    keep_source = bool(body.get("keep_source"))
    column = body.get("column_id") or ""
    limit = int(body.get("limit") or 0)
    include_pub = bool(body.get("include_published"))

    if body.get("dry_run"):
        conn = db.connect()
        try:
            pr = compress.plan(conn, config.audio_profile(profile), column=column,
                               limit=limit, include_published=include_pub)
        finally:
            conn.close()
        return {"dry_run": True, "estimate": round(pr["est_saved"] / 1073741824, 2),
                "count": pr["count"], "profile": pr["profile"],
                "avg_before_mb": round(pr["avg_before"] / 1048576, 1),
                "avg_after_mb": round(pr["avg_after"] / 1048576, 1)}

    if running("compact"):
        raise HTTPException(409, "瘦身任务正在跑，稍等")

    def _run():
        r = compress.run(profile=profile, limit=limit, column=column,
                         keep_source=keep_source, workers=int(body.get("workers") or 4),
                         include_published=include_pub, verbose=False)
        return {**r, "_note": (f"瘦身 {r['converted']} 集："
                               f"{r['before_gb']}GB → {r['after_gb']}GB，"
                               f"省 {r['saved_gb']}GB"
                               + (f"，失败 {r['fail_count']}" if r["fail_count"] else ""))}

    job = spawn("compact", _run, note="按档位重编存量音频")
    return {"job_id": job["id"]}


# ---------- 概览 ----------

@app.get("/api/overview")
def api_overview():
    import accounts as accmod
    import browser_scan
    import processor
    accmod.sync_to_db()  # accounts.json 是唯一编辑入口，先同步进库
    conn = db.connect()
    try:
        return {"stats": db.stats(conn), "accounts": db.list_accounts(conn),
                "columns": db.list_columns(conn),
                "stages": db.STAGES, "cookie": accmod.cookie_preview(),
                "browser": browser_scan.login_state(),
                "browser_log": browser_scan.recent_log(12),
                "jobs": job_list(),
                "process": processor.CURRENT.snapshot(),
                "engine": config.SCAN_ENGINE, "port": config.PANEL_PORT,
                "cloud": _cloud_summary(conn),
                "audio": _audio_summary(conn),
                "server_time": db.now()}
    finally:
        conn.close()


@app.get("/api/events")
def api_events(limit: int = Query(80)):
    conn = db.connect()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
    conn.close()
    return rows


# ---------- 账号 ----------

@app.get("/api/accounts")
def api_accounts():
    import accounts as accmod
    conn = db.connect()
    accmod.sync_to_db(conn)
    rows = db.list_accounts(conn)
    conn.close()
    return rows


@app.post("/api/accounts")
def api_add_account(body: dict = Body(...)):
    import accounts as accmod
    raw = (body.get("raw") or "").strip()
    if not raw:
        raise HTTPException(400, "请粘贴抖音主页链接或 sec_user_id")
    try:
        acc = accmod.add(raw, name=body.get("name", ""), style=body.get("style", ""),
                         max_items=int(body.get("max_items") or 0),
                         enabled=bool(body.get("enabled", True)))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    accmod.sync_to_db()
    if body.get("probe"):
        return {**acc, "probe": _probe_now(acc["sec_user_id"])}
    return acc


@app.put("/api/accounts/{sec_user_id}")
def api_update_account(sec_user_id: str, body: dict = Body(...)):
    import accounts as accmod
    fields = {k: v for k, v in body.items()
              if k in ("name", "style", "slug", "max_items", "enabled")}
    if "max_items" in fields:
        fields["max_items"] = max(0, int(fields["max_items"] or 0))
    if "enabled" in fields:
        fields["enabled"] = bool(fields["enabled"])
    try:
        acc = accmod.update(sec_user_id, **fields)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    accmod.sync_to_db()
    return acc


@app.delete("/api/accounts/{sec_user_id}")
def api_delete_account(sec_user_id: str):
    import accounts as accmod
    accmod.remove(sec_user_id)
    accmod.sync_to_db()
    return {"removed": True}


def _probe_now(sec_user_id: str) -> dict:
    """先走浏览器（风控下唯一可靠的一条路），起不来再退回 f2 接口"""
    import browser_scan
    try:
        return browser_scan.call(browser_scan.probe, sec_user_id, timeout=150)
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {str(e)[:160]}"
    try:
        import inventory as inv
        res = inv.probe_account(sec_user_id)
        res["engine"] = "api"
        return res
    except Exception as e2:  # noqa: BLE001
        return {"ok": False, "engine": "none", "mixes": [], "samples": [],
                "message": f"浏览器：{err}；接口：{type(e2).__name__}: {str(e2)[:120]}"}


def _check_now(sec_user_id: str) -> dict:
    import browser_scan
    return browser_scan.call(browser_scan.health_check, sec_user_id, timeout=180)


def _login_now(headless: bool = False, probe_sec: str = "",
               fresh: bool = True) -> dict:
    import browser_scan
    return browser_scan.call(browser_scan.login, headless=headless,
                            probe_sec=probe_sec, fresh=fresh, timeout=1800)


@app.get("/api/probe")
def api_probe(raw: str, async_: bool = Query(False, alias="async")):
    import accounts as accmod
    sec = accmod.parse_sec_user_id(raw)
    if not sec:
        raise HTTPException(400, "无法解析 sec_user_id")
    if async_:
        job = spawn("probe", _probe_now, sec)
        return {"job_id": job["id"]}
    return _probe_now(sec)


# ---------- 登录态 / Cookie ----------

@app.get("/api/cookie")
def api_cookie():
    import accounts as accmod
    return accmod.cookie_preview()


@app.post("/api/cookie")
def api_set_cookie(body: dict = Body(...)):
    import accounts as accmod
    import browser_scan
    raw = (body.get("cookie") or "").strip()
    try:
        n = accmod.set_cookie(raw)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    injected = 0
    if body.get("inject", True):
        try:
            injected = browser_scan.call(_inject_now, timeout=180)
        except Exception as e:  # noqa: BLE001
            return {"ok": True, "length": n, "injected": 0,
                    "warning": f"Cookie 已保存，但注入浏览器失败：{type(e).__name__}: {str(e)[:120]}"}
    return {"ok": True, "length": n, "injected": injected,
            "message": f"已写入 Cookie（{n} 字符），注入浏览器 {injected} 项。"
                       "粘贴的 Cookie 会立刻用于采集，无需重启面板"}


def _inject_now() -> int:
    import browser_scan
    sess = browser_scan.get_session(None)
    return sess.inject_cookie(force=True)


@app.post("/api/check")
def api_check(body: dict = Body(...)):
    import accounts as accmod
    import browser_scan
    sec = body.get("sec_user_id") or (accmod.enabled_accounts() or [{}])[0].get("sec_user_id")
    if not sec:
        raise HTTPException(400, "没有可用账号，先加一个")
    if body.get("async"):
        if running("check"):
            raise HTTPException(409, "体检正在进行中")
        job = spawn("check", _check_now, sec, note="浏览器翻页体检中")
        return {"job_id": job["id"]}
    return _check_now(sec)


@app.post("/api/login")
def api_login(body: dict = Body(...)):
    import browser_scan
    if running("login"):
        raise HTTPException(409, "已经在等扫码了，请在弹出的 Chrome 里完成扫码")
    sec = body.get("sec_user_id") or ""
    job = spawn("login", _login_now,
                headless=bool(body.get("headless")), probe_sec=sec,
                fresh=bool(body.get("fresh", True)),
                note="Chrome 已打开，等待扫码")
    return {"job_id": job["id"],
            "message": "已弹出 Chrome，请用手机抖音扫码；扫完我会自动把 Cookie 写回 cookie.txt"}


@app.post("/api/browser/reset")
def api_browser_reset():
    """残留的采集浏览器会把 profile 占死，面板里给一个一键释放的出口"""
    import browser_scan
    dead = browser_scan.call(browser_scan.kill_profile_chrome, timeout=60)
    return {"killed": dead,
            "message": (f"已关闭 {len(dead)} 个残留浏览器进程：{dead}" if dead
                        else "没有需要清理的浏览器，配置目录已释放")}


@app.post("/api/stop")
def api_stop():
    import browser_scan
    import processor
    browser_scan.request_stop()
    processor.stop()
    return {"ok": True}


# ---------- 采集 ----------

@app.post("/api/scan")
def api_scan(body: dict = Body(...)):
    import accounts as accmod
    import inventory as inv
    secs = body.get("sec_user_ids") or []
    if body.get("all") or not secs:
        secs = [a["sec_user_id"] for a in accmod.enabled_accounts()]
    if not secs:
        raise HTTPException(400, "没有启用中的账号")
    if running("scan"):
        raise HTTPException(409, "已有采集任务在跑，请等它结束或点停止")

    def _run(targets: list[str]):
        notes = []
        for sec in targets:
            res = inv.scan_account(sec, engine=body.get("engine"),
                                   headless=True if body.get("headless") else None,
                                   verbose=False)
            notes.append(f"{res.get('scan_note', '')}")
        return {"scanned": len(targets), "detail": notes, "_note": "；".join(notes[:6])}

    job = spawn("scan", _run, secs, note=f"排队采集 {len(secs)} 个账号")
    return {"job_id": job["id"], "accounts": secs}


# ---------- 作品队列 ----------

@app.get("/api/videos")
def api_videos(sec_user_id: str = "", column_id: str = "", stage: str = "",
               q: str = "", ungrouped: bool = False, limit: int = 200,
               offset: int = 0, order: str = "create_time"):
    conn = db.connect()
    where, params = ["1=1"], []
    if sec_user_id:
        where.append("v.sec_user_id=?")
        params.append(sec_user_id)
    if column_id == "__none__" or ungrouped:
        where.append("v.column_id IS NULL")
    elif column_id:
        where.append("v.column_id=?")
        params.append(column_id)
    if stage == "pending":
        where.append("v.stage NOT IN ('article','published','skipped')")
    elif stage:
        where.append("v.stage=?")
        params.append(stage)
    if q:
        where.append("(v.title LIKE ? OR v.mix_name LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    w = " AND ".join(where)
    order_sql = {"create_time": "v.create_time DESC", "episode":
                 "v.column_id, v.episode_no, v.create_time DESC",
                 "title": "v.title", "stage": "v.stage, v.create_time DESC"}\
        .get(order, "v.create_time DESC")
    sql = f"""SELECT v.*, a.name AS account_name, c.name AS column_name
              FROM videos v
              LEFT JOIN accounts a ON a.sec_user_id = v.sec_user_id
              LEFT JOIN columns c ON c.column_id = v.column_id
              WHERE {w} ORDER BY {order_sql} LIMIT ? OFFSET ?"""
    rows = [dict(r) for r in conn.execute(sql, tuple(params) + (limit, offset)).fetchall()]
    # 计数要和上面的查询用同一套 JOIN，否则 v. 前缀的条件在 count 里找不到列
    total = conn.execute(
        f"""SELECT COUNT(*) AS c FROM videos v
            LEFT JOIN accounts a ON a.sec_user_id = v.sec_user_id
            LEFT JOIN columns c ON c.column_id = v.column_id
            WHERE {w}""", params).fetchone()["c"]
    conn.close()
    return {"total": total, "items": rows}


@app.get("/api/videos/{aweme_id}")
def api_video(aweme_id: str):
    conn = db.connect()
    v = db.get_video(conn, aweme_id)
    if not v:
        conn.close()
        raise HTTPException(404, "作品不存在")
    acc = conn.execute("SELECT name, style FROM accounts WHERE sec_user_id=?",
                       (v["sec_user_id"],)).fetchone()
    col = conn.execute("SELECT name, slug FROM columns WHERE column_id=?",
                       (v["column_id"],)).fetchone() if v.get("column_id") else None
    conn.close()
    out = dict(v)
    out["account_name"] = acc["name"] if acc else ""
    out["style"] = acc["style"] if acc else ""
    out["column_name"] = col["name"] if col else ""
    out["article_md"] = _read_text(v.get("article_path"))
    out["transcript_txt"] = _read_text(v.get("transcript_path"), 6000)
    return out


def _read_text(path, cap: int = 0) -> str:
    if not path:
        return ""
    p = Path(str(path))
    if not p.exists():
        return ""
    text = p.read_text(encoding="utf-8", errors="replace")
    return text[:cap] if cap else text


@app.post("/api/process")
def api_process(body: dict = Body(...)):
    import processor
    if processor.CURRENT.running:
        raise HTTPException(409, "已有处理任务在跑，请先停止")
    ids = [str(i) for i in (body.get("ids") or [])]
    sec = body.get("sec_user_id") or ""
    column = body.get("column_id") or ""
    steps = body.get("steps") or processor.STEP_ORDER
    limit = int(body.get("limit") or 0)
    stage = body.get("stage") or None
    if stage == "pending":  # 前端语义：待处理 = 排除已成文/已发布，交给 select_ids 默认逻辑
        stage = None
    if not ids and not sec and not column and not body.get("all"):
        raise HTTPException(400, "请勾选作品，或指定账号/栏目，或明确 all=true 处理全部待处理")
    if running("process"):
        raise HTTPException(409, "处理任务仍在收尾，稍等几秒")
    sec = "" if body.get("all") else sec
    job = spawn("process", processor.run, steps=steps, sec_user_id=sec or None,
                column_id=column or None, ids=ids, limit=limit, stage=stage,
                force=bool(body.get("force")), verbose=False,
                note=f"{len(ids) or limit or '全部'} 集 × {len(steps)} 步")
    return {"job_id": job["id"], "queued": len(ids) or limit}


# ---------- 栏目 ----------

@app.get("/api/columns")
def api_columns(sec_user_id: str = ""):
    conn = db.connect()
    rows = db.list_columns(conn, sec_user_id or None)
    conn.close()
    return rows


@app.post("/api/columns/build")
def api_build_columns(body: dict = Body(...)):
    import accounts as accmod
    import columns as colmod
    secs = body.get("sec_user_ids") or ([body["sec_user_id"]]
                                       if body.get("sec_user_id") else
                                       [a["sec_user_id"] for a in accmod.enabled_accounts()])
    if not secs:
        raise HTTPException(400, "没有可用账号")

    def _run():
        conn = db.connect()
        out = []
        try:
            for sec in secs:
                out.append(colmod.build_columns(conn, sec_user_id=sec,
                                                use_ai=bool(body.get("use_ai", True)),
                                                verbose=False))
        finally:
            conn.close()
        return out

    job = spawn("columns", _run, note=f"AI 分栏目：{len(secs)} 个账号")
    return {"job_id": job["id"]}


@app.patch("/api/columns/{column_id}")
def api_patch_column(column_id: str, body: dict = Body(...)):
    import columns as colmod
    conn = db.connect()
    col = colmod.update_column(conn, column_id,
                              **{k: v for k, v in body.items()
                                 if k in ("name", "slug", "description", "sort",
                                          "episode_total", "locked")})
    conn.close()
    if not col:
        raise HTTPException(404, "栏目不存在")
    return col


@app.post("/api/columns")
def api_create_column(body: dict = Body(...)):
    import columns as colmod
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "栏目名不能为空")
    conn = db.connect()
    col = colmod.create_column(conn, body.get("sec_user_id") or "", name,
                              body.get("slug") or "")
    conn.close()
    return col


@app.delete("/api/columns/{column_id}")
def api_delete_column(column_id: str):
    conn = db.connect()
    db.delete_column(conn, column_id)
    conn.close()
    return {"removed": True}


@app.post("/api/videos/assign")
def api_assign(body: dict = Body(...)):
    ids = [str(i) for i in (body.get("ids") or [])]
    if not ids:
        raise HTTPException(400, "没有选中作品")
    conn = db.connect()
    import columns as colmod
    for aweme_id in ids:
        colmod.reassign(conn, aweme_id, body.get("column_id") or None,
                        body.get("episode_no"))
    conn.close()
    return {"assigned": len(ids)}


# ---------- 上架 / 导出 ----------

@app.post("/api/publish")
def api_publish(body: dict = Body(...)):
    """旧入口别名：统一走云端上架，避免两套发布逻辑分叉"""
    b = dict(body or {})
    b.setdefault("note", "面板上架")
    return api_cloud_publish(b)


@app.post("/api/export")
def api_export(body: dict = Body(...)):
    import exporter
    conn = db.connect()
    try:
        return exporter.export_all(conn, only_published=not body.get("draft"))
    finally:
        conn.close()


# ---------- uniCloud 云端上架 ----------

def _cloud_summary(conn) -> dict:
    """面板红绿灯用：只看本地配置和本地进度，不发网络请求"""
    import cloud_client as cc

    pushed = conn.execute("SELECT COUNT(*) FROM videos WHERE cloud_pushed_at "
                          "IS NOT NULL AND cloud_pushed_at != ''").fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM videos WHERE stage='article'").fetchone()[0]
    # 封面账：有内容的合集里已经转存了几张
    cov = conn.execute(
        "SELECT COUNT(*) FROM columns c WHERE c.cover_url_cloud IS NOT NULL "
        "AND c.cover_url_cloud != '' AND EXISTS (SELECT 1 FROM videos v "
        "WHERE v.column_id = c.column_id AND v.stage IN ('article','published'))").fetchone()[0]
    cols = conn.execute(
        "SELECT COUNT(*) FROM columns c WHERE EXISTS (SELECT 1 FROM videos v "
        "WHERE v.column_id = c.column_id AND v.stage IN ('article','published'))").fetchone()[0]
    out = cc.configured()
    out.update({"pushed": int(pushed), "pending": int(pending),
                "covers": int(cov), "coverTotal": int(cols)})
    return out


def _cloud_now() -> dict:
    conn = db.connect()
    try:
        return _cloud_summary(conn)
    finally:
        conn.close()


def _cloud_reporter(ref: dict):
    def _r(msg: str):
        jid = ref.get("id")
        if jid:
            _job_update(jid, note=str(msg)[:200])
    return _r


@app.get("/api/cloud/config")
def api_cloud_config():
    return _cloud_now()


@app.post("/api/cloud/config")
def api_cloud_set_config(body: dict = Body(...)):
    """
    面板里直接改云端配置（base_url / 前缀 / 令牌）。

    令牌写进 unicloud.key（已在 .gitignore 里），其余落 settings.json；
    改完本进程立刻生效，不用重启面板。
    """
    base = (body.get("base_url") or "").strip()
    host = (body.get("storage_host") or "").strip()
    prefix = (body.get("prefix") or "").strip()
    tok = (body.get("token") or "").strip()
    if base:
        if not base.startswith("https://"):
            raise HTTPException(400, "接口地址必须以 https:// 开头")
        config.UNICLOUD_BASE_URL = base.rstrip("/")
        _write_setting("unicloud_base_url", config.UNICLOUD_BASE_URL)
    if host:
        config.CLOUD_STORAGE_HOST = host.rstrip("/")
        _write_setting("unicloud_storage_host", config.CLOUD_STORAGE_HOST)
    if prefix:
        config.CLOUD_PATH_PREFIX = prefix.strip("/")
        _write_setting("unicloud_prefix", config.CLOUD_PATH_PREFIX)
    if tok:
        config.UNICLOUD_KEY_FILE.write_text(tok + "\n", encoding="utf-8")
    return _cloud_now()


@app.post("/api/cloud/test")
def api_cloud_test():
    """两个云函数分别探活，部署完第一时间能验联通"""
    import cloud_client as cc

    return cc.ping()


@app.post("/api/cloud/init")
def api_cloud_init(body: dict = Body(...)):
    import cloud_release

    try:
        return cloud_release.init_site(body.get("app") or None)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, str(e)[:300])


@app.post("/api/cloud/status")
def api_cloud_status():
    """线上 vs 本地条数对比，只读"""
    import cloud_release

    conn = db.connect()
    try:
        return cloud_release.compare(conn)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, str(e)[:300])
    finally:
        conn.close()


@app.post("/api/cloud/publish")
def api_cloud_publish(body: dict = Body(...)):
    """
    一条龙：音频 + 正文传云存储 → 元数据写云数据库 → dataVersion +1

    范围三选一：ids / column_id / sec_user_id，都不带给 all=true 才做全量。
    """
    import cloud_release

    ids = [str(i) for i in (body.get("ids") or [])]
    column = body.get("column_id") or ""
    sec = body.get("sec_user_id") or ""
    limit = int(body.get("limit") or 0)
    note = str(body.get("note") or "")[:180]
    if not ids and not column and not sec and not body.get("all"):
        raise HTTPException(400, "请勾选作品，或指定账号/栏目，或明确 all=true 上架全部")
    if running("cloud"):
        raise HTTPException(409, "云端任务进行中，请等当前任务结束")

    ref: dict = {}

    def _run():
        conn = db.connect()
        try:
            return cloud_release.release(
                conn, ids=ids or None, column_id=column, sec_user_id=sec,
                limit=limit, force=bool(body.get("force")), note=note,
                dry_run=bool(body.get("dry_run")), reporter=_cloud_reporter(ref))
        finally:
            conn.close()

    scope = len(ids) or ("栏目" if column else ("账号" if sec else "全部"))
    job = spawn("cloud-publish", _run, note=f"准备上架：{scope}", job_ref=ref)
    return {"job_id": job["id"], "scope": scope}


@app.post("/api/cloud/texts")
def api_cloud_texts(body: dict = Body(...)):
    """只重推正文：改了排版规则之后刷新线上文章，音频一个字节都不碰"""
    import cloud_release

    ids = [str(i) for i in (body.get("ids") or [])]
    column = body.get("column_id") or ""
    sec = body.get("sec_user_id") or ""
    limit = int(body.get("limit") or 0)
    if not ids and not column and not sec and not body.get("all"):
        raise HTTPException(400, "请勾选作品，或指定账号/栏目，或明确 all=true 刷全部正文")
    if running("cloud"):
        raise HTTPException(409, "云端任务进行中，请等当前任务结束")

    ref: dict = {}

    def _run():
        conn = db.connect()
        try:
            return cloud_release.release_texts(
                conn, ids=ids or None, column_id=column, sec_user_id=sec,
                limit=limit, note=str(body.get("note") or "")[:180] or "面板只刷正文",
                dry_run=bool(body.get("dry_run")), reporter=_cloud_reporter(ref))
        finally:
            conn.close()

    scope = len(ids) or ("栏目" if column else ("账号" if sec else "全部已上线"))
    job = spawn("cloud-texts", _run, note=f"准备刷新正文：{scope}", job_ref=ref)
    return {"job_id": job["id"], "scope": scope}


@app.post("/api/cloud/sync")
def api_cloud_sync(body: dict = Body(...)):
    """只同步元数据（改了栏目名/上下架状态时用），不重传文件"""
    import cloud_client as cc
    import cloud_release

    if running("cloud"):
        raise HTTPException(409, "云端任务进行中")

    ref: dict = {}

    def _run():
        conn = db.connect()
        try:
            cloud_release.reconcile_offline(conn)
            meta = cloud_release.push_meta(conn, reporter=_cloud_reporter(ref))
            rel = cc.call_content("pushRelease",
                                  note=str(body.get("note") or "只同步元数据")[:180],
                                  episodes=meta["episodes"]["total"])
            return {"meta": meta, "release": rel}
        finally:
            conn.close()

    job = spawn("cloud-sync", _run, note="同步元数据中", job_ref=ref)
    return {"job_id": job["id"]}


@app.post("/api/cloud/covers")
def api_cloud_covers(body: dict = Body(...)):
    """
    合集封面转存：一个合集一张首图，抓下来传云存储，回写永久直链

    发布流程里会自动跑这一步，这里留着单独入口：改了合集归属或换封面后
    只想补封面、不想重传音频时用。
    """
    import covers

    if running("cloud"):
        raise HTTPException(409, "云端任务进行中，请等当前任务结束")
    column = str(body.get("column_id") or "")
    force = bool(body.get("force"))
    dry = bool(body.get("dry_run"))

    ref: dict = {}

    def _run():
        conn = db.connect()
        try:
            return covers.refresh(conn, column_ids=[column] if column else None,
                                  force=force, dry_run=dry,
                                  reporter=_cloud_reporter(ref))
        finally:
            conn.close()

    job = spawn("cloud-cover", _run,
                note="封面试跑（不上传）" if dry else "合集封面转存中", job_ref=ref)
    return {"job_id": job["id"]}


@app.post("/api/cloud/offline")
def api_cloud_offline(body: dict = Body(...)):
    """
    下架：线上只置 enabled=false，purge_files=true 才删云存储文件

    ids 勾选单集，column_id / sec_user_id 直接按整个栏目或账号下架。
    """
    import cloud_release

    ids = [str(i) for i in (body.get("ids") or [])]
    column = body.get("column_id") or ""
    sec = body.get("sec_user_id") or ""
    if not ids and not column and not sec:
        raise HTTPException(400, "请勾选要下架的条目，或指定栏目/账号")
    conn = db.connect()
    try:
        return cloud_release.offline(conn, ids, purge_files=bool(body.get("purge_files")),
                                     column_id=column, sec_user_id=sec)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, str(e)[:300])
    finally:
        conn.close()


@app.post("/api/cloud/reconcile")
def api_cloud_reconcile():
    """把本地的下架标记和线上 enabled 对齐一次，只读线上、不改云端"""
    import cloud_release

    conn = db.connect()
    try:
        return cloud_release.reconcile_offline(conn)
    finally:
        conn.close()


@app.post("/api/cloud/restore")
def api_cloud_restore(body: dict = Body(...)):
    """
    重新上架：解除本地下架标记并同步元数据，不重传音频和正文。

    ids 勾选单集，column_id / sec_user_id 按整个栏目或账号恢复。
    """
    import cloud_release

    ids = [str(i) for i in (body.get("ids") or [])]
    column = body.get("column_id") or ""
    sec = body.get("sec_user_id") or ""
    if not ids and not column and not sec:
        raise HTTPException(400, "请勾选要恢复的条目，或指定栏目/账号")
    conn = db.connect()
    try:
        return cloud_release.restore(conn, ids, column_id=column, sec_user_id=sec)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, str(e)[:300])
    finally:
        conn.close()


@app.post("/api/cloud/retract")
def api_cloud_retract(body: dict = Body(...)):
    """把某集从线上撤回本地态（清云端地址，下次上架会重推）"""
    ids = [str(i) for i in (body.get("ids") or [])]
    if not ids:
        raise HTTPException(400, "请勾选条目")
    import cloud_release

    conn = db.connect()
    try:
        return cloud_release.retract(conn, ids)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, str(e)[:300])
    finally:
        conn.close()


# ---------- 本地文件 ----------

@app.api_route("/media/audio", methods=["GET", "HEAD"])
def media_audio(aweme_id: str):
    import media
    path = media.audio_path_any(aweme_id).resolve()
    if not path.exists() or config.AUDIO_DIR.resolve() not in path.parents:
        raise HTTPException(404, "音频不存在")
    # Range 交给 Starlette FileResponse；m4a 的 MIME 必须给对，iOS 才肯播
    return FileResponse(path, media_type=media.media_type(path))


@app.get("/media/article")
def media_article(aweme_id: str):
    conn = db.connect()
    v = db.get_video(conn, aweme_id)
    conn.close()
    if not v or not v.get("article_path") or not Path(v["article_path"]).exists():
        raise HTTPException(404, "文章不存在")
    return JSONResponse({"markdown": Path(v["article_path"]).read_text(encoding="utf-8"),
                         "title": v["title"]})


@app.get("/media/article/text", response_class=PlainTextResponse)
def media_article_text(aweme_id: str):
    """前端本地预览用的纯正文：uni.request 按字符串解析，这里不能返 JSON"""
    conn = db.connect()
    v = db.get_video(conn, aweme_id)
    conn.close()
    if not v or not v.get("article_path") or not Path(v["article_path"]).exists():
        raise HTTPException(404, "文章不存在")
    return Path(v["article_path"]).read_text(encoding="utf-8")


# ---------- 前端页面 ----------

@app.get("/", response_class=HTMLResponse)
def index():
    html = HERE / "index.html"
    if not html.exists():
        raise HTTPException(500, f"缺少 {html}")
    return html.read_text(encoding="utf-8")


def main(port: int = 0):
    import uvicorn
    port = port or config.PANEL_PORT
    url = f"http://127.0.0.1:{port}"
    print(f"\n  抖音采集流水线面板  {url}\n  按 Ctrl+C 停止\n")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=config.PANEL_PORT)
    a = ap.parse_args()
    main(a.port)
