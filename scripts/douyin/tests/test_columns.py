"""栏目聚类的回归测试

重点是那几个真金白银踩过坑的地方：
  - AI 刚归栏的作品不能被随后的「单篇兜底」覆盖回去
  - 重跑时自动归栏先清空，但原生合集 / 手工挪动 / 人工锁定必须保住
  - 没聚到作品的空壳栏目要剪掉，不能留在面板上
全部用假的 LLM 返回值，不联网、不花钱。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SEC_A = "MS4wLjABAAAA" + "a" * 46
SEC_B = "MS4wLjABAAAA" + "b" * 46

AI_TITLES = ["司马谈的临终遗命", "白起到底该不该杀", "吕不韦的奇货可居",
             "秦始皇的接班人困局", "李斯的眼泪", "蒙恬的最后一战"]
AI_PICKS = AI_TITLES[:4]


@pytest.fixture
def conn(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "DB_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_FILE", tmp_path / "test.sqlite")
    import pipeline_db as db
    c = db.connect()
    db.upsert_account(c, {"sec_user_id": SEC_A, "slug": "alpha",
                          "name": "甲号", "style": "历史"})
    db.upsert_account(c, {"sec_user_id": SEC_B, "slug": "beta",
                          "name": "乙号", "style": "认知"})
    c.commit()
    yield c
    c.close()


def seed(conn, sec: str, titles: list[str], start_ts: int = 1_700_000_000,
         mix=None):
    """按标题造几条作品，aweme_id 直接用序号，方便断言"""
    import pipeline_db as db
    prefix = "A" if sec == SEC_A else "B"
    for i, t in enumerate(titles, start=1):
        v = {"aweme_id": f"{prefix}{i:04d}", "sec_user_id": sec, "title": t,
             "create_time": start_ts - i * 60, "duration_ms": 60_000,
             "kind": "video"}
        if mix:
            v["mix_id"], v["mix_name"] = mix
            v["ep_no"], v["ep_total"] = i, len(titles)
        db.upsert_video(conn, v)
    conn.commit()


def fake_ai(monkeypatch, groups: dict):
    """让 LLM 按 groups 归栏：只挑当前 prompt 里出现过的标题"""
    import columns as colmod
    calls: list[str] = []

    def chat_json(prompt, system="", temperature=0.2, max_tokens=4000):
        calls.append(prompt)
        cols = []
        for name, titles in groups.items():
            items = [{"id": _id_of(prompt, t), "episode_no": no}
                     for no, t in enumerate(titles, start=1)
                     if f"|{t}" in prompt]
            if items:
                cols.append({"name": name, "items": items})
        return {"columns": cols}

    monkeypatch.setattr(colmod.llm, "chat_json", chat_json)
    return calls


def _id_of(prompt: str, title: str) -> str:
    for line in prompt.splitlines():
        if line.endswith("|" + title):
            return line.split("|", 1)[0]
    raise AssertionError(f"标题没出现在 prompt 里: {title}")


def column_of(conn, aweme_id: str) -> dict:
    row = conn.execute(
        """SELECT v.column_id, v.column_source, v.episode_no, c.name, c.source
           FROM videos v LEFT JOIN columns c ON c.column_id=v.column_id
           WHERE v.aweme_id=?""", (aweme_id,)).fetchone()
    return dict(row)


# ---------- 原生合集 ----------

def test_mix_column_is_kept_across_reruns(conn, monkeypatch):
    import columns as colmod
    seed(conn, SEC_A, ["大风歌 开场", "大风歌 后续", "大风歌 收束"],
         mix=("7627866019750479899", "《王立群大风歌》"))
    fake_ai(monkeypatch, {})

    out = colmod.build_columns(conn, SEC_A, use_ai=False, verbose=False)
    assert out["mix"] == 3
    row = column_of(conn, "A0001")
    assert row["column_source"] == "mix"
    assert row["name"] == "《王立群大风歌》"

    # 重跑：合集归栏不能被清空后再随机分配
    out2 = colmod.build_columns(conn, SEC_A, use_ai=False, verbose=False)
    assert out2["reset"] == 0
    assert out2["mix"] == 3
    assert column_of(conn, "A0003")["column_source"] == "mix"


# ---------- 标题正则 ----------

def test_title_numbering_builds_column(conn, monkeypatch):
    import columns as colmod
    seed(conn, SEC_A, [f"王立群讲史记 第{n}章" for n in ("一", "二", "三", "四")])
    fake_ai(monkeypatch, {})

    out = colmod.build_columns(conn, SEC_A, use_ai=True, verbose=False)
    assert out["regex"] == 4
    assert out["single"] == 0
    eps = [column_of(conn, f"A{i:04d}")["episode_no"] for i in range(1, 5)]
    assert eps == [1, 2, 3, 4]
    assert column_of(conn, "A0002")["column_source"] == "regex"


# ---------- AI 聚类：核心回归 ----------

def test_ai_column_survives_single_bucket(conn, monkeypatch):
    import columns as colmod
    seed(conn, SEC_A, AI_TITLES)
    fake_ai(monkeypatch, {"大风歌": AI_PICKS})

    out = colmod.build_columns(conn, SEC_A, use_ai=True, min_ep=3, verbose=False)
    assert out["ai"] == 4
    assert out["single"] == 2
    for i, title in enumerate(AI_PICKS, start=1):
        row = column_of(conn, f"A{i:04d}")
        assert row["column_source"] == "ai", title
        assert row["name"] == "大风歌"
        assert row["episode_no"] == i
    for i in (5, 6):
        assert column_of(conn, f"A{i:04d}")["column_source"] == "single"


def test_ai_assignment_is_stable_on_rerun(conn, monkeypatch):
    """上一版就是这里翻车：AI 归好的栏被单篇桶覆盖回去"""
    import columns as colmod
    seed(conn, SEC_A, AI_TITLES)
    calls = fake_ai(monkeypatch, {"大风歌": AI_PICKS})

    colmod.build_columns(conn, SEC_A, use_ai=True, verbose=False)
    before = {f"A{i:04d}": column_of(conn, f"A{i:04d}") for i in range(1, 7)}
    calls.clear()
    out = colmod.build_columns(conn, SEC_A, use_ai=True, verbose=False)

    # 自动归栏（AI 4 条 + 单篇 2 条）重跑前全部清空重算
    assert out["reset"] == 6
    assert out["ai"] == 4
    assert out["single"] == 2
    assert all(f"|{t}" in calls[0] for t in AI_PICKS), "清空后 AI 要能重新看到这些标题"
    for k, v in before.items():
        assert column_of(conn, k) == v, k


def test_empty_ai_column_is_pruned(conn, monkeypatch):
    import columns as colmod
    seed(conn, SEC_A, AI_TITLES)
    fake_ai(monkeypatch, {"大风歌": AI_PICKS})
    colmod.build_columns(conn, SEC_A, use_ai=True, verbose=False)
    assert conn.execute("SELECT COUNT(*) FROM columns").fetchone()[0] == 2

    # 这一轮模型什么都不归，全部落单篇 → 空壳 AI 栏目必须消失
    fake_ai(monkeypatch, {})
    out = colmod.build_columns(conn, SEC_A, use_ai=True, verbose=False)
    assert out["ai"] == 0
    assert out["pruned"] == 1
    assert conn.execute("SELECT COUNT(*) FROM columns WHERE source='ai'"
                        ).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM videos WHERE column_id IS NULL"
                        ).fetchone()[0] == 0


def test_other_accounts_are_not_pruned(conn, monkeypatch):
    import columns as colmod
    seed(conn, SEC_A, AI_TITLES)
    seed(conn, SEC_B, ["独自一人的碎碎念"])
    fake_ai(monkeypatch, {"大风歌": AI_PICKS})
    colmod.build_columns(conn, SEC_A, use_ai=True, verbose=False)
    names = {r[0] for r in conn.execute("SELECT name FROM columns")}
    assert "大风歌" in names
    assert "乙号 · 单篇" not in names  # 没处理到的账号不能被牵连


# ---------- 人工干预 ----------

def test_locked_column_is_never_touched(conn, monkeypatch):
    import columns as colmod
    seed(conn, SEC_A, AI_TITLES)
    fake_ai(monkeypatch, {"大风歌": AI_PICKS})
    colmod.build_columns(conn, SEC_A, use_ai=True, verbose=False)
    col_id = column_of(conn, "A0001")["column_id"]
    colmod.update_column(conn, col_id, locked=1)

    fake_ai(monkeypatch, {})
    out = colmod.build_columns(conn, SEC_A, use_ai=True, verbose=False)
    assert out["protected"] == 4
    assert out["pruned"] == 0
    for i in range(1, 5):
        row = column_of(conn, f"A{i:04d}")
        assert row["column_id"] == col_id and row["column_source"] == "ai"
    for i in (5, 6):
        assert column_of(conn, f"A{i:04d}")["column_source"] == "single"


def test_manual_reassign_survives(conn, monkeypatch):
    import columns as colmod
    seed(conn, SEC_A, AI_TITLES)
    fake_ai(monkeypatch, {"大风歌": AI_PICKS})
    colmod.build_columns(conn, SEC_A, use_ai=True, verbose=False)
    target = colmod.create_column(conn, SEC_A, "我的系列")["column_id"]
    # 手工挪动不参与重跑清空
    colmod.reassign(conn, "A0006", target, 1)

    out = colmod.build_columns(conn, SEC_A, use_ai=True, verbose=False)
    assert column_of(conn, "A0006")["column_source"] == "manual"
    assert out["single"] == 1

    fake_ai(monkeypatch, {})
    colmod.build_columns(conn, SEC_A, use_ai=True, verbose=False)
    assert column_of(conn, "A0006")["column_id"] == target


# ---------- 纯规则模式（省钱路径） ----------

def test_no_ai_falls_back_to_single(conn, monkeypatch):
    import columns as colmod
    seed(conn, SEC_A, AI_TITLES)

    def boom(*a, **k):
        raise AssertionError("use_ai=False 不该调模型")

    monkeypatch.setattr(colmod.llm, "chat_json", boom)
    out = colmod.build_columns(conn, SEC_A, use_ai=False, verbose=False)
    assert out["ai"] == 0 and out["single"] == 6
