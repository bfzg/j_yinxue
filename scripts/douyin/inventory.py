"""
作品盘点：把抖音账号主页的元数据（标题/合集/章节/时长）抓进 sqlite

只抓元数据，不下载媒体，所以快且便宜。列表页给出的播放直链有时效，
下载时必须用 resolve_media() 重新取一次。
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).parent))

import config
import pipeline_db as db


def _run_coro(coro):
    """面板的采集线程可能已经有事件循环在跑，这里 asyncio.run 会直接抛错，
    协程连创建都没创建就变成‘never awaited’，直链就只能退回浏览器。
    检测到活动循环就换一条干净线程跑，两条路都能走通。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import threading

    box: dict = {}

    def _worker():
        try:
            box["value"] = asyncio.run(coro)
        except BaseException as e:  # noqa: BLE001
            box["error"] = e

    th = threading.Thread(target=_worker, daemon=True, name="f2-loop")
    th.start()
    th.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")


class CookieInvalid(RuntimeError):
    """Cookie 失效 / 触发风控，需要人工换 Cookie"""


_HASHTAG_RE = re.compile(r"#\S+")
_ELLIPSIS_RE = re.compile(r"[\s\u3000]*(?:\.{3}|…+|。。。+)\s*$")


def _f2_kwargs(sec_user_id: str) -> dict:
    """构造 f2 DouyinHandler 需要的参数字典（已实测可用）"""
    import f2
    from f2.utils.conf_manager import ConfigManager
    from f2.utils.utils import merge_config
    from f2.apps.douyin.utils import ClientConfManager

    main_conf = ConfigManager(f2.APP_CONFIG_FILE_PATH).get_config("douyin")
    kw = merge_config(
        main_conf,
        {"headers": {}},
        **{
            "cookie": config.get_cookie(),
            "url": f"https://www.douyin.com/user/{sec_user_id}",
            "mode": "post",
        },
    )
    kw["proxies"] = ClientConfManager.proxies()
    # f2 里 timeout 同时是 HTTP 超时和翻页 sleep，取两者较大值做限流
    kw["timeout"] = max(5, int(config.PAGE_DELAY))
    kw.setdefault("headers", {})
    kw["headers"]["User-Agent"] = ClientConfManager.user_agent()
    kw["headers"]["Referer"] = ClientConfManager.referer()
    kw["app_name"] = "douyin"
    return kw


def _handler(sec_user_id: str):
    from f2.apps.douyin.handler import DouyinHandler
    handler = DouyinHandler(_f2_kwargs(sec_user_id))
    # f2 默认把每个异常都往 api.day.app 推一条 Bark，我们没配 key，
    # 只会在日志里刷一屏 405 红字，直接关掉
    if config.QUIET_F2_NOTIFY:
        handler.enable_bark = False
    return handler


def clean_title(desc: str) -> str:
    """去掉话题标签、尾巴省略号，取首行，限 80 字"""
    text = (desc or "").strip()
    if not text:
        return ""
    first = text.splitlines()[0].strip() if "\n" in text[:200] else text
    line = (first or text).strip()
    line = _HASHTAG_RE.sub(" ", line)
    line = re.sub(r"\s+", " ", line).strip()
    line = _ELLIPSIS_RE.sub("", line).strip()
    if len(line) > 80:
        line = line[:80].rstrip() + "…"
    return line


def _first_url(raw: Any) -> Optional[str]:
    """url_list 是 list，个别接口给的是 dict(main_url/backup_url)"""
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw.get("url_list", [None])[0] if raw.get("url_list") else (
            raw.get("main_url") or raw.get("backup_url"))
    if isinstance(raw, (list, tuple)):
        return raw[0] if raw else None
    return str(raw)


