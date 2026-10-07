"""文章排版的回归测试

踩过的坑：模型爱用 ‘ ’ 包中文术语，大陆规范应该是 “ ”；
渲染出来的 Markdown 版式（标题/导语/分节/要点/金句）也是前端解析的依据，
不能随便漂。正文里不写栏目/时长/原视频这类尾注，界面上已经有了。
全部本地跑，不联网。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import article_formatter as af  # noqa: E402


def test_tidy_text_uses_fullwidth_double_quotes():
    out = af.tidy_text("他说‘真聪明’不如说“努力”")
    assert "\u2018" not in out and "\u2019" not in out
    assert out == "他说“真聪明”不如说“努力”"


def test_tidy_text_collapses_blank_lines_and_trailing_space():
    assert af.tidy_text("  第一段   \n\n\n\n第二段 ") == "第一段\n\n第二段"


def test_tidy_text_handles_empty():
    assert af.tidy_text("") == ""
    assert af.tidy_text(None) == ""


def test_render_markdown_layout_and_typography():
    md = af.render_markdown({
        "title": "读《史记》",
        "lead": "导语一句话",
        "sections": [{"heading": "  开头  ", "paragraphs": ["段落一‘术语’", "", "段落二"]}],
        "takeaways": ["要点一", "  "],
        "quotes": ["金句原文"],
    }, {"title": "兜底标题", "duration_ms": 420000, "source_url": "https://x/1",
        "column_name": "大风歌", "episode_no": 3})

    lines = [ln for ln in md.splitlines() if ln]
    assert lines[0] == "# 读《史记》"
    assert lines[1] == "> 导语一句话"
    assert "## 开头" in lines
    assert "段落一“术语”" in lines
    assert "## 本期要点" in lines and "- 要点一" in lines
    assert "## 值得记住的一句" in lines and "> 金句原文" in lines
    assert "栏目" not in md and "时长" not in md and "原视频" not in md
    assert "---" not in md
    assert lines[-1] == "> 金句原文"
    assert md.endswith("\n") and not md.endswith("\n\n")


def test_strip_footer_removes_legacy_tail_note():
    """存量文章末尾那三行是旧版留下的，前端已经不再展示，迁移时得抹干净"""
    old = ("# 标题\n\n> 导语\n\n## 一\n\n正文。\n\n## 值得记住的一句\n\n"
           "> 金句\n\n---\n\n- 栏目：大风歌 · 第 3 集\n- 时长：34 分钟\n"
           "- 原视频：https://www.douyin.com/video/123\n")
    out = af.strip_footer(old)
    assert out == "# 标题\n\n> 导语\n\n## 一\n\n正文。\n\n## 值得记住的一句\n\n> 金句\n"


def test_strip_footer_keeps_body_divider_and_lists():
    """正文中段的分隔线和要点列表不能被误伤，只认文件最末"""
    body = "## 本期要点\n\n- 要点一\n- 要点二\n\n---\n\n## 收尾\n\n正文。\n"
    assert af.strip_footer(body) == body


def test_strip_footer_on_clean_text_is_noop():
    assert af.strip_footer("") == ""
    one = "# T\n\n正文。\n"
    assert af.strip_footer(one) == one
