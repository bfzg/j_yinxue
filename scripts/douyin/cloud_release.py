"""
发布到 uniCloud：文件进云存储，元数据进云数据库，小程序只读接口

一轮发布四步：
  1. 逐集把音频 .m4a 与正文 .txt 推到云存储，把永久地址回写 sqlite
  2. 每个合集把首图转存成一张封面，永久地址回写 columns.cover_url_cloud
  3. 账号 / 栏目 / 分集的元数据 upsert 进云数据库（正文不进库，只存 URL）
  4. pushRelease 让 dataVersion +1，小程序下次进首页就能感知到更新

云路径规范（同时决定公共读能不能按目录整体开）：
  jiugeyinxue/{account_slug}/{column_slug}/audio/{stem}.m4a
  jiugeyinxue/{account_slug}/{column_slug}/article/{stem}.txt
  jiugeyinxue/{account_slug}/{column_slug}/cover.jpg

排序约定：episode.sort = -(发布时间戳)，读接口按 sort 升序，等价于全站最新在前；
栏目内集序另有 column.episodeIds 显式决定，不受这个值影响。
"""
from __future__ import annotations

import hashlib
import re
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import cloud_client as cc
import config
import media
import pipeline_db as db

_SAFE = re.compile(r"[^0-9A-Za-z_-]+")


def _safe(part: str, fallback: str = "misc") -> str:
    """目录名要能安全进 URL，中文栏目名一律转拼音前的兜底：用 id 而不是名字"""
    s = _SAFE.sub("-", str(part or "").strip()).strip("-")
    return s or fallback


def _quote_path(cloud_path: str) -> str:
    """逐段编码，斜杠留着"""
    return "/".join(urllib.parse.quote(one, safe="") for one in cloud_path.split("/"))


def layout(conn, v: dict, audio: Path) -> tuple[str, str, str]:
    """返回 (account_slug, column_slug, stem)"""
    acc = conn.execute("SELECT slug, name FROM accounts WHERE sec_user_id=?",
                       (v.get("sec_user_id") or "",)).fetchone()
    account_slug = _safe(acc["slug"] if acc else "", "unknown")
    col_slug = "single"
    if v.get("column_id"):
        row = conn.execute("SELECT slug, column_id FROM columns WHERE column_id=?",
                           (v["column_id"],)).fetchone()
        col_slug = _safe((row["slug"] if row else "") or v["column_id"], "single")
    ep = int(v.get("episode_no") or 0)
    stem = f"ep{ep:03d}_{v['aweme_id']}" if ep else v["aweme_id"]
    return account_slug, col_slug, stem


def cloud_paths(conn, v: dict, audio: Path) -> tuple[str, str]:
    """(音频 cloudPath, 正文 cloudPath)"""
    account_slug, col_slug, stem = layout(conn, v, audio)
    root = config.CLOUD_PATH_PREFIX
    ext = (audio.suffix or ".m4a").lower()
    return (f"{root}/{account_slug}/{col_slug}/audio/{stem}{ext}",
            f"{root}/{account_slug}/{col_slug}/article/{stem}.txt")


def _readable(status: int) -> bool:
    """公共读没开就是 403/404，这时永久地址不能用"""
    return status in (200, 206)


def resolve_url(cloud_path: str, res: dict, *, verify: bool = True,
                timeout: float = 20.0, query: str = "") -> tuple[str, str]:
    """
    返回 (可用地址, 警告)。

    优先永久地址：{CLOUD_STORAGE_HOST}/{cloudPath}，不带签名、不过期，能直接落库。
    探测不通就说明目录还没开公共读，退成云函数回传的临时签名地址并给出警告。

    query 是给正文用的内容指纹：同一个 cloudPath 换了内容就换个地址，
    CDN 那份旧缓存自然作废，不必每次改排版都去控制台刷预热。
    """
    permanent = f"{config.CLOUD_STORAGE_HOST.rstrip('/')}/{_quote_path(cloud_path)}"
    if query:
        permanent += f"?{query}"
    if not verify:
        return permanent, ""
    try:
        resp = httpx_get(permanent, {"Range": "bytes=0-0"}, timeout)
        if _readable(resp.status_code):
            return permanent, ""
        warning = (f"永久地址不可直读（HTTP {resp.status_code}），"
                   f"请把云存储 {config.CLOUD_PATH_PREFIX}/ 目录设为公共读后重推")
    except Exception as e:  # noqa: BLE001
        warning = f"永久地址探测失败（{type(e).__name__}），改用临时地址"
    temp = str((res or {}).get("url") or "")
    if temp.startswith("https://"):
        return temp, warning
    # 临时地址也拿不到时仍然返回永久地址，至少路径是对的
    return permanent, (warning or "云函数没有回传可用地址")