def pick_media_urls(aweme: dict) -> dict:
    """从一条 aweme 里挑出最好的下载地址：优先纯音频流，其次最低码率 mp4"""
    video = aweme.get("video") or {}
    out: dict[str, Any] = {"audio_urls": [], "video_urls": [], "duration_ms":
                           video.get("duration") or 0}

    if config.PREFER_AUDIO_STREAM:
        for br in video.get("bit_rate_audio") or []:
            meta = br.get("audio_meta") or {}
            url = _first_url(meta.get("url_list") or meta)
            if url:
                out["audio_urls"].append(url)

    audio_only = []
    for br in sorted(video.get("bit_rate") or [], key=lambda b: b.get("bit_rate", 1 << 30)):
        if br.get("format") not in (None, "mp4"):
            continue
        url = _first_url((br.get("play_addr") or {}).get("url_list"))
        if url and url not in audio_only:
            audio_only.append(url)
    out["video_urls"] = audio_only

    if not out["video_urls"]:
        out["video_urls"] = [_first_url((video.get("play_addr") or {}).get("url_list"))]
        out["video_urls"] = [u for u in out["video_urls"] if u]
    return out


# ---------- aweme -> 数据库记录 ----------

# 2=图文 61/68=图文合集，这类没有音频可转写，只留文字
_IMAGE_TEXT_TYPES = {2, 61, 68}


def _mix_episode(mix: dict) -> tuple[Optional[int], Optional[int]]:
    """合集里的第几集 / 共几集。f2 不同版本字段位置不一样，逐个兜底"""
    statis = mix.get("statis") or {}
    ep = (mix.get("current_episode") or statis.get("current_episode")
          or mix.get("episode_number"))
    total = (mix.get("episode_total_count") or statis.get("episode_count")
             or mix.get("updated_to_episode") or statis.get("updated_to_episode"))
    try:
        ep = int(ep) if ep is not None else None
    except (TypeError, ValueError):
        ep = None
    try:
        total = int(total) if total is not None else None
    except (TypeError, ValueError):
        total = None
    return ep, total


def parse_aweme(aweme: dict, sec_user_id: str) -> dict:
    """把一条 aweme 拆成盘点记录（含合集、官方章节、统计）"""
    aweme_id = str(aweme.get("aweme_id") or "")
    desc = aweme.get("desc") or ""
    video = aweme.get("video") or {}
    mix = aweme.get("mix_info") or {}
    stat = aweme.get("statistics") or {}
    chapters = []
    for ch in aweme.get("chapter_list") or []:
        chapters.append({
            "title": ch.get("desc") or "",
            "timestamp": ch.get("timestamp") or 0,
            "detail": ch.get("detail_title") or ch.get("detail") or "",
        })

    ep_no, ep_total = _mix_episode(mix)
    aweme_type = aweme.get("aweme_type") or 0

    return {
        "aweme_id": aweme_id,
        "sec_user_id": sec_user_id,
        "title": clean_title(desc) or f"未命名作品 {aweme_id[-6:]}",
        "desc_raw": desc,
        "create_time": aweme.get("create_time") or 0,
        "duration_ms": video.get("duration") or 0,
        "mix_id": str(mix.get("mix_id")) if mix.get("mix_id") else None,
        "mix_name": mix.get("mix_name") or None,
        "ep_no": ep_no,
        "ep_total": ep_total,
        "chapters": chapters,
        "kind": "image_text" if aweme_type in _IMAGE_TEXT_TYPES else "video",
        "digg_count": int(stat.get("digg_count") or 0),
        "comment_count": int(stat.get("comment_count") or 0),
        "share_count": int(stat.get("share_count") or 0),
        "cover_url": _first_url((video.get("cover") or {}).get("url_list")),
        "source_url": f"https://www.douyin.com/video/{aweme_id}",
    }


