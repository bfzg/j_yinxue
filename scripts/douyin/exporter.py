"""
导出与发布

publish_one：把 mp3 + 文章正文推到 COS，作品阶段变 published
export_all：sqlite → src/static/data/{articles.json, playlist.json, columns.json}

导出按 id upsert，绝不无脑 append（旧 uploader 每跑一次就多塞一条重复记录，
这是必修的坑）。前端手工加的老条目原样保留。
"""
from __future__ import annotations

import json
import sys
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
import config
import pipeline_db as db

_APP = {"name": "九哥隐学", "author": "九哥", "cover": "",
        "description": "九哥原创文章阅读"}


def _cos_url(key: str) -> str:
    return (f"https://{config.COS_BUCKET}.cos.{config.COS_REGION}"
            f".myqcloud.com/" + urllib.parse.quote(key))


def _date(create_time: int) -> str:
    if not create_time:
        return datetime.now().strftime("%Y-%m-%d")
    return datetime.fromtimestamp(create_time).strftime("%Y-%m-%d")


def _category(style: str) -> str:
    """账号定位的第一个词当分类，前端分类 tabs 用它"""
    s = (style or "").strip()
    if not s:
        return "文章"
    for sep in (" / ", "/", "·", "、", ","):
        if sep in s:
            s = s.split(sep)[0].strip()
            break
    return s or "文章"


def _rows(conn, only_published: bool = True) -> list[dict]:
    sql = """
      SELECT v.*, c.name AS column_name, c.slug AS column_slug,
             c.source AS column_kind2, c.episode_total AS col_total, c.sort AS col_sort,
             a.slug AS account_slug, a.name AS account_name, a.style AS account_style
      FROM videos v
      LEFT JOIN columns c ON c.column_id = v.column_id
      LEFT JOIN accounts a ON a.sec_user_id = v.sec_user_id
    """
    if only_published:
        sql += " WHERE v.stage='published'"
    else:
        sql += " WHERE v.stage IN ('article','published')"
    sql += " ORDER BY a.name, c.sort, c.name, v.episode_no, v.create_time DESC"
    return [dict(r) for r in conn.execute(sql).fetchall()]


def _article_item(r: dict, article_url: str, audio_url: str) -> dict:
    meta = {}
    if r.get("article_path"):
        p = Path(str(r["article_path"]).replace(".md", ".article.json"))
        if p.exists():
            meta = json.loads(p.read_text(encoding="utf-8"))
    lead = meta.get("lead") or ""
    return {
        "id": r["aweme_id"],
        "awemeId": r["aweme_id"],
        "title": meta.get("title") or r["title"],
        "summary": lead[:120],
        "category": _category(r.get("account_style") or ""),
        "cover": r.get("cover_url") or "",
        "publishedAt": _date(r.get("create_time") or 0),
        "articleUrl": article_url,
        "audioUrl": audio_url,
        "enabled": True,
        "sort": int(r.get("episode_no") or 0),
        "accountId": r.get("account_slug") or "",
        "accountName": r.get("account_name") or "",
        "columnId": r.get("column_slug") or "",
        "columnName": r.get("column_name") or "",
        "episodeNo": int(r.get("episode_no") or 0),
        "episodeTotal": int(r.get("ep_total") or r.get("col_total") or 0),
        "sourceUrl": r.get("source_url") or "",
        "duration": int((r.get("duration_ms") or 0) / 1000),
        "wordCount": int(meta.get("word_count") or 0),
    }


# ---------- 发布到 COS ----------

def publish_one(conn, aweme_id: str, verbose: bool = True) -> dict:
    """上传音频 + 正文，成功后阶段置 published"""
    from uploader import CosUploader

    v = db.get_video(conn, aweme_id)
    if not v:
        raise ValueError(f"数据库里没有这条作品: {aweme_id}")
    if not v.get("article_path") or not Path(v["article_path"]).exists():
        raise RuntimeError("还没有生成文章，先跑 article 步骤")
    audio = Path(v.get("audio_path") or (config.AUDIO_DIR / f"{aweme_id}.mp3"))
    if not audio.exists():
        raise RuntimeError(f"音频文件不存在: {audio}")

    _acc = conn.execute("SELECT slug, name FROM accounts WHERE sec_user_id=?",
                        (v["sec_user_id"],)).fetchone()
    col_slug = "single"
    if v.get("column_id"):
        row = conn.execute("SELECT slug FROM columns WHERE column_id=?",
                           (v["column_id"],)).fetchone()
        col_slug = (row["slug"] if row else "single")
    account_slug = (_acc["slug"] if _acc else "") or "unknown"
    ep = int(v.get("episode_no") or 0)
    stem = f"ep{ep}_{aweme_id}" if ep else aweme_id

    audio_key = f"{config.COS_AUDIO_PREFIX}/{account_slug}/{col_slug}/{stem}.mp3"
    text_key = f"{config.COS_TXT_PREFIX}/{account_slug}/{col_slug}/{stem}.txt"

    up = CosUploader()
    if not up.upload_file(audio, audio_key):
        raise RuntimeError("音频上传 COS 失败")
    tmp_txt = config.OUTPUT_DIR / "tmp" / f"{stem}.txt"
    tmp_txt.parent.mkdir(parents=True, exist_ok=True)
    # 早于排版收口生成的存量文章，上传前统一再过一遍，避免带着弯引号上线
    from article_formatter import tidy_text
    tmp_txt.write_text(
        tidy_text(Path(v["article_path"]).read_text(encoding="utf-8")) + "\n",
        encoding="utf-8")
    try:
        if not up.upload_file(tmp_txt, text_key):
            raise RuntimeError("正文上传 COS 失败")
    finally:
        tmp_txt.unlink(missing_ok=True)

    urls = {"audio_url": _cos_url(audio_key), "article_url": _cos_url(text_key)}
    db.set_stage(conn, aweme_id, "published", **urls)
    db.log_event(conn, "info", "publish", aweme_id, urls["audio_url"][:120])
    if verbose:
        print(f"  [发布] {aweme_id} → {audio_key}")
    return urls