def httpx_get(url: str, headers: dict, timeout: float) -> Any:
    import httpx
    return httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)


def _article_text(v: dict) -> str:
    """正文统一过一遍排版收口，早于排版规则生成的存量文章也干净"""
    from article_formatter import strip_footer, tidy_text

    src = Path(v["article_path"])
    return tidy_text(strip_footer(src.read_text(encoding="utf-8"))).strip() + "\n"


def _text_version(text: str) -> str:
    """正文内容指纹，进 URL 当缓存键；改一个字就换一个地址"""
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:10]


def already_pushed(v: dict, audio: Path, text_src: Path) -> bool:
    """三件事都成立才算推过：有云地址、有 fileID、本地文件没比推送时间更新"""
    if not (v.get("audio_url") or "").startswith("https://") or \
       not (v.get("article_url") or "").startswith("https://"):
        return False
    if not v.get("cloud_pushed_at"):
        return False
    try:
        pushed = datetime.strptime(v["cloud_pushed_at"], "%Y-%m-%d %H:%M:%S").timestamp()
    except (TypeError, ValueError):
        return False
    newest = max(p.stat().st_mtime for p in (audio, text_src) if p.exists())
    return newest <= pushed


def publish_files(conn, aweme_id: str, *, force: bool = False, verify: bool = True,
                  reporter=None) -> dict:
    """
    把一集的音频 + 正文推到云存储，成功即回写地址并把阶段置 published

    返回 {audioUrl, articleUrl, audioFileId, articleFileId, skipped, warning}
    """
    v = db.get_video(conn, aweme_id)
    if not v:
        raise cc.CloudError(f"数据库里没有这条作品: {aweme_id}")
    if not v.get("article_path") or not Path(v["article_path"]).exists():
        raise cc.CloudError("还没有生成文章，先跑 article 步骤")

    audio = Path(v.get("audio_path") or "")
    if not audio.exists():
        audio = media.audio_path_any(aweme_id)
    if not audio.exists():
        raise cc.CloudError(f"音频文件不存在: {aweme_id}")

    text_src = Path(v["article_path"])
    if not force and already_pushed(v, audio, text_src):
        return {"audioUrl": v["audio_url"], "articleUrl": v["article_url"],
                "audioFileId": v.get("audio_file_id") or "",
                "articleFileId": v.get("article_file_id") or "",
                "skipped": True, "warning": ""}

    audio_cp, text_cp = cloud_paths(conn, v, audio)
    out: dict[str, Any] = {"skipped": False, "warning": ""}

    # 正文只有几 KB，先传它：万一音频被限流，这集至少内容是完整的
    if reporter:
        reporter(f"{aweme_id} 正文上传中")
    text = _article_text(v)
    res_txt = cc.put_text(text_cp, text)
    out["articleFileId"] = res_txt["fileID"]
    url_txt, warn_txt = resolve_url(text_cp, res_txt, verify=verify,
                                    query=f"v={_text_version(text)}")
    out["articleUrl"] = url_txt

    if reporter:
        size_mb = audio.stat().st_size / 1048576
        reporter(f"{aweme_id} 音频上传中（{size_mb:.1f}MB）")
    res_aud = cc.put_file(audio, audio_cp, media.media_type(audio), reporter=reporter)
    out["audioFileId"] = res_aud["fileID"]
    url_aud, warn_aud = resolve_url(audio_cp, res_aud, verify=verify)
    out["audioUrl"] = url_aud

    warning = warn_aud or warn_txt
    out["warning"] = warning
    out["cloudPath"] = {"audio": audio_cp, "article": text_cp}
    if not url_aud.startswith("https://") or not url_txt.startswith("https://"):
        raise cc.CloudError(f"云地址不是 https，无法上线: {out}")

    db.set_stage(conn, aweme_id, "published",
                 audio_url=url_aud, article_url=url_txt,
                 audio_file_id=out["audioFileId"], article_file_id=out["articleFileId"],
                 cloud_pushed_at=db.now())
    db.log_event(conn, "info", "publish", aweme_id,
                 (f"→ {audio_cp}" + (f" | {warning}" if warning else ""))[:160])
    return out


