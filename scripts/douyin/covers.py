"""
合集封面转存：抖音签名直链 → uniCloud 云存储永久直链

一个合集一张封面，取的就是「第一张图」：合集自己存的封面优先，
没有就退到合集里最靠前那一集的封面（按 episode_no 排，同集号再按发布时间）。

为什么要转存：抖音给的封面链接带签名（x-expires / x-signature），作者删视频、
CDN 换签名策略都能让它当场 403，而小程序里 image 加载失败不会重试。
转存之后，线上数据里就不该再出现任何 douyinpic 域名。

云路径与音频/正文同前缀，公共读按目录一次开全：
  jiugeyinxue/{account_slug}/{column_slug}/cover.jpg
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import cloud_client as cc
import config
import pipeline_db as db

# 封面不挑来源，认字节头就够；抖音清一色 jpeg，认不出来也按 jpeg 落
_MAGIC = [
    (b"\xff\xd8\xff", ".jpg", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", ".png", "image/png"),
    (b"GIF87a", ".gif", "image/gif"),
    (b"GIF89a", ".gif", "image/gif"),
    (b"BM", ".bmp", "image/bmp"),
]
_FALLBACK = (".jpg", "image/jpeg")
MAX_COVER_BYTES = 5 * 1024 * 1024

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def sniff(data: bytes) -> tuple[str, str]:
    """(扩展名, Content-Type)：webp 单独判，它的类型标记在 RIFF 之后四个字节"""
    for head, ext, mime in _MAGIC:
        if data.startswith(head):
            return ext, mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp", "image/webp"
    return _FALLBACK


def _unit_dir(account_slug: str, col_slug: str) -> Path:
    return config.COVERS_DIR / account_slug / col_slug


def cached_bytes(account_slug: str, col_slug: str) -> Optional[bytes]:
    """本地存过这张图就别再回抖音抓：重推封面只该花云存储的账"""
    d = _unit_dir(account_slug, col_slug)
    if not d.exists():
        return None
    for p in sorted(d.glob("cover.*"), key=lambda one: one.stat().st_size, reverse=True):
        if p.stat().st_size > 0:
            return p.read_bytes()
    return None


def fetch(url: str, *, timeout: float = 25.0) -> bytes:
    """拉封面字节流。抖音的图不要 cookie，但 UA 得像个浏览器"""
    import httpx

    resp = httpx.get(url, timeout=timeout, follow_redirects=True,
                     headers={"User-Agent": _UA, "Referer": "https://www.douyin.com/"})
    if resp.status_code != 200:
        raise cc.CloudError(f"封面下载失败 HTTP {resp.status_code}",
                            code=resp.status_code,
                            retryable=resp.status_code >= 500)
    data = resp.content
    if len(data) < 512:
        raise cc.CloudError(f"封面内容太小（{len(data)}B），不像一张图")
    if len(data) > MAX_COVER_BYTES:
        raise cc.CloudError(f"封面 {len(data) / 1024:.0f}KB 超过 "
                            f"{MAX_COVER_BYTES // 1024}KB 上限")
    return data


def units(conn, *, column_ids: Optional[list[str]] = None) -> list[dict]:
    """
    列出封面单元：一个合集一条，只算真有内容的合集

    first_cover 是兜底来源：合集自己没封面时用它，取第一集（集序最小、
    同为第一集时取最早发布）那张带封面的图。
    """
    sql = """
      SELECT c.column_id, c.name, c.slug, c.sec_user_id,
             c.cover_url, c.cover_url_cloud, c.cover_file_id,
             c.cover_source_url, c.cover_pushed_at,
             a.slug AS account_slug,
             (SELECT v.cover_url FROM videos v
                WHERE v.column_id = c.column_id
                  AND v.stage IN ('article','published')
                  AND v.cover_url IS NOT NULL AND v.cover_url != ''
                ORDER BY (v.episode_no IS NULL) ASC, v.episode_no ASC,
                         v.create_time ASC
                LIMIT 1) AS first_cover
      FROM columns c
      LEFT JOIN accounts a ON a.sec_user_id = c.sec_user_id
      WHERE EXISTS (SELECT 1 FROM videos v WHERE v.column_id = c.column_id
                      AND v.stage IN ('article','published'))
    """
    params: tuple = ()
    if column_ids is not None:
        # 传了空列表就是「这批不碰封面」，不能退化成全库
        if not column_ids:
            return []
        sql += " AND c.column_id IN (" + ",".join("?" * len(column_ids)) + ")"
        params = tuple(column_ids)
    sql += " ORDER BY c.sort, c.name"
    rows = [dict(r) for r in conn.execute(sql, params)]
    for u in rows:
        u["source_url"] = u.get("cover_url") or u.get("first_cover") or ""
    return rows


def push_unit(conn, u: dict, *, force: bool = False, verify: bool = True,
              reporter=None) -> dict:
    """抓一张 → 传云存储 → 探一次公共读 → 永久直链回写本地库"""
    import cloud_release as cr

    src = u.get("source_url") or ""
    out: dict[str, Any] = {"columnId": u["column_id"], "name": u.get("name") or "",
                           "cover": u.get("cover_url_cloud") or "",
                           "skipped": False, "cached": False, "warning": ""}
    if not src:
        return {**out, "skipped": True, "reason": "没有可用封面"}
    if not force and out["cover"].startswith("https://") and \
       u.get("cover_source_url") == src:
        return {**out, "skipped": True, "cached": True, "reason": "封面未变"}

    account_slug = cr._safe(u.get("account_slug") or "", "unknown")
    col_slug = cr._safe(u.get("slug") or u["column_id"], "col")

    # 只有本地那张确实对应当前来源才复用，否则换了封面会被缓存骗过去
    same_src = bool(src) and u.get("cover_source_url") == src
    data = cached_bytes(account_slug, col_slug) if (same_src and not force) else None
    if data is None:
        if reporter:
            reporter(f"{out['name']} 抓封面")
        data = fetch(src)
    ext, mime = sniff(data)
    target = _unit_dir(account_slug, col_slug)
    target.mkdir(parents=True, exist_ok=True)
    for stale in target.glob("cover.*"):
        stale.unlink()
    target.joinpath(f"cover{ext}").write_bytes(data)

    cloud_path = f"{config.CLOUD_PATH_PREFIX}/{account_slug}/{col_slug}/cover{ext}"
    if reporter:
        reporter(f"{out['name']} 上传封面（{len(data) / 1024:.0f}KB）")
    res = cc.put_bytes(cloud_path, data, mime, reporter=reporter)
    url, warning = cr.resolve_url(cloud_path, res, verify=verify)
    if not url.startswith("https://"):
        raise cc.CloudError(f"封面地址不是 https，无法上线: {url}")

    # 图片格式变了等于换了文件名，旧那张再没人引用，删掉省体积
    old_id = u.get("cover_file_id") or ""
    old_url = u.get("cover_url_cloud") or ""
    if old_id and old_url and not old_url.endswith(cloud_path):
        try:
            cc.delete_files([old_id])
        except Exception as e:  # noqa: BLE001
            warning = warning or f"旧封面删除失败：{type(e).__name__}"

    db.set_column_cover(conn, u["column_id"], cloud_url=url, file_id=res["fileID"],
                        source_url=src)
    db.log_event(conn, "info", "cover", u["column_id"],
                 (f"{out['name']} → {cloud_path}"
                  + (f" | {warning}" if warning else ""))[:160])
    return {**out, "cover": url, "skipped": False, "bytes": len(data),
            "cloudPath": cloud_path, "warning": warning}


def refresh(conn, *, column_ids: Optional[list[str]] = None, force: bool = False,
            verify: bool = True, dry_run: bool = False, reporter=None) -> dict:
    """把有内容的合集封面全刷一遍；发布流程会自动调，也能单独跑"""
    rows = units(conn, column_ids=column_ids)
    if dry_run:
        return {"units": len(rows), "dryRun": True,
                "need": [{"columnId": u["column_id"], "name": u["name"],
                          "hasCover": bool(u.get("cover_url_cloud")),
                          "source": (u.get("source_url") or "")[:80]} for u in rows]}
    done = cached = 0
    fails: list[str] = []
    warnings: list[str] = []
    covers: dict[str, str] = {}
    for i, u in enumerate(rows, 1):
        def _report(msg: str, _i=i):
            if reporter:
                reporter(f"[封面 {_i}/{len(rows)}] {msg}")
        try:
            res = push_unit(conn, u, force=force, verify=verify, reporter=_report)
            if res.get("cached"):
                cached += 1
            elif not res.get("skipped"):
                done += 1
            if res.get("warning"):
                warnings.append(f"{res['name']}: {res['warning']}")
            if res.get("cover"):
                covers[u["column_id"]] = res["cover"]
        except Exception as e:  # noqa: BLE001
            fails.append(f"{u.get('name') or u['column_id']}: "
                         f"{type(e).__name__}: {str(e)[:120]}")
            if reporter:
                reporter(f"[封面 {i}/{len(rows)}] 失败 {u.get('name')}: {str(e)[:80]}")
    return {"units": len(rows), "uploaded": done, "cached": cached,
            "noSource": sum(1 for u in rows if not u.get("source_url")),
            "fails": fails, "warnings": warnings, "covers": covers}


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="合集封面转存到云存储")
    ap.add_argument("--force", action="store_true", help="已转存过的也重抓重传")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-verify", action="store_true", help="不探公共读，直接落永久地址")
    args = ap.parse_args()
    conn = db.connect()
    print(json.dumps(refresh(conn, force=args.force, dry_run=args.dry_run,
                             verify=not args.no_verify,
                             reporter=lambda m: print(f"  {m}")),
                     ensure_ascii=False, indent=2))
    conn.close()
