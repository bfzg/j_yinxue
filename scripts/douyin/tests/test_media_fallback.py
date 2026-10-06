"""直链取不到时的降级顺序：f2 接口 → 浏览器兜底 → 才认定 Cookie 失效。

回归背景：批量跑到 85/112 时，一条作品的 f2 详情接口返回 403，
旧代码直接抛 CookieInvalid 把整批掐断，浏览器兜底根本没机会跑。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
import inventory as inv  # noqa: E402
import media  # noqa: E402

GOOD = {"audio_urls": ["https://example.com/a.m4a"], "video_urls": []}


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """把网络与转码都换掉，只留下 ensure_audio 的分支逻辑"""
    target = tmp_path / "1.audio.mp3"
    monkeypatch.setattr(config, "MEDIA_SOURCE", "auto", raising=False)
    monkeypatch.setitem(media._F2_COOLDOWN, "fails", 0)
    monkeypatch.setitem(media._F2_COOLDOWN, "until", 0.0)
    # 每条作品各自的落盘路径，否则第二次调用会被「已有音频」直接短路
    monkeypatch.setattr(media, "audio_path",
                        lambda aweme_id: target if aweme_id == "1"
                        else tmp_path / f"{aweme_id}.mp3")
    monkeypatch.setattr(media, "_tmp", lambda name: tmp_path / name)

    def fake_download(url, dest, timeout=180):
        dest.write_bytes(b"x" * 2048)
        return True

    def fake_to_mp3(src, aweme_id):
        target.write_bytes(b"ID3" + b"0" * 20000)
        return target

    monkeypatch.setattr(media, "_download", fake_download)
    monkeypatch.setattr(media, "_to_mp3", fake_to_mp3)
    return target


def _raise_403(_aweme_id):
    raise RuntimeError("HTTP状态码错误： Status Code: 403")


def test_single_403_falls_back_to_browser_instead_of_aborting_the_batch(wired, monkeypatch):
    monkeypatch.setattr(inv, "resolve_media", _raise_403)
    calls = []

    def fake_browser(aweme_id):
        calls.append(aweme_id)
        return dict(GOOD)

    monkeypatch.setattr(media, "_resolve_via_browser", fake_browser)

    out = media.ensure_audio("123", verbose=False)

    assert out == wired
    assert calls == ["123"], "浏览器兜底必须被调用"


def test_both_paths_dead_is_reported_as_cookie_failure(wired, monkeypatch):
    monkeypatch.setattr(inv, "resolve_media", _raise_403)
    monkeypatch.setattr(media, "_resolve_via_browser", lambda aweme_id: None)

    with pytest.raises(inv.CookieInvalid):
        media.ensure_audio("123", verbose=False)


def test_ordinary_error_stays_a_plain_runtime_error(wired, monkeypatch):
    """非登录类失败不该被升级成 CookieInvalid，否则一条坏作品能停掉整批"""
    monkeypatch.setattr(inv, "resolve_media",
                        lambda _id: (_ for _ in ()).throw(RuntimeError("connection reset")))
    monkeypatch.setattr(media, "_resolve_via_browser", lambda aweme_id: None)

    with pytest.raises(RuntimeError) as exc:
        media.ensure_audio("123", verbose=False)

    assert not isinstance(exc.value, inv.CookieInvalid)
    assert "取直链失败" in str(exc.value)


def test_repeated_403_engages_the_cooldown(wired, monkeypatch):
    """连撞两次后应进入冷却，后续条目直接走浏览器，不再白烧十几秒"""
    monkeypatch.setattr(inv, "resolve_media", _raise_403)
    monkeypatch.setattr(media, "_resolve_via_browser", lambda aweme_id: dict(GOOD))

    media.ensure_audio("1", verbose=False)
    assert media._F2_COOLDOWN["fails"] == 1
    media.ensure_audio("2", verbose=False)
    assert media._F2_COOLDOWN["until"] > 0