def publish_text(conn, aweme_id: str, *, verify: bool = True, reporter=None) -> dict:
    """只重推正文（几 KB），音频原样不动

    改排版规则之后用它刷新线上文章，没必要为了几行字把几十 MB 音频再传一遍。
    """
    v = db.get_video(conn, aweme_id)
    if not v or not v.get("article_path"):
        raise cc.CloudError(f"数据库里没有这篇文章: {aweme_id}")
    text_src = Path(v["article_path"])
    if not text_src.exists():
        raise cc.CloudError(f"正文文件不存在: {text_src}")

    audio = Path(v.get("audio_path") or "")
    if not audio.exists():
        audio = media.audio_path_any(aweme_id)
    _audio_cp, text_cp = cloud_paths(conn, v, audio)

    text = _article_text(v)
    if reporter:
        reporter(f"{aweme_id} 正文上传中（{len(text)} 字）")
    res = cc.put_text(text_cp, text)
    url, warning = resolve_url(text_cp, res, verify=verify,
                               query=f"v={_text_version(text)}")
    if not url.startswith("https://"):
        raise cc.CloudError(f"云地址不是 https，无法上线: {url}")

    db.set_stage(conn, aweme_id, v.get("stage") or "article",
                 audio_url=v.get("audio_url"), article_url=url,
                 audio_file_id=v.get("audio_file_id"),
                 article_file_id=res["fileID"], cloud_pushed_at=db.now())
    db.log_event(conn, "info", "publish", aweme_id,
                 (f"仅正文 → {text_cp}" + (f" | {warning}" if warning else ""))[:160])
    return {"articleUrl": url, "articleFileId": res["fileID"],
            "cloudPath": text_cp, "chars": len(text), "warning": warning}


def release_texts(conn, *, ids: Optional[list[str]] = None, column_id: str = "",
                  sec_user_id: str = "", limit: int = 0, note: str = "",
                  dry_run: bool = False, reporter=None) -> dict:
    """批量刷正文 → 同步元数据 → 版本号 +1，音频一个字节不碰

    只动已上线的条目：没发布的正文跟着正常发布一起推，没必要提前占云空间。
    ids / column_id / sec_user_id 任选其一缩小范围，都不传就是全部已上线条目。
    """
    where = "stage='published' AND COALESCE(article_path, '') <> ''"
    params: tuple = ()
    if ids:
        where += " AND aweme_id IN (" + ",".join("?" * len(ids)) + ")"
        params = tuple(ids)
    elif column_id:
        where, params = where + " AND column_id=?", (column_id,)
    elif sec_user_id:
        where, params = where + " AND sec_user_id=?", (sec_user_id,)

    targets = [r["aweme_id"] for r in db.list_videos(
        conn, where, params, "create_time DESC", limit=limit or 5000)]
    if not targets:
        return {"updated": 0, "fails": [], "note": "没有可刷新的正文"}
    if dry_run:
        return {"updated": 0, "dryRun": True, "targets": len(targets),
                "ids": targets[:50], "note": f"预计刷新 {len(targets)} 集正文"}

    ok = 0
    fails: list[str] = []
    warnings: list[str] = []
    for i, aweme_id in enumerate(targets, 1):
        def _report(msg: str, _i=i):
            if reporter:
                reporter(f"[{_i}/{len(targets)}] {msg}")
        try:
            res = publish_text(conn, aweme_id, reporter=_report)
            ok += 1
            if res.get("warning"):
                warnings.append(f"{aweme_id}: {res['warning']}")
        except Exception as e:  # noqa: BLE001
            fails.append(f"{aweme_id}: {type(e).__name__}: {str(e)[:140]}")
            if reporter:
                reporter(f"[{i}/{len(targets)}] 失败 {aweme_id}: {str(e)[:80]}")
            time.sleep(1.0)

    if reporter:
        reporter("同步元数据到云数据库")
    meta = push_meta(conn, reporter=reporter)
    rel_note = note or f"{datetime.now():%m-%d %H:%M} 刷新正文 {ok} 集"
    rel = cc.call_content("pushRelease", note=rel_note[:200],
                          episodes=meta["episodes"]["total"])
    db.log_event(conn, "info", "release", "",
                 f"v{rel.get('dataVersion')} 刷新正文 {ok} 失败 {len(fails)}")
    return {"updated": ok, "fails": fails[:30], "nFails": len(fails),
            "meta": meta, "release": rel, "warnings": warnings[:10],
            "note": rel_note}


