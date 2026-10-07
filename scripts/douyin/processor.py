"""
按集推进的处理引擎

一条作品的完整链路：scanned → audio → transcript → article →（人工点发布）published。
每一步都按 aweme_id 幂等，中断后重跑会自动跳过已完成的部分，失败只标这一条，
不影响队列里其他作品。
"""
from __future__ import annotations

import json
import sys
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
import config
import pipeline_db as db

STEP_ORDER = ["audio", "transcript", "article"]
RANK = {"failed": -1, "skipped": 0, "scanned": 0, "audio": 1,
        "transcript": 2, "article": 3, "published": 4}


@dataclass
class Progress:
    running: bool = False
    total: int = 0
    done: int = 0
    ok: int = 0
    failed: int = 0
    current: str = ""
    step: str = ""
    started_at: str = ""
    elapsed: int = 0
    note: str = ""
    cancel_requested: bool = False
    errors: list = field(default_factory=list)

    def snapshot(self) -> dict:
        d = asdict(self)
        d["errors"] = self.errors[-20:]
        return d


CURRENT = Progress()
_LOCK = threading.Lock()


def stop():
    """面板点「停止」时调用：当前这一集做完后退出"""
    with _LOCK:
        CURRENT.cancel_requested = True
        CURRENT.note = "收到停止请求，正在收尾……"


def _reset(total: int, note: str = ""):
    with _LOCK:
        CURRENT.running = True
        CURRENT.total = total
        CURRENT.done = 0
        CURRENT.ok = 0
        CURRENT.failed = 0
        CURRENT.current = ""
        CURRENT.step = ""
        CURRENT.started_at = db.now()
        CURRENT.elapsed = 0
        CURRENT.note = note
        CURRENT.cancel_requested = False
        CURRENT.errors = []


def _finish(note: str = ""):
    with _LOCK:
        CURRENT.running = False
        CURRENT.current = ""
        CURRENT.step = ""
        CURRENT.elapsed = int(time.time() - _t0()) if _t0() else 0
        if note:
            CURRENT.note = note


_T0 = [0.0]


def _t0():
    return _T0[0]


def stage_reached(stage: Optional[str], step: str) -> bool:
    """这条作品是否已经完成某个阶段"""
    return RANK.get(stage or "scanned", 0) >= RANK[step]


def select_ids(conn, sec_user_id: str = None, column_id: str = None,
               stage: str = None, ids: list = None, limit: int = 0,
               newest: int = 0, include_done: bool = False,
               only_pending: bool = True) -> list[str]:
    """
    选集：
      ids 显式勾选 > column_id 整个栏目 > sec_user_id 整个账号
    stage 精确匹配某个阶段；only_pending 默认排除已到 article/published 的
    """
    if ids:
        return [str(i) for i in ids]

    where, params = ["kind != 'image_text'"], []
    if sec_user_id:
        where.append("sec_user_id=?")
        params.append(sec_user_id)
    if column_id:
        where.append("column_id=?")
        params.append(column_id)
    if stage:
        where.append("stage=?")
        params.append(stage)
    elif only_pending and not include_done:
        where.append("stage NOT IN ('article','published','skipped')")

    sql = "SELECT aweme_id FROM videos WHERE " + " AND ".join(where)
    sql += " ORDER BY column_id, episode_no, create_time DESC"
    cap = limit or (newest or 0)
    if cap:
        sql += f" LIMIT {int(cap)}"
    return [r["aweme_id"] for r in conn.execute(sql, tuple(params)).fetchall()]


def process_one(conn, aweme_id: str, steps: list = None, force: bool = False,
                verbose: bool = True) -> dict:
    """处理单条作品。返回每一步的结果摘要，任何一步失败即中止这条"""
    import asr
    import article_formatter as af
    import inventory as inv
    import media

    steps = steps or STEP_ORDER
    v = db.get_video(conn, aweme_id)
    if not v:
        raise ValueError(f"数据库里没有这条作品: {aweme_id}")
    out: dict = {"aweme_id": aweme_id, "title": v["title"], "steps": {}}

    for step in steps:
        if not force and stage_reached(v["stage"], step):
            continue
        try:
            if step == "audio":
                path = media.ensure_audio(aweme_id, force=force, verbose=verbose)
                ms = v.get("duration_ms") or media.probe_duration(path)
                db.set_stage(conn, aweme_id, "audio",
                             audio_path=str(path), duration_ms=ms)
                out["steps"]["audio"] = {"path": str(path), "size_kb":
                                         round(path.stat().st_size / 1024)}
            elif step == "transcript":
                audio = (Path(v["audio_path"]) if v.get("audio_path")
                                  and Path(v["audio_path"]).exists()
                         else media.audio_path_any(aweme_id))
                res = asr.ensure_transcript(aweme_id, audio, force=force)
                db.set_stage(conn, aweme_id, "transcript",
                             transcript_path=res["text_path"],
                             asr_seconds=res.get("seconds", 0))
                out["steps"]["transcript"] = {k: res[k] for k in
                                             ("backend", "n_sentences", "seconds")}
            elif step == "article":
                v = db.get_video(conn, aweme_id)
                _acc = conn.execute(
                    "SELECT style FROM accounts WHERE sec_user_id=?",
                    (v["sec_user_id"],)).fetchone()
                v["style"] = (_acc["style"] if _acc else "") or ""
                if v.get("column_id"):
                    row = conn.execute("SELECT name FROM columns WHERE column_id=?",
                                       (v["column_id"],)).fetchone()
                    v["column_name"] = row["name"] if row else ""
                res = af.build_article(aweme_id, v, force=force, verbose=verbose)
                db.set_stage(conn, aweme_id, "article",
                             article_path=res["path"], error="")
                out["steps"]["article"] = res["meta"]
            v = db.get_video(conn, aweme_id)
        except inv.CookieInvalid:
            db.set_stage(conn, aweme_id, v["stage"], error="Cookie 失效")
            raise
        except Exception as e:  # noqa: BLE001
            msg = f"{type(e).__name__}: {str(e)[:280]}"
            db.set_stage(conn, aweme_id, "failed", error=f"{step}: {msg}")
            db.log_event(conn, "error", "process", aweme_id, f"{step} 失败 {msg}")
            raise RuntimeError(f"{step} 失败: {msg}") from e

    final = db.get_video(conn, aweme_id)
    out["stage"] = final["stage"]
    if final["stage"] != "failed":
        db.set_stage(conn, final["aweme_id"], final["stage"], error="")
    return out