# ---------- 导出前端数据 ----------

def _load(path: Path, wrapper: dict) -> dict:
    if not path.exists():
        return wrapper
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return wrapper
    return data if isinstance(data, dict) and "items" in data else wrapper


def _write(path: Path, data: dict) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return len(data["items"])


def export_all(conn=None, only_published: bool = True,
               verbose: bool = True) -> dict:
    """重建三个前端数据文件，返回各自条数"""
    own = conn is None
    conn = conn or db.connect()
    rows = _rows(conn, only_published)

    articles, playlist, columns = [], [], {}
    for r in rows:
        article_url = r.get("article_url") or ""
        audio_url = r.get("audio_url") or ""
        if not article_url or not audio_url:
            if only_published:
                # 只导已发布的：没 COS 直链的条目不进前端数据，避免线上 404
                continue
            # 本地预览没上传 COS，就用面板的 /media 路由当直链，
            # 前端起个 H5 服务即可试听试读（同一局域网内把 base 换成面板机 IP）
            aid = r["aweme_id"]
            if not audio_url:
                audio_url = f"{config.LOCAL_MEDIA_BASE}/media/audio?aweme_id={aid}"
            if not article_url:
                article_url = (f"{config.LOCAL_MEDIA_BASE}"
                               f"/media/article/text?aweme_id={aid}")
        item = _article_item(r, article_url, audio_url)
        articles.append(item)
        playlist.append({
            "id": item["id"], "title": item["title"],
            "description": item["summary"], "cover": item["cover"],
            "audioUrl": audio_url, "duration": item["duration"],
            "sort": item["sort"], "publishedAt": item["publishedAt"],
            "enabled": True, "columnName": item["columnName"],
            "columnId": item["columnId"], "episodeNo": item["episodeNo"],
            "episodeTotal": item["episodeTotal"], "accountId": item["accountId"],
        })
        key = item["columnId"] or "single"
        col = columns.setdefault(key, {
            "id": key, "name": item["columnName"] or "单篇",
            "accountId": item["accountId"], "accountName": item["accountName"],
            "cover": item["cover"], "sort": r.get("col_sort") or 0,
            "episodeTotal": item["episodeTotal"], "episodes": [],
        })
        col["episodes"].append({
            "awemeId": item["id"], "title": item["title"], "episodeNo": item["episodeNo"],
            "audioUrl": audio_url, "articleUrl": article_url,
            "duration": item["duration"], "publishedAt": item["publishedAt"],
            "summary": item["summary"],
        })

    col_items = sorted(columns.values(), key=lambda c: (c["sort"], c["name"]))
    for c in col_items:
        c["episodes"].sort(key=lambda e: (e["episodeNo"] or 10 ** 6))
        c["nEpisodes"] = len(c["episodes"])

    fd = config.FRONTEND_DATA_DIR
    a_doc = _load(fd / "articles.json", {"app": _APP, "items": []})
    p_doc = _load(fd / "playlist.json",
                  {"app": _APP, "settings": {"autoplayNext": True,
                                             "playMode": "sequence"}, "items": []})
    c_doc = {"app": _APP, "items": col_items}

    n_art = _upsert(a_doc, articles)
    n_pls = _upsert(p_doc, playlist)
    # 这两个必须落盘：之前只 upsert 到内存，跑完导出文件还是旧的
    _write(fd / "articles.json", a_doc)
    _write(fd / "playlist.json", p_doc)
    _write(fd / "columns.json", c_doc)

    if verbose:
        print(f"  articles.json 共 {len(a_doc['items'])} 条（本次写入 {n_art}）")
        print(f"  playlist.json 共 {len(p_doc['items'])} 条（本次写入 {n_pls}）")
        print(f"  columns.json 共 {len(col_items)} 个栏目")
    if own:
        conn.close()
    return {"articles": n_art, "playlist": n_pls, "columns": len(col_items),
            "dir": str(fd)}


def _upsert(doc: dict, items: list[dict]) -> int:
    """按 id 覆盖，保留非本流程生成的历史条目"""
    by_id = {it["id"]: i for i, it in enumerate(doc["items"])}
    for it in items:
        if it["id"] in by_id:
            merged = {**doc["items"][by_id[it["id"]]], **it}
            doc["items"][by_id[it["id"]]] = merged
        else:
            doc["items"].append(it)
            by_id[it["id"]] = len(doc["items"]) - 1
    return len(items)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="导出前端数据 / 发布单集")
    ap.add_argument("--publish", help="发布指定 aweme_id 到 COS")
    ap.add_argument("--export", action="store_true", help="导出 json")
    ap.add_argument("--all-stages", action="store_true",
                    help="连未发布的也导出（本地预览用）")
    args = ap.parse_args()
    config.ensure_dirs()
    conn = db.connect()
    if args.publish:
        print(json.dumps(publish_one(conn, args.publish), ensure_ascii=False))
    if args.export or not args.publish:
        print(json.dumps(export_all(conn, only_published=not args.all_stages),
                         ensure_ascii=False))
    conn.close()