# ---------- 元数据推送 ----------

def _episode_row(conn, r: dict) -> dict:
    import exporter

    row = exporter._article_item(r, r.get("article_url") or "", r.get("audio_url") or "")
    # 线上只认云存储那份封面，抖音原链留作没转存成功时的临时兜底
    row["cover"] = r.get("column_cover") or r.get("cover_url") or ""
    create_time = int(r.get("create_time") or 0)
    row["sort"] = -create_time if create_time else int(row.get("episodeNo") or 0)
    row["audioFileId"] = r.get("audio_file_id") or ""
    row["articleFileId"] = r.get("article_file_id") or ""
    row["status"] = r.get("stage") or ""
    row["channel"] = "douyin"
    row["syncedAt"] = int(time.time() * 1000)
    row["enabled"] = bool(r.get("stage") == "published")
    return row


def episode_rows(conn, ids: Optional[list[str]] = None) -> list[dict]:
    sql = """
      SELECT v.*, c.name AS column_name, c.slug AS column_slug,
             c.episode_total AS col_total, c.sort AS col_sort,
             c.cover_url_cloud AS column_cover,
             a.slug AS account_slug, a.name AS account_name, a.style AS account_style
      FROM videos v
      LEFT JOIN columns c ON c.column_id = v.column_id
      LEFT JOIN accounts a ON a.sec_user_id = v.sec_user_id
    """
    params: tuple = ()
    if ids:
        sql += " WHERE v.aweme_id IN (" + ",".join("?" * len(ids)) + ")"
        params = tuple(ids)
    else:
        sql += " WHERE v.stage IN ('article','published')"
    sql += " ORDER BY a.name, c.sort, c.name, v.episode_no, v.create_time DESC"
    return [_episode_row(conn, dict(r)) for r in conn.execute(sql, params)]


def column_rows(conn) -> list[dict]:
    """栏目里的 episodeIds 按集号排好，线上集序就以它为准"""
    cols = db.list_columns(conn)
    out = []
    for c in cols:
        rows = conn.execute(
            "SELECT aweme_id, episode_no, create_time FROM videos "
            "WHERE column_id=? AND stage='published' ORDER BY episode_no, create_time",
            (c["column_id"],)).fetchall()
        ids = [r["aweme_id"] for r in rows]
        if not ids:
            continue
        acc = conn.execute("SELECT slug, name FROM accounts WHERE sec_user_id=?",
                           (c["sec_user_id"],)).fetchone()
        out.append({
            "id": _safe(c.get("slug") or c["column_id"], c["column_id"]),
            "name": c.get("name") or "",
            "accountId": (acc["slug"] if acc else "") or "",
            "accountName": (acc["name"] if acc else "") or "",
            "cover": c.get("cover_url_cloud") or c.get("cover_url") or "",
            "sort": int(c.get("sort") or 0),
            "episodeTotal": int(c.get("episode_total") or 0),
            "nEpisodes": len(ids),
            "episodeIds": ids,
        })
    return out