def run(steps: list = None, sec_user_id: str = None, column_id: str = None,
        ids: list = None, limit: int = 0, newest: int = 0, stage: str = None,
        force: bool = False, delay: Optional[float] = None, verbose: bool = True,
        conn=None) -> dict:
    """批量处理。面板后台线程与 CLI 都走这里"""
    import inventory as inv

    own = conn is None
    conn = conn or db.connect()
    targets = select_ids(conn, sec_user_id=sec_user_id, column_id=column_id,
                        stage=stage, ids=ids, limit=limit, newest=newest)
    if not targets:
        _reset(0, "没有符合条件的作品")
        _finish("没有符合条件的作品")
        if own:
            conn.close()
        return {"total": 0, "ok": 0, "failed": 0}

    steps = steps or STEP_ORDER
    delay = config.DOWNLOAD_DELAY if delay is None else delay
    _reset(len(targets), f"{len(targets)} 集 × {len(steps)} 步")
    _T0[0] = time.time()
    db.log_event(conn, "info", "process", "-",
                 f"开始处理 {len(targets)} 集 steps={steps}")

    try:
        for i, aweme_id in enumerate(targets, start=1):
            with _LOCK:
                if CURRENT.cancel_requested:
                    CURRENT.note = f"已停止（完成 {CURRENT.done} / {len(targets)}）"
                    break
                CURRENT.current = aweme_id
                CURRENT.step = "/".join(steps)
                # 面板轮询要看实时耗时，只在收尾算一次的话跑起来一直是 0s
                CURRENT.elapsed = int(time.time() - _T0[0])
            try:
                res = process_one(conn, aweme_id, steps, force=force,
                                  verbose=verbose)
                with _LOCK:
                    CURRENT.ok += 1
                    CURRENT.done += 1
                if verbose:
                    print(f"[{i}/{len(targets)}] {aweme_id} → {res['stage']}")
            except inv.CookieInvalid as e:
                with _LOCK:
                    CURRENT.note = f"Cookie 失效，已暂停：{str(e)[:120]}"
                    CURRENT.failed += 1
                    CURRENT.done += 1
                db.log_event(conn, "error", "process", aweme_id, str(e))
                break
            except Exception as e:  # noqa: BLE001
                with _LOCK:
                    CURRENT.failed += 1
                    CURRENT.done += 1
                    CURRENT.errors.append(f"{aweme_id}: {str(e)[:200]}")
                if verbose:
                    print(f"[{i}/{len(targets)}] {aweme_id} 失败: {str(e)[:160]}")

            if i < len(targets) and not CURRENT.cancel_requested:
                time.sleep(delay)
    finally:
        with _LOCK:
            note = (CURRENT.note if CURRENT.cancel_requested or CURRENT.note
                    else f"完成 {CURRENT.ok} 集，失败 {CURRENT.failed} 集")
            CURRENT.elapsed = int(time.time() - _T0[0])
            CURRENT.running = False
            CURRENT.current = ""
        db.log_event(conn, "info", "process", "-",
                     f"结束：成功 {CURRENT.ok} / 失败 {CURRENT.failed}")
        if own:
            conn.close()

    return {"total": len(targets), "ok": CURRENT.ok, "failed": CURRENT.failed,
            "note": CURRENT.note, "llm": _llm_summary()}


def _llm_summary() -> str:
    try:
        import llm
        return llm.summary()
    except Exception:
        return ""


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="批量处理作品")
    ap.add_argument("--sec", help="账号 sec_user_id")
    ap.add_argument("--column", help="栏目 column_id")
    ap.add_argument("--ids", nargs="*", default=[], help="指定 aweme_id")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--steps", default="audio,transcript,article")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--delay", type=float, default=None)
    args = ap.parse_args()
    config.ensure_dirs()
    print(json.dumps(run(steps=args.steps.split(","), sec_user_id=args.sec,
                         column_id=args.column, ids=args.ids, limit=args.limit,
                         force=args.force, delay=args.delay),
                     ensure_ascii=False, indent=2))
