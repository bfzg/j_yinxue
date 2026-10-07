"""
存量音频瘦身：把 output/audio 里已有的大文件按 config.AUDIO_PROFILE 原地重编

为什么需要它：抖音给的音频流是双声道，直接落库每集平均 21MB、1023 集约 20GB。
纯语音降到单声道 AAC 40kbps 只要四分之一，而且不用重新下载，本地 ffmpeg 重编即可。

  dry run 先看账： .venv/bin/python compress.py --dry-run
  真跑一遍：       .venv/bin/python compress.py --workers 4
  只转某个栏目：    .venv/bin/python compress.py --column <column_id>
  只转前 20 个：    .venv/bin/python compress.py --limit 20
  换档位：         .venv/bin/python compress.py --profile aac32_mono
  保留原 mp3：      .venv/bin/python compress.py --keep-source

安全边界：
  - 已经 <= 目标码率的文件直接跳过，重复跑没有副作用
  - 先写 output/tmp/compact，校验时长差 <2 秒且体积变小，才替换原文件
  - 已发布到云端的条目默认不动（线上 URL 已经发出去了），要动加 --include-published
  - 档位是 opus/ogg 这类容器时告警跳过：微信小程序播放器不支持，别往库里灌
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import sys
import threading
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
import config  # noqa: E402
import media  # noqa: E402
import pipeline_db as db  # noqa: E402

def _stage_dir() -> Path:
    d = config.OUTPUT_DIR / "tmp" / "compact"
    d.mkdir(parents=True, exist_ok=True)
    return d


_DB_LOCK = threading.Lock()
_TL = threading.local()


def _thread_conn():
    """sqlite 连接不能跨线程用，每个 worker 各开一条（WAL 下读写都安全）"""
    conn = getattr(_TL, "conn", None)
    if conn is None:
        conn = _TL.conn = db.connect()
    return conn


def _update_path(aweme_id: str, path: str):
    """只碰 audio_path，不动 stage/error/updated_at，免得打乱面板排序"""
    with _DB_LOCK:
        conn = _thread_conn()
        conn.execute("UPDATE videos SET audio_path=? WHERE aweme_id=?",
                     (path, aweme_id))
        conn.commit()


def _expected_bytes(dur_ms: int, kbps: int) -> int:
    """按时长和目标码率估体积；40k AAC 实测约为标称的 1.02 倍，留 6% 头"""
    return int(dur_ms / 1000.0 * kbps * 1000 / 8 * 1.06)


def needs_work(path: Path, dur_ms: int, prof: dict) -> Optional[str]:
    """返回为什么要重编的理由，不需要则 None"""
    if path.suffix != "." + prof["ext"]:
        return f"容器不同({path.suffix})"
    if dur_ms <= 0:
        return "缺时长，按容器一致处理"
    cur = path.stat().st_size * 8 / dur_ms              # kbps
    if cur > prof["kbps"] * 1.3:
        return f"当前 {cur:.0f}kbps > 目标 {prof['kbps']}kbps"
    return None


def encode_one(src: Path, prof: dict) -> Optional[Path]:
    """单声道重编，不叠加 loudnorm（入库时已经归一化过，再归一次会抽）"""
    from extractor import AudioExtractor

    ex = AudioExtractor(profile=prof, delete_video=False, loudnorm=False)
    out = ex.extract(src, _stage_dir())
    return Path(out) if out else None


def compact_one(aweme_id: str, prof: dict, keep_source: bool,
                verbose: bool = True) -> dict:
    """一集一条：编 → 校验 → 替换 → 更新库。返回体积账"""
    conn = _thread_conn()
    row = conn.execute("SELECT aweme_id, audio_path, duration_ms, stage, title"
                       " FROM videos WHERE aweme_id=?", (aweme_id,)).fetchone()
    if not row:
        return {"aweme_id": aweme_id, "skipped": "库里没有这条"}
    src = Path(row["audio_path"] or "")
    if not src.exists():
        src = media.audio_path_any(aweme_id)
    if not src.exists():
        return {"aweme_id": aweme_id, "skipped": "本地没有音频"}

    before = src.stat().st_size
    reason = needs_work(src, int(row["duration_ms"] or 0), prof)
    if not reason:
        return {"aweme_id": aweme_id, "skipped": "已达标", "before": before,
                "after": before, "saved": 0}

    dst_stage = _stage_dir() / f"{aweme_id}.{prof['ext']}"
    if dst_stage.exists():
        dst_stage.unlink()
    try:
        produced = encode_one(src, prof)
    except Exception as e:  # noqa: BLE001
        return {"aweme_id": aweme_id, "error": f"重编失败 {type(e).__name__}: {str(e)[:120]}"}
    if not produced:
        return {"aweme_id": aweme_id, "error": "重编无产物"}
    staged = Path(produced)
    if staged != dst_stage:
        staged.replace(dst_stage)

    dur_new = media.probe_duration(dst_stage)
    dur_old = int(row["duration_ms"] or 0) or media.probe_duration(src)
    after = dst_stage.stat().st_size
    if dur_old and abs(dur_new - dur_old) > 2500:
        dst_stage.unlink(missing_ok=True)
        return {"aweme_id": aweme_id,
                "error": f"时长对不上({dur_old / 1000:.0f}s→{dur_new / 1000:.0f}s)，保留原文件"}
    if after >= before:
        dst_stage.unlink(missing_ok=True)
        return {"aweme_id": aweme_id, "skipped": "重编后没变小，保留原文件",
                "before": before, "after": before, "saved": 0}

    # 落到正式文件名 {aweme_id}.{ext}
    final = config.AUDIO_DIR / f"{aweme_id}.{prof['ext']}"
    if final == src:
        # 同容器换档位：先给原文件留个 .keep 备份，再原地换成小的
        if keep_source:
            src.with_name(f"{aweme_id}.keep{src.suffix}").write_bytes(src.read_bytes())
        dst_stage.replace(final)
    else:
        if final.exists():
            final.unlink()
        dst_stage.replace(final)
        if not keep_source:
            src.unlink(missing_ok=True)

    _update_path(aweme_id, str(final))
    if verbose:
        print(f"  [瘦身] {aweme_id} {before / 1048576:.1f}MB → "
              f"{after / 1048576:.1f}MB  ({reason})", flush=True)
    return {"aweme_id": aweme_id, "before": before, "after": after,
            "saved": before - after, "reason": reason}


def _targets(conn, ids=None, limit: int = 0, column: str = "",
             include_published: bool = False) -> list[str]:
    if ids:
        return [str(i) for i in ids]
    where, params = "audio_path IS NOT NULL AND audio_path != ''", []
    if column:
        where += " AND column_id=?"
        params = [column]
    sql = f"SELECT aweme_id FROM videos WHERE {where}"
    if not include_published:
        sql += " AND stage != 'published'"
    sql += " ORDER BY episode_no ASC, aweme_id ASC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [r["aweme_id"] for r in conn.execute(sql, params)]


def plan(conn, prof: dict, **filter_kwargs) -> dict:
    """只读预估：现在多大、按档位转完多大、能省多少"""
    todo, total_before, est_after = [], 0, 0
    skipped_pub = 0
    for aweme_id in _targets(conn, **filter_kwargs):
        row = conn.execute("SELECT audio_path, duration_ms, stage FROM videos"
                           " WHERE aweme_id=?", (aweme_id,)).fetchone()
        src = Path(row["audio_path"] or "")
        if not src.exists():
            src = media.audio_path_any(aweme_id)
        if not src.exists():
            continue
        size = src.stat().st_size
        dur = int(row["duration_ms"] or 0) or media.probe_duration(src)
        if needs_work(src, dur, prof):
            todo.append(aweme_id)
            total_before += size
            est_after += _expected_bytes(dur, prof["kbps"])
        else:
            total_before += size
            est_after += size
    return {"profile": prof["name"], "ext": prof["ext"], "kbps": prof["kbps"],
            "count": len(todo), "scanned_total": total_before,
            "est_total_after": est_after, "est_saved": total_before - est_after,
            "avg_before": int(total_before / len(todo)) if todo else 0,
            "avg_after": int(est_after / len(todo)) if todo else 0,
            "ids": todo}


def run(profile: str = "", limit: int = 0, ids: Optional[list[str]] = None,
        column: str = "", keep_source: bool = False, workers: int = 4,
        include_published: bool = False, verbose: bool = True) -> dict:
    prof = config.audio_profile(profile)
    if prof["ext"] in ("ogg", "opus", "weba"):
        raise RuntimeError(f"档位 {prof['name']} 是 {prof['ext']} 容器，"
                           "微信小程序播放器不支持，别写进库")
    conn = db.connect()
    t0 = time.time()
    ok, fail, stats = 0, [], []
    try:
        targets = _targets(conn, ids=ids, limit=limit, column=column,
                           include_published=include_published)
        if verbose:
            print(f"  [瘦身] 档位 {prof['name']} {prof['kbps']}kbps → 待处理 "
                  f"{len(targets)} 集，保留原文件={keep_source}")
        # ffmpeg 自己吃满多核，并发压到 4 路就够，再高只是抢内存
        with cf.ThreadPoolExecutor(max_workers=max(1, min(workers, 6))) as pool:
            futs = {pool.submit(compact_one, aid, prof, keep_source, verbose): aid
                    for aid in targets}
            for fut in cf.as_completed(futs):
                try:
                    r = fut.result()
                except Exception as e:  # noqa: BLE001
                    fail.append(f"{futs[fut]}: {type(e).__name__}: {str(e)[:100]}")
                    continue
                if r.get("error"):
                    fail.append(f"{r['aweme_id']}: {r['error']}")
                elif r.get("saved"):
                    ok += 1
                    stats.append(r)
                if verbose and (ok % 25 == 0) and stats:
                    saved = sum(x["saved"] for x in stats)
                    print(f"  [进度] 已转 {ok} 集，累计省 {saved / 1073741824:.2f}GB",
                          flush=True)
        saved = sum(x["saved"] for x in stats)
        before = sum(x["before"] for x in stats)
        after = sum(x["after"] for x in stats)
        out = {"profile": prof["name"], "converted": ok, "failed": fail[:20],
               "fail_count": len(fail), "before_gb": round(before / 1073741824, 3),
               "after_gb": round(after / 1073741824, 3),
               "saved_gb": round(saved / 1073741824, 3),
               "avg_before_mb": round(before / ok / 1048576, 2) if ok else 0,
               "avg_after_mb": round(after / ok / 1048576, 2) if ok else 0,
               "seconds": round(time.time() - t0)}
        if verbose or fail:
            print("\n  ===== 瘦身结果 =====")
            print(f"  转换 {out['converted']} 集，失败 {out['fail_count']}")
            print(f"  {out['before_gb']} GB → {out['after_gb']} GB，"
                  f"省 {out['saved_gb']} GB")
            print(f"  平均每集 {out['avg_before_mb']} MB → {out['avg_after_mb']} MB")
            print(f"  耗时 {out['seconds']} 秒")
            for msg in out["failed"]:
                print(f"    ! {msg}")
        return out
    finally:
        conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="存量音频按档位重编瘦身")
    ap.add_argument("--profile", default="", help="档位名，默认用当前配置")
    ap.add_argument("--dry-run", action="store_true", help="只算账不动文件")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--ids", nargs="*", default=[])
    ap.add_argument("--column", default="", help="只处理某个 column_id")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--keep-source", action="store_true", help="保留原来的大文件")
    ap.add_argument("--include-published", action="store_true",
                    help="连已发布的条目一起转（URL 会变，需重新推云端）")
    a = ap.parse_args()
    config.ensure_dirs()
    conn = db.connect()
    try:
        prof = config.audio_profile(a.profile)
        if a.dry_run:
            pr = plan(conn, prof, ids=a.ids, limit=a.limit, column=a.column,
                      include_published=a.include_published)
            print(f"  档位 {pr['profile']}({pr['kbps']}kbps, .{pr['ext']})")
            print(f"  待重编 {pr['count']} 集")
            gb = 1073741824
            print(f"  全部音频 {pr['scanned_total'] / gb:.2f} GB → "
                  f"预计 {pr['est_total_after'] / gb:.2f} GB，"
                  f"省 {pr['est_saved'] / gb:.2f} GB")
            if pr["count"]:
                print(f"  待转部分平均 {pr['avg_before'] / 1048576:.1f} MB/集 → "
                      f"约 {pr['avg_after'] / 1048576:.1f} MB/集")
        else:
            run(profile=a.profile, limit=a.limit, ids=a.ids, column=a.column,
                keep_source=a.keep_source, workers=a.workers,
                include_published=a.include_published)
    finally:
        conn.close()