def account_rows(conn) -> list[dict]:
    out = []
    for a in db.list_accounts(conn):
        n = conn.execute("SELECT COUNT(*) FROM videos WHERE sec_user_id=? "
                         "AND stage='published'", (a["sec_user_id"],)).fetchone()[0]
        out.append({
            "id": a.get("slug") or a["sec_user_id"],
            "slug": a.get("slug") or "",
            "name": a.get("name") or "",
            "style": a.get("style") or "",
            "enabled": bool(a.get("enabled", 1)),
            "maxItems": int(a.get("max_items") or 0),
            "awemeCount": int(a.get("aweme_count") or 0),
            "scannedItems": int(a.get("scanned_items") or 0),
            "updatedAt": a.get("last_scan_at") or "",
            "publishedItems": int(n),
        })
    return out


def push_meta(conn, *, ids: Optional[list[str]] = None, reporter=None) -> dict:
    """账号 → 栏目 → 分集，顺序不能反：栏目引用的分集得先存在或被一起写"""
    result: dict[str, Any] = {}
    accs = account_rows(conn)
    if accs and reporter:
        reporter(f"推账号 {len(accs)} 个")
    if accs:
        result["accounts"] = cc.call_content("upsertAccounts", items=accs)

    cols = column_rows(conn)
    if cols and reporter:
        reporter(f"推栏目 {len(cols)} 个")
    if cols:
        result["columns"] = cc.call_content("upsertColumns", items=cols)

    eps = episode_rows(conn, ids)
    done = 0
    for group in cc.batches(eps, 120):
        result.setdefault("episodes", {"written": 0})
        res = cc.call_content("upsertEpisodes", items=group, reporter=reporter)
        done += int((res or {}).get("written") or 0)
        if reporter:
            reporter(f"推分集 {done}/{len(eps)}")
    result["episodes"] = {"written": done, "total": len(eps)}
    return result


def touched_columns(conn, ids: list[str]) -> list[str]:
    """这批要发布的集涉及哪些合集，封面就只刷这几个"""
    if not ids:
        return []
    rows = conn.execute(
        "SELECT DISTINCT column_id FROM videos WHERE column_id IS NOT NULL "
        "AND column_id != '' AND aweme_id IN (" + ",".join("?" * len(ids)) + ")",
        tuple(ids)).fetchall()
    return [r["column_id"] for r in rows]


def release(conn, *, ids: Optional[list[str]] = None, column_id: str = "",
            sec_user_id: str = "", limit: int = 0, force: bool = False,
            note: str = "", dry_run: bool = False, reporter=None) -> dict:
    """
    一条龙：传文件 → 推元数据 → 版本号 +1

    ids / column_id / sec_user_id 任选其一缩小范围，都不传就是全部待发布条目。
    """
    where, params = "stage='article'", ()
    if ids:
        where = "stage IN ('article','published') AND aweme_id IN (" + ",".join("?" * len(ids)) + ")"
        params = tuple(ids)
    elif column_id:
        where, params = "stage='article' AND column_id=?", (column_id,)
    elif sec_user_id:
        where, params = "stage='article' AND sec_user_id=?", (sec_user_id,)
    order = "create_time DESC"
    targets = [r["aweme_id"] for r in db.list_videos(conn, where, params, order,
                                                    limit=limit or 5000)]
    if not targets:
        return {"published": 0, "skipped": 0, "fails": [], "note": "没有待发布条目"}
    if dry_run:
        return {"published": 0, "skipped": 0, "dryRun": True,
                "targets": len(targets), "ids": targets[:50],
                "note": f"预计发布 {len(targets)} 集"}

    ok = skipped = 0
    fails: list[str] = []
    warnings: list[str] = []
    for i, aweme_id in enumerate(targets, 1):
        def _report(msg: str, _i=i):
            if reporter:
                reporter(f"[{_i}/{len(targets)}] {msg}")
        try:
            res = publish_files(conn, aweme_id, force=force, reporter=_report)
            if res.get("skipped"):
                skipped += 1
            else:
                ok += 1
            if res.get("warning"):
                warnings.append(f"{aweme_id}: {res['warning']}")
        except Exception as e:  # noqa: BLE001
            fails.append(f"{aweme_id}: {type(e).__name__}: {str(e)[:140]}")
            if reporter:
                reporter(f"[{i}/{len(targets)}] 失败 {aweme_id}: {str(e)[:80]}")
            time.sleep(1.0)

    # 封面必须赶在元数据之前：栏目行里的 cover 取的就是这步回写的云地址
    import covers
    col_ids = touched_columns(conn, targets)
    if reporter:
        reporter(f"转存合集封面（{len(col_ids)} 个）")
    cov = covers.refresh(conn, column_ids=col_ids, force=force, reporter=reporter)

    if reporter:
        reporter("同步元数据到云数据库")
    meta = push_meta(conn, reporter=reporter)

    rel_note = note or f"{datetime.now():%m-%d %H:%M} 发布 {ok} 集（跳过 {skipped}）"
    rel = cc.call_content("pushRelease", note=rel_note[:200], episodes=meta["episodes"]["total"])
    db.log_event(conn, "info", "release", "",
                 f"v{rel.get('dataVersion')} 新发 {ok} 跳过 {skipped} 失败 {len(fails)}")
    return {"published": ok, "skipped": skipped, "fails": fails[:30],
            "nFails": len(fails), "meta": meta, "release": rel, "covers": cov,
            "warnings": warnings[:10] + cov.get("warnings", [])[:5],
            "note": rel_note}


