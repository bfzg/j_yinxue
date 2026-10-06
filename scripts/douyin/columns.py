"""
栏目整理：把散落的作品归成一个个可连续收看的栏目

优先级：
  1. 抖音原生合集 mix_info —— 平台已经给了合集名和第几集，直接采信
  2. 标题正则 —— 「第X集 / （12） / Part 3 / 上中下」这类自编号
  3. qwen-plus 语义聚类 —— 剩下的按主题批量归类，少于 3 集不开栏目
  4. 兜底 —— 归进该账号的「单篇」桶，人工可在面板里拖动改名并锁定

人工锁定（locked=1）的栏目和它名下的作品，重跑时绝不覆盖。
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
import llm
import pipeline_db as db

_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_POS = {"上": 1, "中": 2, "下": 3}

_EP_PATTERNS = [
    re.compile(r"第\s*([0-9一二三四五六七八九十百]{1,5})\s*[集章讲期部篇卷回]"),
    re.compile(r"(?:Part|EP|Episode|Ep|#)\s*([0-9]{1,4})", re.I),
    re.compile(r"[（(\[【]\s*([0-9]{1,4})\s*[)）\]】]"),
    re.compile(r"(?:^|[\s\-_/｜|：:])([0-9]{1,3})\s*$"),
    re.compile(r"([上中下])\s*[篇集]?$"),
]


def cn_to_int(text: str) -> Optional[int]:
    """支持 1-99 的中文数字与阿拉伯数字"""
    text = text.strip()
    if text.isdigit():
        return int(text)
    if not text:
        return None
    total = 0
    if "百" in text:
        parts = text.split("百")
        total += _CN_DIGITS.get(parts[0], 1) * 100
        text = parts[1]
    if "十" in text:
        parts = text.split("十")
        tens = _CN_DIGITS.get(parts[0], 1) if parts[0] else 1
        total += tens * 10
        text = parts[1]
    else:
        text = text.replace("零", "")
    for ch in text:
        if ch in _CN_DIGITS:
            total += _CN_DIGITS[ch]
        else:
            return None
    return total or None


def _strip_punct(text: str) -> str:
    return re.sub(r"[\s\-_/｜|，,。.、:：;；!！?？#*~（）()\[\]【】]+", "", text)


def find_episode(title: str) -> tuple[Optional[int], str]:
    """返回 (第几集, 去掉编号后的系列名)。找不到编号时 ep 为 None"""
    title = (title or "").strip()
    if not title:
        return None, ""
    for pat in _EP_PATTERNS:
        m = pat.search(title)
        if not m:
            continue
        token = m.group(1)
        if token in _POS:
            ep = _POS[token]
        else:
            ep = cn_to_int(token)
        if ep is None or ep > 2000:
            continue
        key = _strip_punct(pat.sub("", title, count=1))
        return ep, key
    return None, _strip_punct(title)


def slugify(name: str) -> str:
    """栏目落盘/CDN 用 ASCII 名，中文进路径容易出事"""
    ascii_part = re.sub(r"[^a-zA-Z0-9]+", "-", name or "").strip("-").lower()[:24]
    tail = hashlib.md5((name or "").encode()).hexdigest()[:6]
    return f"{ascii_part}-{tail}" if ascii_part else f"col-{tail}"


def _display_name(title: str, key: str) -> str:
    """系列名优先用带标点的好看版本：取标题里 key 对应的前半段"""
    t = (title or "").strip()
    if len(t) <= 40:
        return t
    return t[:40].rstrip()


def _put_video_in_column(conn, aweme_id: str, column_id: str, source: str,
                         episode_no: Optional[int]):
    conn.execute(
        """UPDATE videos SET column_id=?, column_source=?,
                  episode_no=COALESCE(?, episode_no)
           WHERE aweme_id=?""",
        (column_id, source, episode_no, aweme_id),
    )


def _sync_column_ids(conn, rows: list[dict]):
    """把库里最新的 column_id 同步回内存。

    各阶段都只改数据库，不同步的话后面的「单篇兜底」会把 AI 刚归栏的作品
    再按旧快照覆盖回去。
    """
    ids = [r["aweme_id"] for r in rows]
    if not ids:
        return
    ph = ",".join("?" * len(ids))
    got = {r["aweme_id"]: r["column_id"] for r in conn.execute(
        f"SELECT aweme_id, column_id FROM videos WHERE aweme_id IN ({ph})",
        ids).fetchall()}
    for r in rows:
        if r["aweme_id"] in got:
            r["column_id"] = got[r["aweme_id"]]


# 重跑时要保留的归栏来源：原生合集、面板里手工挪动的、人工新建的
_KEEP_SOURCES = ("mix", "manual")


def _reset_auto_assignments(conn, rows: list[dict]) -> int:
    """重跑前清空「自动归栏」的结果

    不清的话，上一轮落进单篇桶的作品再也进不了这一轮的 AI 聚类，新采集的
    视频也就永远接不上旧系列。保留三类：原生合集、手工挪动、人工锁定。
    """
    ids = [r["aweme_id"] for r in rows if r["column_id"]]
    if not ids:
        return 0
    ph = ",".join("?" * len(ids))
    keep = ",".join("?" * len(_KEEP_SOURCES))
    cleared = {r["aweme_id"] for r in conn.execute(
        f"""SELECT aweme_id FROM videos
             WHERE aweme_id IN ({ph})
               AND COALESCE(column_source, '') <> 'manual'
               AND column_id NOT IN (
                   SELECT column_id FROM columns
                   WHERE source IN ({keep}) OR locked=1)""",
        ids + list(_KEEP_SOURCES)).fetchall()}
    if not cleared:
        return 0
    conn.execute(
        f"""UPDATE videos SET column_id=NULL, episode_no=NULL, column_source=NULL
             WHERE aweme_id IN ({",".join("?" * len(cleared))})""",
        list(cleared))
    conn.commit()
    for r in rows:
        if r["aweme_id"] in cleared:
            r["column_id"] = None
            r["column_source"] = None
            r["episode_no"] = None
    return len(cleared)


def _prune_empty_columns(conn, secs: set[str], verbose: bool) -> int:
    """删掉本轮没聚到任何作品的自动栏目，避免面板上挂一堆空壳"""
    if not secs:
        return 0
    ph = ",".join("?" * len(secs))
    rows = conn.execute(
        f"""SELECT column_id, name FROM columns
             WHERE sec_user_id IN ({ph})
               AND COALESCE(source, '') NOT IN ('mix', 'manual')
               AND locked = 0
               AND column_id NOT IN (
                   SELECT column_id FROM videos WHERE column_id IS NOT NULL)""",
        tuple(secs)).fetchall()
    for r in rows:
        conn.execute("DELETE FROM columns WHERE column_id=?", (r["column_id"],))
    conn.commit()
    if verbose and rows:
        print(f"  清理空栏目 {len(rows)} 个: "
              + "、".join((r["name"] or "")[:12] for r in rows))
    return len(rows)


def _locked_column_ids(conn) -> set[str]:
    return {r["column_id"] for r in conn.execute(
        "SELECT column_id FROM columns WHERE locked=1")}


def _locked_video_ids(conn) -> set[str]:
    return {r["aweme_id"] for r in conn.execute(
        "SELECT v.aweme_id FROM videos v JOIN columns c ON v.column_id=c.column_id"
        " WHERE c.locked=1")}


def _from_mix(conn, rows: list[dict], verbose: bool) -> int:
    """1. 抖音原生合集"""
    n = 0
    for v in rows:
        if not v["mix_id"]:
            continue
        column_id = f"mix:{v['mix_id']}"
        db.upsert_column(conn, {
            "column_id": column_id,
            "sec_user_id": v["sec_user_id"],
            "name": (v["mix_name"] or "").strip() or f"合集 {v['mix_id']}",
            "slug": slugify(v["mix_name"] or v["mix_id"]),
            "source": "mix",
            "description": "",
            "episode_total": v["ep_total"] or 0,
            "cover_url": v["cover_url"],
        })
        _put_video_in_column(conn, v["aweme_id"], column_id, "mix", v["ep_no"])
        n += 1
    conn.commit()
    if verbose and n:
        print(f"  原生合集命中 {n} 条")
    return n


def _from_titles(conn, rows: list[dict], sec_user_id: str, min_ep: int,
                 verbose: bool) -> int:
    """2. 标题自编号"""
    groups: dict[str, list[dict]] = {}
    for v in rows:
        if v["column_id"]:
            continue
        ep, key = find_episode(v["title"])
        if ep is None or len(key) < 2:
            continue
        groups.setdefault(key, []).append({**v, "_ep": ep, "_key": key})

    n = 0
    for key, items in groups.items():
        if len(items) < min_ep:
            continue
        # 系列内编号必须大体唯一，否则很可能是巧合
        eps = [it["_ep"] for it in items]
        if len(set(eps)) < max(2, int(len(eps) * 0.5)):
            continue
        name = _display_name(items[0]["title"], key)
        column_id = f"title:{hashlib.md5((sec_user_id + key).encode()).hexdigest()[:16]}"
        db.upsert_column(conn, {
            "column_id": column_id,
            "sec_user_id": sec_user_id,
            "name": name,
            "slug": slugify(name),
            "source": "regex",
            "description": "按标题编号自动整理",
            "episode_total": max(eps),
        })
        for it in sorted(items, key=lambda x: x["_ep"]):
            _put_video_in_column(conn, it["aweme_id"], column_id, "regex", it["_ep"])
            n += 1
    conn.commit()
    if verbose and n:
        print(f"  标题编号命中 {n} 条")
    return n


def _ai_cluster(conn, rows: list[dict], sec_user_id: str, min_ep: int,
                verbose: bool, batch: int = 40) -> int:
    """3. qwen-plus 语义聚类"""
    left = [v for v in rows if not v["column_id"] and v["kind"] != "image_text"]
    if not left:
        return 0
    by_id = {v["aweme_id"]: v for v in left}
    assigned = 0
    for i in range(0, len(left), batch):
        chunk = left[i:i + batch]
        listing = "\n".join(f"{v['aweme_id']}|{(v['title'] or '')[:60]}"
                            for v in chunk)
        prompt = (
            "下面是同一个抖音账号的作品，每行格式为「ID|标题」。\n"
            "请把明显属于同一系列（同一本书、同一个人物、同一部史书、同一主题连续讲）"
            "的作品归成栏目。\n"
            "规则：\n"
            "1. 只有凑够 %d 条以上才开栏目，零散的不要硬凑；\n"
            "2. 栏目名用系列本身的中文名，不要带「合集」「栏目」字样；\n"
            "3. 按标题里的集数顺序给 episode_no，标题里没有编号就按出现顺序；\n"
            "4. 一条作品只能进一个栏目；\n"
            "只输出 JSON：{\"columns\":[{\"name\":\"栏目名\",\"items\":"
            "[{\"id\":\"作品ID\",\"episode_no\":1}]}]}\n\n%s" % (min_ep, listing)
        )
        try:
            data = llm.chat_json(prompt, system="你是资深音频栏目的编辑。",
                                 temperature=0.1, max_tokens=3000)
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] AI 聚类批次 {i // batch + 1} 失败: {e}")
            continue

        for col in (data or {}).get("columns") or []:
            name = (col.get("name") or "").strip()
            items = [it for it in (col.get("items") or [])
                     if isinstance(it, dict) and it.get("id") in by_id]
            if not name or len(items) < min_ep:
                continue
            column_id = "ai:" + hashlib.md5(
                (sec_user_id + name).encode()).hexdigest()[:16]
            prev = conn.execute("SELECT episode_total FROM columns WHERE column_id=?",
                                 (column_id,)).fetchone()
            db.upsert_column(conn, {
                "column_id": column_id,
                "sec_user_id": sec_user_id,
                "name": name[:40],
                "slug": slugify(name),
                "source": "ai",
                "description": "AI 按标题聚类",
                "episode_total": max(len(items),
                                     int(prev["episode_total"] or 0) if prev else 0),
            })
            ordered = sorted(items, key=lambda x: int(x.get("episode_no") or 0))
            for idx, it in enumerate(ordered, start=1):
                ep = int(it.get("episode_no") or idx)
                _put_video_in_column(conn, it["id"], column_id, "ai", ep)
                assigned += 1
        conn.commit()
        if verbose:
            print(f"  AI 聚类 {i + len(chunk)}/{len(left)} 条，累计成栏 {assigned}")
    return assigned


def _single_bucket(conn, rows: list[dict], sec_user_id: str, acc_name: str,
                   verbose: bool) -> int:
    """4. 兜底单篇桶"""
    left = [v for v in rows if not v["column_id"]]
    if not left:
        return 0
    column_id = f"single:{sec_user_id}"
    db.upsert_column(conn, {
        "column_id": column_id,
        "sec_user_id": sec_user_id,
        "name": f"{acc_name} · 单篇",
        "slug": slugify(f"{acc_name}-single"),
        "source": "single",
        "description": "未构成系列的独立作品",
        "episode_total": 0,
        "sort": 900,
    })
    for v in sorted(left, key=lambda x: -(x["create_time"] or 0)):
        _put_video_in_column(conn, v["aweme_id"], column_id, "single", None)
    conn.commit()
    if verbose:
        print(f"  单篇兜底 {len(left)} 条")
    return len(left)


def _refresh_footers(conn, verbose: bool = True) -> int:
    """把已生成正文里的「- 栏目：」尾注回填成最新归栏结果

    栏目多半是在文章写完之后才聚好的，不回填的话前端正文会一直显示「单篇」，
    而这里只改一行，不用重跑模型烧 token。
    """
    from article_formatter import refresh_column_line

    rows = conn.execute(
        """SELECT v.aweme_id, v.episode_no, c.name
           FROM videos v LEFT JOIN columns c ON c.column_id = v.column_id
           WHERE COALESCE(v.article_path, '') <> ''""").fetchall()
    n = 0
    for r in rows:
        name, ep = r["name"], r["episode_no"]
        where = f"{name} · 第 {ep} 集" if name and ep else (name or "单篇")
        if refresh_column_line(r["aweme_id"], where):
            n += 1
    if verbose and n:
        print(f"  回填正文栏目尾注 {n} 篇")
    return n


def build_columns(conn=None, sec_user_id: Optional[str] = None,
                  use_ai: bool = True, min_ep: int = 3, verbose: bool = True) -> dict:
    """给账号下所有作品分栏目。返回统计"""
    own = conn is None
    conn = conn or db.connect()
    locked_cols = _locked_column_ids(conn)
    locked_videos = _locked_video_ids(conn)
    out = {"mix": 0, "regex": 0, "ai": 0, "single": 0, "reset": 0, "pruned": 0,
           "footers": 0, "protected": len(locked_videos)}

    sql = "SELECT * FROM videos"
    params: tuple = ()
    if sec_user_id:
        sql += " WHERE sec_user_id=?"
        params = (sec_user_id,)
    rows = [dict(r) for r in conn.execute(
        sql + " ORDER BY create_time DESC", params).fetchall()]
    rows = [r for r in rows if r["aweme_id"] not in locked_videos]

    # 自动归栏每次重跑都先清空，否则上一轮落进「单篇」的作品永远进不了
    # 这一轮的 AI 聚类。原生合集 / 手工挪动 / 人工锁定的一律保留。
    out["reset"] = _reset_auto_assignments(conn, rows)

    accs = {a["sec_user_id"]: a for a in db.list_accounts(conn)}
    per_acc: dict[str, list[dict]] = {}
    for r in rows:
        per_acc.setdefault(r["sec_user_id"], []).append(r)

    for sec, items in per_acc.items():
        acc = accs.get(sec, {})
        name = acc.get("nickname") or acc.get("name") or sec[:12]
        if verbose:
            print(f"\n[{name}] 整理 {len(items)} 条")
        # 合集列名如果和 locked 冲突就跳过
        mix_items = [v for v in items if v["mix_id"]]
        for v in mix_items:
            if f"mix:{v['mix_id']}" in locked_cols:
                locked_videos.add(v["aweme_id"])
        mix_items = [v for v in mix_items if f"mix:{v['mix_id']}" not in locked_cols]
        out["mix"] += _from_mix(conn, mix_items, verbose)

        _sync_column_ids(conn, items)
        rest = [v for v in items if not v["column_id"] and not v["mix_id"]]
        out["regex"] += _from_titles(conn, rest, sec, min_ep, verbose)
        _sync_column_ids(conn, rest)
        if use_ai:
            out["ai"] += _ai_cluster(conn, rest, sec, min_ep, verbose)
            _sync_column_ids(conn, rest)
        out["single"] += _single_bucket(conn, rest, sec, name, verbose)

    out["pruned"] = _prune_empty_columns(conn, set(per_acc), verbose)
    out["footers"] = _refresh_footers(conn, verbose)

    if own:
        conn.close()
    out["llm"] = llm.summary()
    return out


def reassign(conn, aweme_id: str, column_id: Optional[str],
             episode_no: Optional[int] = None):
    """面板里手工把某条作品挪进别的栏目"""
    conn.execute("UPDATE videos SET column_id=?, episode_no=?, column_source='manual'"
                 " WHERE aweme_id=?", (column_id, episode_no, aweme_id))
    conn.commit()


def update_column(conn, column_id: str, **fields) -> dict:
    allowed = ("name", "slug", "description", "sort", "episode_total", "locked")
    sets, vals = [], []
    for k in allowed:
        if k in fields:
            sets.append(f"{k}=?")
            vals.append(int(fields[k]) if k in ("sort", "episode_total", "locked")
                        else fields[k])
    if sets:
        vals.append(column_id)
        conn.execute(f"UPDATE columns SET {', '.join(sets)} WHERE column_id=?", vals)
        conn.commit()
    row = conn.execute("SELECT * FROM columns WHERE column_id=?",
                       (column_id,)).fetchone()
    return dict(row) if row else {}


def create_column(conn, sec_user_id: str, name: str, slug: str = "") -> dict:
    column_id = "manual:" + hashlib.md5(
        (sec_user_id + name + db.now()).encode()).hexdigest()[:16]
    col = {"column_id": column_id, "sec_user_id": sec_user_id, "name": name,
           "slug": slug or slugify(name), "source": "manual",
           "description": "", "episode_total": 0, "sort": 100, "locked": 1}
    db.upsert_column(conn, col)
    conn.commit()
    return col


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="作品栏目整理")
    ap.add_argument("--sec", help="只处理指定账号")
    ap.add_argument("--no-ai", action="store_true", help="跳过 AI 聚类（省钱）")
    ap.add_argument("--min-ep", type=int, default=3, help="几集起立栏目")
    args = ap.parse_args()
    config.ensure_dirs()
    print(json.dumps(build_columns(sec_user_id=args.sec, use_ai=not args.no_ai,
                                   min_ep=args.min_ep),
                     ensure_ascii=False, indent=2))
