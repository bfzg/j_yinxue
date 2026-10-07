"""
文章排版：ASR 逐句稿 → 可直接发布的 Markdown

模型只做「整理」不做「创作」：修错别字、去口头禅、按语义分节拟小标题、
提炼要点与金句。为避免幻觉，明确要求不得补充原文没有的信息。
抖音官方 chapter_list 是最可靠的分节骨架，优先喂给模型。
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
import config
import llm

# 模型爱用 ‘ ’ 包术语和引语，中文规范应该是 “ ”，成文时统一收口
_QUOTE_FIX = {"\u2018": "\u201c", "\u2019": "\u201d",
              "\u300c": "\u201c", "\u300d": "\u201d"}


def tidy_text(text: str) -> str:
    """中文排版收口：统一引号、去行尾空格、连续空行压成一个"""
    if not text:
        return ""
    out = str(text).strip()
    for bad, good in _QUOTE_FIX.items():
        out = out.replace(bad, good)
    lines: list[str] = []
    for ln in (line.rstrip() for line in out.splitlines()):
        if not ln and lines and not lines[-1]:
            continue
        lines.append(ln)
    return "\n".join(lines).strip()


_SYSTEM = ('你是中文音频节目的文字编辑，负责把语音转写稿整理成适合手机阅读的文章。'
           '你的纪律：只整理，不创作。不得增删观点，不得补充原文没有的事实、数据、'
           '名人名言或背景知识。遇到明显同音错字按上下文改正，拿不准就保留原词。')

_JSON_GUARD = (
    '严格输出 JSON，字段固定为：'
    '{"title":"文章标题","lead":"80字以内导语",'
    '"sections":[{"heading":"小标题","paragraphs":["段落","段落"]}],'
    '"takeaways":["要点","要点"],"quotes":["原文金句"]}。'
    '要求：sections 3-7 节；takeaways 3-5 条；quotes 1-3 条且必须是原文表述；'
    '段落每段 60-200 字；删掉「嗯」「啊」这类口头禅；'
    '正文里的引号一律用“”，不要用‘’；JSON 之外不要输出任何文字。'
)


def article_path(aweme_id: str) -> Path:
    return config.ARTICLES_DIR / f"{aweme_id}.md"


def meta_path(aweme_id: str) -> Path:
    return config.ARTICLES_DIR / f"{aweme_id}.article.json"


# 旧版正文末尾会写「- 栏目：」「- 时长：」「- 原视频：」三行尾注，现在不留了，
# 存量文章用 strip_footer 一次性抹掉。只认文件最末，正文中段的分隔线不动。
_FOOTER_BULLETS = re.compile(r"(?:\n\s*-\s*(?:栏目|时长|原视频)[：:][^\n]*)+\Z")
_FOOTER_RULE = re.compile(r"\n\s*-{3,}[ \t]*\Z")


def strip_footer(text: str) -> str:
    """去掉正文末尾的旧尾注（栏目 / 时长 / 原视频）"""
    if not text:
        return text
    body = _FOOTER_BULLETS.sub("", text.rstrip())
    body = _FOOTER_RULE.sub("", body)
    return body.rstrip() + "\n" if body.strip() else ""


def strip_footers(conn=None, *, apply_changes: bool = True,
                  verbose: bool = True) -> dict:
    """一次性迁移：抹掉存量文章的旧尾注，改过的文件 mtime 会变新

    mtime 一变，cloud_release 就认不出「已经推过」，下次发布自动重推正文。
    """
    if conn is not None:
        rows = conn.execute(
            "SELECT article_path FROM videos "
            "WHERE COALESCE(article_path, '') <> ''").fetchall()
        files = [Path(r["article_path"]) for r in rows]
    else:
        files = sorted(config.ARTICLES_DIR.glob("*.md"))

    changed: list[str] = []
    for p in files:
        if not p.exists():
            continue
        old = p.read_text(encoding="utf-8")
        new = strip_footer(old)
        if new == old:
            continue
        changed.append(p.name)
        if apply_changes:
            p.write_text(new, encoding="utf-8")
    if verbose and changed:
        print(f"  清理正文尾注 {len(changed)} 篇"
              + ("" if apply_changes else "（dry-run，未写盘）"))
    return {"files": len(files), "cleaned": len(changed),
            "applied": apply_changes, "examples": changed[:10]}


def _read_transcript(aweme_id: str) -> tuple[str, list]:
    jp = config.SUBTITLES_DIR / f"{aweme_id}.json"
    if jp.exists():
        sentences = json.loads(jp.read_text(encoding="utf-8")).get("sentences") or []
        text = "\n".join((s.get("text") or "").strip() for s in sentences
                          if (s.get("text") or "").strip())
        return text, sentences
    tp = config.SUBTITLES_DIR / f"{aweme_id}.txt"
    if tp.exists():
        raw = tp.read_text(encoding="utf-8")
        return "\n".join(re.sub(r"^\[[^\]]*\]\[[^\]]*\]\s*", "", line)
                          for line in raw.splitlines()), []
    raise FileNotFoundError(f"没有转写稿: {jp}")


def _chapter_hint(chapters_json: Optional[str]) -> str:
    """官方章节的措辞直接当小标题候选"""
    try:
        chapters = json.loads(chapters_json or "[]")
    except Exception:
        return ""
    lines = [f"{i + 1}. {c.get('title', '')}" for i, c in enumerate(chapters[:12])
             if (c.get("title") or "").strip()]
    if not lines:
        return ""
    return ("原视频官方章节（小标题优先沿用这个顺序和措辞，可轻微改写）：\n"
            + "\n".join(lines) + "\n\n")


def render_markdown(data: dict, video: dict) -> str:
    """固定版式：标题 / 导语 / 分节正文 / 本期要点 / 金句

    正文到金句为止，栏目、时长、原视频不再写进正文，交给界面展示。
    """
    title = tidy_text(data.get("title") or video.get("title") or "未命名")
    parts: list[str] = [f"# {title}", ""]

    lead = tidy_text(data.get("lead") or "")
    if lead:
        parts += [f"> {lead}", ""]

    for i, sec in enumerate(data.get("sections") or [], start=1):
        heading = tidy_text(sec.get("heading") or str(i))
        parts += [f"## {heading}", ""]
        for para in sec.get("paragraphs") or []:
            para = tidy_text(para or "")
            if para:
                parts += [para, ""]

    takeaways = [t for t in (tidy_text(x) for x in (data.get("takeaways") or [])) if t]
    if takeaways:
        parts += ["## 本期要点", ""] + [f"- {t}" for t in takeaways] + [""]

    quotes = [q for q in (tidy_text(x) for x in (data.get("quotes") or [])) if q]
    if quotes:
        parts += ["## 值得记住的一句", ""] + [f"> {q}" for q in quotes] + [""]

    return tidy_text("\n".join(parts)) + "\n"


def build_article(aweme_id: str, video: dict, force: bool = False,
                  verbose: bool = True) -> dict:
    """生成一篇文章。幂等：已有 .md 与 .article.json 时默认跳过"""
    md_path, mj = article_path(aweme_id), meta_path(aweme_id)
    if md_path.exists() and mj.exists() and not force:
        return {"path": str(md_path),
                "meta": json.loads(mj.read_text(encoding="utf-8")), "cached": True}

    text, _ = _read_transcript(aweme_id)
    if len(text) < 30:
        raise RuntimeError(f"转写稿过短（{len(text)} 字），不足以成文")
    clipped = text[: config.ARTICLE_MAX_CHARS]

    prompt = (
        f"【栏目】{video.get('column_name') or '未归栏'}"
        f"（第 {video.get('episode_no') or '?'} 集）\n"
        f"【账号定位】{video.get('style') or ''}\n"
        f"【原视频标题】{video.get('title') or ''}\n"
        f"【转写稿字数】{len(clipped)}\n\n"
        f"{_chapter_hint(video.get('chapters'))}"
        f"【转写稿】\n{clipped}\n\n{_JSON_GUARD}"
    )
    if verbose:
        print(f"  [排版] {len(clipped)} 字 → {config.BAILIAN_MODEL}")

    data = llm.chat_json(prompt, system=_SYSTEM, temperature=0.4, max_tokens=6000)
    if not isinstance(data, dict) or not data.get("sections"):
        raise RuntimeError(f"模型返回结构不对: {str(data)[:200]}")

    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_markdown(data, video), encoding="utf-8")

    body_words = sum(len(p) for s in data.get("sections") or []
                     for p in (s.get("paragraphs") or []))
    meta = {
        "aweme_id": aweme_id,
        "title": tidy_text(data.get("title") or video.get("title") or ""),
        "lead": tidy_text(data.get("lead") or ""),
        "n_sections": len(data.get("sections") or []),
        "word_count": body_words,
        "source_chars": len(clipped),
        "truncated": len(text) > len(clipped),
        "model": config.BAILIAN_MODEL,
        "llm": llm.summary(),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    mj.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"path": str(md_path), "meta": meta, "cached": False}


def enrich_video(conn, v: dict) -> dict:
    """把栏目名与账号定位补进 video，供提示词使用"""
    if v.get("column_id"):
        row = conn.execute("SELECT name FROM columns WHERE column_id=?",
                           (v["column_id"],)).fetchone()
        v["column_name"] = row["name"] if row else ""
    acc = conn.execute("SELECT name, style FROM accounts WHERE sec_user_id=?",
                       (v["sec_user_id"],)).fetchone()
    if acc:
        v["style"] = acc["style"] or acc["name"]
    return v


if __name__ == "__main__":
    import argparse
    import pipeline_db as db
    ap = argparse.ArgumentParser(description="生成文章")
    ap.add_argument("aweme_id")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    config.ensure_dirs()
    conn = db.connect()
    row = db.get_video(conn, args.aweme_id)
    if not row:
        raise SystemExit(f"数据库里没有这条作品: {args.aweme_id}")
    print(json.dumps(build_article(args.aweme_id, enrich_video(conn, row),
                                   force=args.force),
                     ensure_ascii=False, indent=2))