# ---------- 线上状态 / 下架 ----------

def remote_stats() -> dict:
    return cc.call_content("remoteStats")


def init_site(app: Optional[dict] = None) -> dict:
    return cc.call_content("initSite", app=app or None)


def local_status(conn) -> dict:
    """本地和线上各有多少条，面板用来提醒「还没推」"""
    q = "SELECT stage, COUNT(*) FROM videos GROUP BY stage"
    by_stage = {r[0]: r[1] for r in conn.execute(q)}
    pushed = conn.execute("SELECT COUNT(*) FROM videos WHERE cloud_pushed_at IS NOT NULL"
                          " AND cloud_pushed_at != ''").fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM videos WHERE stage='article'").fetchone()[0]
    return {"by_stage": by_stage, "pushed": pushed, "pending": pending,
            "articles": conn.execute("SELECT COUNT(*) FROM columns").fetchone()[0],
            "accounts": conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0],
            "cloud": cc.configured()}


def offline(conn, ids: list[str], purge_files: bool = False) -> dict:
    """下架：库里置 enabled=false；purge_files 才真的删云存储文件和记录"""
    ids = [str(one) for one in ids if one]
    if not ids:
        raise cc.CloudError("没有要下架的条目")
    res = cc.call_content("deleteEpisodes", ids=ids, purgeFiles=bool(purge_files))
    for aweme_id in ids:
        row = db.get_video(conn, aweme_id)
        if not row:
            continue
        if purge_files:
            db.set_stage(conn, aweme_id, row.get("stage") or "article",
                         audio_url=None, article_url=None, audio_file_id=None,
                         article_file_id=None, cloud_pushed_at=None)
        db.log_event(conn, "info", "offline", aweme_id,
                     f"purge={int(purge_files)}")
    rel = cc.call_content("pushRelease", note=f"下架 {len(ids)} 集", episodes=0)
    return {"removed": (res or {}).get("removed", len(ids)),
            "filesRemoved": (res or {}).get("filesRemoved", 0),
            "release": rel}


def retract(conn, ids: list[str]) -> dict:
    """把某集从线上状态撤回本地态：清掉云端地址，下次发布当作新条目重推"""
    ids = [str(one) for one in ids if one]
    if not ids:
        raise cc.CloudError("没有要撤回的条目")
    for aweme_id in ids:
        db.set_stage(conn, aweme_id, "article", audio_url=None, article_url=None,
                     audio_file_id=None, article_file_id=None, cloud_pushed_at=None)
        db.log_event(conn, "info", "retract", aweme_id, "清云端地址，待重推")
    return {"retracted": len(ids)}


def compare(conn) -> dict:
    """线上 vs 本地条数差，只读，不改任何东西"""
    remote = remote_stats()
    local = local_status(conn)
    return {"remote": remote, "local": local,
            "diff": {"episodes": int(remote.get("counts", {}).get("episodes", 0))
                     - local.get("pushed", 0)}}