def _is_cookie_error(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    keys = ("cookie", "登录", "扫码", "verify", "验证", "风控", "restrict",
            "403", "401", "blocked", "滑块")
    return any(k in text for k in keys)


# ---------- 扫描 ----------

def remaining_ok(target: int, scanned: int) -> bool:
    """还没取满上限时值得再试一次"""
    return not target or scanned < target


async def _scan_async(sec_user_id: str, max_items: int, conn,
                      on_progress=None) -> dict:
    """翻主页作品列表，逐条落库。返回 {scanned, total_profile, mixes, nickname}"""
    handler = _handler(sec_user_id)
    result = {"scanned": 0, "new": 0, "truncated": False, "nickname": "", "aweme_count": 0,
              "mix_count": 0, "follower_count": 0, "mixes": {}}

    try:
        prof = await handler.fetch_user_profile(sec_user_id)
        user = (prof._data or {}).get("user") or {}
        result["nickname"] = user.get("nickname") or ""
        result["aweme_count"] = int(user.get("aweme_count") or 0)
        result["mix_count"] = int(user.get("mix_count") or 0)
        result["follower_count"] = int(user.get("follower_count") or 0)
        conn.execute(
            """UPDATE accounts SET nickname=?, aweme_count=?, mix_count=?,
                      follower_count=? WHERE sec_user_id=?""",
            (result["nickname"], result["aweme_count"], result["mix_count"],
             result["follower_count"], sec_user_id),
        )
        conn.commit()
    except Exception as e:
        if _is_cookie_error(e):
            raise CookieInvalid(f"获取账号信息失败，Cookie 可能已失效: {e}") from e
        print(f"  [warn] profile 失败（继续扫作品）: {type(e).__name__}: {e}")

    seen: set[str] = set()
    target = max_items if max_items > 0 else (result["aweme_count"] or 0)
    cursor = 0
    retry = 0

    while True:
        finished = True
        last_cursor = cursor
        remaining = (target - result["scanned"]) if target else None
        async for flt in handler.fetch_user_post_videos(
                sec_user_id, max_cursor=cursor, page_counts=20,
                max_counts=remaining if remaining and remaining > 0 else None):
            data = getattr(flt, "_data", None) or {}
            items = data.get("aweme_list") or []
            ids = [str(i["aweme_id"]) for i in items if i.get("aweme_id")]
            exists = set()
            if ids:
                placeholders = ",".join("?" * len(ids))
                rows = conn.execute(
                    f"SELECT aweme_id FROM videos WHERE aweme_id IN ({placeholders})",
                    tuple(ids)).fetchall()
                exists = {r["aweme_id"] for r in rows}

            for aweme in items:
                rec = parse_aweme(aweme, sec_user_id)
                if not rec["aweme_id"] or rec["aweme_id"] in seen:
                    continue
                seen.add(rec["aweme_id"])
                if rec["aweme_id"] not in exists:
                    result["new"] += 1
                if rec["mix_name"]:
                    result["mixes"][rec["mix_name"]] = result["mixes"].get(
                        rec["mix_name"], 0) + 1
                db.upsert_video(conn, rec)
                result["scanned"] += 1
                if target and result["scanned"] >= target:
                    break

            last_cursor = int(data.get("max_cursor") or last_cursor)
            conn.execute(
                "UPDATE accounts SET last_cursor=? WHERE sec_user_id=?",
                (last_cursor, sec_user_id))
            conn.commit()
            if on_progress:
                on_progress(result["scanned"], target or 0, result["new"])

            if target and result["scanned"] >= target:
                finished = True
                break
            if not data.get("has_more"):
                finished = True
                break
            if not items:
                # 空页但 has_more=1，多半是接口抖动或风控，带原 cursor 重试
                finished = False
                break

        if finished or not remaining_ok(target, result["scanned"]):
            cursor = last_cursor
            break
        cursor = last_cursor
        retry += 1
        if retry > 3:
            result["truncated"] = True
            db.log_event(conn, "warn", "scan", sec_user_id,
                         f"翻页在 cursor={cursor} 处连续 4 次拿到空页，已停止")
            break
        await asyncio.sleep(4 * retry)

    return result


REASON_TEXT = {
    "": "",
    "not_logged_in": "未登录，只拿到第一页",
    "degraded_login": "登录态被风控降级，本次走匿名，只够翻到首页",
    "timeout": "超时自动收工",
    "stopped": "已手动停止",
    "scroll_error": "页面滚不动，可能被验证码拦住",
    "stopped_early": "提前收工",
    "profile_busy": "Chrome 配置被占用",
}


def scan_note(res: dict, limit: int = 0) -> str:
    """把扫描结果写成一句人话，绝不在面板上假装「已经抓全了」"""
    scanned = int(res.get("scanned") or 0)
    expect = int(res.get("aweme_count") or 0)
    reason = res.get("reason") or ""
    parts = [f"已扫 {scanned}"]
    if expect:
        parts.append(f"主页 {expect}")
    if limit:
        parts.append(f"上限 {limit}")
    note = " / ".join(parts)
    tail = REASON_TEXT.get(reason, reason)
    if tail:
        note += f"（{tail}）"
    elif scanned == 0:
        note += "（一条都没抓到，先检查登录态）"
    elif limit and scanned >= limit:
        note += "（已达上限）"
    elif expect and scanned >= expect:
        note += "（已全部抓取）"
    elif res.get("truncated"):
        note += "（未取满，建议重扫一次）"
    return note


def _scan_via_browser(sec_user_id: str, limit: int, headless=None,
                      verbose: bool = True) -> dict:
    import browser_scan
    res = browser_scan.scan(sec_user_id, max_items=limit or 0, headless=headless,
                            verbose=verbose,
                            idle_limit=int(getattr(config, "SCAN_IDLE_LIMIT", 12)),
                            deadline_sec=int(getattr(config, "SCAN_DEADLINE_MIN", 40)) * 60)
    res["engine"] = "browser"
    return res


def _scan_via_api(sec_user_id: str, limit: int, verbose: bool = True) -> dict:
    conn = db.connect()
    try:
        res = _run_coro(_scan_async(sec_user_id, limit or 0, conn,
                                      on_progress=lambda n, t, new: verbose
                                      and print(f"  … {n}/{t} 条（新增 {new}）", flush=True)))
        res.setdefault("reason", "")
        if res.get("truncated") and not res["reason"]:
            res["reason"] = "stopped_early"
        res["engine"] = "api"
        return res
    finally:
        conn.close()


def scan_account(sec_user_id: str, max_items: Optional[int] = None,
                 verbose: bool = True, engine: Optional[str] = None,
                 headless=None) -> dict:
    """
    扫描单个账号（面板与 CLI 共用入口）

    engine: browser（默认，本机 Chrome 真滚动）/ api（f2 接口）/
            auto（先 browser，浏览器起不来再退回 api）
    """
    import accounts as accmod
    engine = (engine or config.SCAN_ENGINE or "browser").lower()
    allow_fallback = engine == "auto"
    engine = "api" if engine not in ("browser", "api") else engine

    conn = db.connect()
    accmod.sync_to_db(conn)
    acc = next((a for a in db.list_accounts(conn)
                if a["sec_user_id"] == sec_user_id), None)
    if acc is None:
        conn.close()
        raise ValueError(f"账号不存在，请先添加: {sec_user_id}")
    limit = int(acc.get("max_items", 0) if max_items is None else max_items)
    db.log_event(conn, "info", "scan", sec_user_id,
                 f"开始扫描 limit={limit} engine={engine}")

    def _record(res: dict) -> dict:
        res.setdefault("new", 0)
        note = scan_note(res, limit)
        conn.execute(
            """UPDATE accounts SET scanned_items=?, last_scan_at=?, scan_note=?
               WHERE sec_user_id=?""",
            (res.get("scanned", 0), db.now(), note, sec_user_id),
        )
        conn.commit()
        level = ("warn" if res.get("reason") in ("not_logged_in", "timeout",
                                                 "scroll_error") else "info")
        db.log_event(conn, level, "scan", sec_user_id,
                     f"{note} | 新增 {res.get('new', 0)}")
        res["scan_note"] = note
        if verbose:
            print(f"  => {note}", flush=True)
        return res

    try:
        if engine == "browser":
            try:
                return _record(_scan_via_browser(sec_user_id, limit,
                                                 headless=headless, verbose=verbose))
            except (ImportError, RuntimeError) as e:
                if not allow_fallback:
                    raise
                print(f"  [warn] 浏览器引擎不可用（{str(e)[:120]}），退回 f2 接口")
                return _record(_scan_via_api(sec_user_id, limit, verbose))
        return _record(_scan_via_api(sec_user_id, limit, verbose))
    except CookieInvalid as e:
        conn.execute("UPDATE accounts SET scan_note=? WHERE sec_user_id=?",
                     (f"Cookie 失效：{e}"[:200], sec_user_id))
        conn.commit()
        db.log_event(conn, "error", "scan", sec_user_id, str(e))
        raise
    finally:
        conn.close()


def scan_all(only_enabled: bool = True, verbose: bool = True,
             engine: Optional[str] = None, headless=None,
             stop_on_cookie_error: bool = True) -> list[dict]:
    out = []
    for acc in accounts_rows(only_enabled):
        if verbose:
            print(f"\n[{acc['name']}] 扫描 {acc['sec_user_id'][:24]}…")
        try:
            res = scan_account(acc["sec_user_id"], verbose=verbose,
                               engine=engine, headless=headless)
        except CookieInvalid as e:
            print(f"  [!] {e}\n  请在面板里粘贴新的 Cookie 后重试。")
            if stop_on_cookie_error:
                raise
            continue
        if verbose:
            print(f"  -> 本次 {res['scanned']} 条（新增 {res['new']}），"
                  f"合集 {len(res['mixes'])} 个")
        out.append({"sec_user_id": acc["sec_user_id"], "name": acc["name"], **res})
    return out


def accounts_rows(only_enabled: bool = True) -> list[dict]:
    import accounts as accmod
    conn = db.connect()
    accmod.sync_to_db(conn)
    rows = db.list_accounts(conn)
    conn.close()
    if only_enabled:
        rows = [r for r in rows if r.get("enabled", 1)]
    return rows


# ---------- 直链重取 / Cookie 体检 ----------

def _unwrap_one(res) -> Optional[dict]:
    """fetch_one_video 的返回形态在 f2 各版本里不统一，逐个兜底"""
    data = getattr(res, "_data", res)
    if isinstance(data, dict):
        # 单作品详情接口现在把结果裹在 aweme_detail 里
        detail = data.get("aweme_detail")
        if isinstance(detail, dict) and detail:
            return detail
        if "aweme_list" in data:
            lst = data.get("aweme_list") or []
            return lst[0] if lst else None
        return data if data.get("aweme_id") else None
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("aweme_id"):
                return item
    return None


async def _resolve_async(aweme_id: str) -> dict:
    handler = _handler("")
    res = await handler.fetch_one_video(aweme_id)
    aweme = _unwrap_one(res)
    if not aweme:
        raise RuntimeError(f"直链重取失败: {aweme_id}")
    out = pick_media_urls(aweme)
    out["aweme"] = aweme
    return out


def resolve_media(aweme_id: str) -> dict:
    """下载前重新获取直链（列表页给出的链接几分钟后就会 403）"""
    return _run_coro(_resolve_async(aweme_id))


async def _check_async(sec_user_id: str) -> dict:
    handler = _handler(sec_user_id)
    out = {"ok": False, "nickname": "", "aweme_count": 0, "message": ""}
    try:
        prof = await handler.fetch_user_profile(sec_user_id)
        user = (prof._data or {}).get("user") or {}
        out["nickname"] = user.get("nickname") or ""
        out["aweme_count"] = int(user.get("aweme_count") or 0)
        out["ok"] = bool(user)
        out["message"] = "Cookie 可用" if out["ok"] else "接口返回空，可能被风控"
    except Exception as e:
        out["message"] = f"{type(e).__name__}: {e}"[:200]
        if _is_cookie_error(e):
            out["message"] = "Cookie 失效或被风控：" + out["message"]
    return out


def check_cookie(sec_user_id: Optional[str] = None) -> dict:
    """用第一个启用账号做 Cookie 体检，面板显示绿灯/红灯"""
    if not sec_user_id:
        rows = accounts_rows(True)
        if not rows:
            return {"ok": False, "message": "还没有启用中的账号"}
        sec_user_id = rows[0]["sec_user_id"]
    return _run_coro(_check_async(sec_user_id))


async def _probe_async(sec_user_id: str) -> dict:
    handler = _handler(sec_user_id)
    out = {"ok": False, "nickname": "", "aweme_count": 0, "mix_count": 0,
           "follower_count": 0, "mixes": [], "samples": [], "message": ""}
    try:
        prof = await handler.fetch_user_profile(sec_user_id)
        user = (prof._data or {}).get("user") or {}
        out.update(
            ok=bool(user),
            nickname=user.get("nickname") or "",
            aweme_count=int(user.get("aweme_count") or 0),
            mix_count=int(user.get("mix_count") or 0),
            follower_count=int(user.get("follower_count") or 0),
            message="账号可访问" if user else "接口返回空，可能被风控",
        )
    except Exception as e:
        out["message"] = f"{type(e).__name__}: {e}"[:200]
        return out

    mixes: dict[str, int] = {}
    try:
        async for flt in handler.fetch_user_post_videos(
                sec_user_id, page_counts=1, max_counts=35):
            data = getattr(flt, "_data", None) or {}
            for aweme in data.get("aweme_list") or []:
                rec = parse_aweme(aweme, sec_user_id)
                if rec["mix_name"]:
                    mixes[rec["mix_name"]] = mixes.get(rec["mix_name"], 0) + 1
                if len(out["samples"]) < 12:
                    out["samples"].append({
                        "aweme_id": rec["aweme_id"],
                        "title": rec["title"],
                        "ep_no": rec["ep_no"],
                        "seconds": round((rec["duration_ms"] or 0) / 1000),
                        "kind": rec["kind"],
                    })
            break
    except Exception as e:
        out["message"] += f"；作品首页获取失败 {type(e).__name__}"
    out["mixes"] = [{"name": k, "n": v} for k, v in
                    sorted(mixes.items(), key=lambda kv: -kv[1])]
    return out


def probe_account(raw: str) -> dict:
    """添加账号前先验一下：能不能访问、多少条作品、有哪些合集"""
    import accounts as accmod
    sec = accmod.parse_sec_user_id(raw)
    if not sec:
        raise ValueError("无法解析 sec_user_id，请粘贴完整的抖音主页链接")
    res = _run_coro(_probe_async(sec))
    res["sec_user_id"] = sec
    return res


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="抖音作品元数据盘点")
    ap.add_argument("--all", action="store_true", help="扫描全部启用账号")
    ap.add_argument("--sec", help="只扫描指定 sec_user_id")
    ap.add_argument("--max", type=int, default=None, help="本次抓取上限（0=不限）")
    ap.add_argument("--check", action="store_true", help="只做 Cookie 体检")
    ap.add_argument("--probe", help="试探某个主页链接/ID 是否可访问")
    ap.add_argument("--engine", choices=["browser", "api"], default=None,
                    help="采集引擎，默认读 config.SCAN_ENGINE")
    ap.add_argument("--headless", action="store_true", help="浏览器引擎不弹窗")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    config.ensure_dirs()
    if args.probe:
        print(json.dumps(probe_account(args.probe), ensure_ascii=False, indent=2))
    elif args.check:
        print(json.dumps(check_cookie(), ensure_ascii=False, indent=2))
    elif args.sec:
        print(json.dumps(scan_account(args.sec, args.max, verbose=not args.quiet,
                                      engine=args.engine,
                                      headless=True if args.headless else None),
                         ensure_ascii=False, indent=2))
    elif args.all:
        scan_all(verbose=not args.quiet, engine=args.engine,
                 headless=True if args.headless else None)
        conn = db.connect()
        print(json.dumps(db.stats(conn), ensure_ascii=False, indent=2))
        conn.close()
    else:
        ap.print_help()
