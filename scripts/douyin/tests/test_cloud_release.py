"""发布链路的回归测试：栏目内集号 + 下架状态必须扛得住下一次上架

不联网：云函数调用全部打桩。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SEC = "MS4wLjABAAAA" + "a" * 46
COL = "col-num"


@pytest.fixture
def conn(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "DB_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_FILE", tmp_path / "test.sqlite")
    import pipeline_db as db
    c = db.connect()
    db.upsert_account(c, {"sec_user_id": SEC, "slug": "alpha", "name": "甲号", "style": "认知"})
    db.upsert_column(c, {"column_id": COL, "sec_user_id": SEC, "name": "隐学精选",
                         "slug": "col-num", "source": "ai"})
    # 集号沿用采集时账号作品列表的位置，正是线上曾经显示 275/331/357 的那种脏数据
    for i, (aweme, ep_no) in enumerate([("V0275", 275), ("V0331", 331), ("V0357", 357)]):
        db.upsert_video(c, {"aweme_id": aweme, "sec_user_id": SEC, "title": f"第{i}篇",
                            "create_time": 1_700_000_000 + i, "duration_ms": 60_000,
                            "kind": "video"})
        db.set_stage(c, aweme, "published")
        c.execute("UPDATE videos SET column_id=?, episode_no=?, column_source='ai' "
                  "WHERE aweme_id=?", (COL, ep_no, aweme))
    c.commit()
    yield c
    c.close()


def test_episode_no_is_renumbered_inside_the_column(conn):
    import cloud_release as cr

    rows = {r["id"]: r for r in cr.episode_rows(conn)}
    assert [rows[i]["episodeNo"] for i in ("V0275", "V0331", "V0357")] == [1, 2, 3]
    # 云存储文件名仍用原始编号，改编号不会把已上传的音频搞丢
    assert cr.cloud_paths(conn, {"aweme_id": "V0275", "column_id": COL,
                                 "episode_no": 275, "sec_user_id": SEC},
                          Path("a.mp3"))[0].endswith("audio/ep275_V0275.mp3")


def test_offline_survives_the_next_release(conn, monkeypatch):
    import cloud_client as cc
    import cloud_release as cr
    import pipeline_db as db

    calls = []
    monkeypatch.setattr(cc, "call_content",
                        lambda action, **kw: calls.append(action) or {"written": 3, "removed": 1})
    monkeypatch.setattr(cc, "batches", lambda items, size=120: [items])

    cr.offline(conn, ["V0331"])
    assert db.get_video(conn, "V0331")["offline"] == 1

    # 再点一次「同步元数据」，下架的集不能被重新点亮，栏目引用里也不该有它
    cr.push_meta(conn)
    eps = {r["id"]: r for r in cr.episode_rows(conn)}
    assert "V0331" not in eps, "已下架的集又被推上线了"
    cols = {r["id"]: r for r in cr.column_rows(conn)}
    assert cols[COL]["episodeIds"] == ["V0275", "V0357"]
    assert [eps[i]["episodeNo"] for i in ("V0275", "V0357")] == [1, 2]


def test_restore_brings_episodes_back(conn, monkeypatch):
    import cloud_client as cc
    import cloud_release as cr
    import pipeline_db as db

    monkeypatch.setattr(cc, "call_content", lambda action, **kw: {"written": 3, "total": 3})
    cr.offline(conn, ["V0331"])
    assert db.get_video(conn, "V0331")["offline"] == 1

    res = cr.restore(conn, ["V0331"])
    assert res["restored"] == 1
    assert db.get_video(conn, "V0331")["offline"] == 0
    assert len(cr.column_rows(conn)[0]["episodeIds"]) == 3


def test_reconcile_picks_up_offline_done_before_the_flag_existed(conn, monkeypatch):
    """老数据：线上 enabled=false，本地 stage 还是 published 且没有 offline 标记"""
    import cloud_client as cc
    import cloud_release as cr
    import pipeline_db as db

    monkeypatch.setattr(cc, "configured", lambda: True)
    monkeypatch.setattr(cr, "remote_enabled",
                        lambda **kw: {"V0275": True, "V0331": False, "V0357": False})
    res = cr.reconcile_offline(conn)
    assert (res["offline"], res["online"]) == (2, 0)
    assert db.get_video(conn, "V0331")["offline"] == 1
    assert db.get_video(conn, "V0275")["offline"] == 0
    assert len(cr.column_rows(conn)[0]["episodeIds"]) == 1
