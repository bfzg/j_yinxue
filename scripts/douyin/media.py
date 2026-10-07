"""
下载 + 转码：抖音直链 → output/audio/{aweme_id}.{档位扩展名}

直链必须先 resolve_media() 现取现用，列表页里的链接几分钟后就失效。
优先下纯音频流（bit_rate_audio），比整条视频小一个数量级；拿不到再退回
最低码率 mp4 并用 -vn 抽音频。

产物格式跟着 config.AUDIO_PROFILE 走，默认单声道 AAC 40kbps（.m4a）。
历史上已经是 .mp3 的存量文件不重下，audio_path_any() 会认它们，
要瘦身跑 compress.py。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
import config
import inventory as inv
from extractor import AudioExtractor

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

_extractor: Optional[AudioExtractor] = None
_extract_profile = ""
# 连续失败计数与冷却截止时间，进程内有效
_F2_COOLDOWN = {"fails": 0, "until": 0.0, "sec": getattr(config, "F2_COOLDOWN_SEC", 600)}


def _ext() -> AudioExtractor:
    """档位变了就重建提取器，面板切音质不用重启"""
    profile = config.audio_profile()
    global _extractor, _extract_profile
    if _extractor is None or _extract_profile != profile["name"]:
        _extractor = AudioExtractor(profile=profile, delete_video=True)
        _extract_profile = profile["name"]
    return _extractor


def audio_path(aweme_id: str) -> Path:
    """当前档位的落库路径（不一定存在）"""
    return config.AUDIO_DIR / f"{aweme_id}.{config.audio_profile()['ext']}"


def audio_path_any(aweme_id: str) -> Path:
    """
    已存在的音频，优先级：当前档位 > 其他档位扩展名 > 老的 .mp3。
    找不到时返回当前档位路径，让调用方拿到一个可写的目标名。
    """
    exts = [config.audio_profile()["ext"]]
    exts += [p["ext"] for p in config.AUDIO_PROFILES.values() if p["ext"] not in exts]
    for e in exts:
        f = config.AUDIO_DIR / f"{aweme_id}.{e}"
        if f.exists() and f.stat().st_size > 10240:
            return f
    return audio_path(aweme_id)


def media_type(path: Path) -> str:
    """给 FileResponse / COS ContentType 用"""
    for prof in config.AUDIO_PROFILES.values():
        if path.suffix == "." + prof["ext"]:
            return prof["media_type"]
    return "application/octet-stream"


def _resolve_via_browser(aweme_id: str):
    """直链兜底：浏览器取 aweme_detail，再走同一套 pick_media_urls 挑流"""
    try:
        import browser_scan
        detail = browser_scan.call(browser_scan.resolve_detail, aweme_id,
                                   timeout=180)
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] 浏览器取直链失败: {type(e).__name__}: {str(e)[:120]}")
        return None
    out = inv.pick_media_urls(detail)
    if not (out.get("audio_urls") or out.get("video_urls")):
        print("  [warn] 浏览器拿到的详情里没有可用播放地址")
        return None
    out["aweme"] = detail
    print("  [直链] 已由浏览器详情接口补齐")
    return out


def _tmp(name: str) -> Path:
    d = config.OUTPUT_DIR / "tmp"
    d.mkdir(parents=True, exist_ok=True)
    return d / name


def _download(url: str, dest: Path, timeout: int = 180) -> bool:
    """流式下载，失败自动清理半截文件"""
    import httpx

    if dest.exists():
        dest.unlink()
    headers = {"User-Agent": _UA, "Referer": "https://www.douyin.com/"}
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True,
                          headers=headers) as client:
            with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    print(f"  [warn] 下载返回 {resp.status_code}")
                    return False
                with open(dest, "wb") as fh:
                    for chunk in resp.iter_bytes(1 << 16):
                        fh.write(chunk)
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] 下载异常: {type(e).__name__}: {str(e)[:120]}")
        if dest.exists():
            dest.unlink()
        return False
    if not dest.exists() or dest.stat().st_size < 4096:
        return False
    return True


def _to_mp3(src: Path, aweme_id: str) -> Optional[Path]:
    """ffmpeg 按档位重编 + 响度标准化，中间文件用完即删"""
    target = audio_path(aweme_id)
    if target.exists():
        src.unlink(missing_ok=True)
        return target
    # AudioExtractor 用输入文件名（去扩展名）决定产物名，中间文件带码率后缀时
    # 产物会叫 {aweme_id}.a0.m4a，统一挪回幂等路径 {aweme_id}.m4a
    result = _ext().extract(src, config.AUDIO_DIR)
    if not result:
        return None
    produced = Path(result)
    if produced != target:
        if target.exists():
            target.unlink()
        produced.rename(target)
        produced = target
    src.unlink(missing_ok=True)
    return produced


def ensure_audio(aweme_id: str, force: bool = False,
                 verbose: bool = True) -> Path:
    """幂等：本地已有任意档位音频就直接用，否则下载并按当前档位转码"""
    config.ensure_dirs()
    target = audio_path(aweme_id)
    existing = audio_path_any(aweme_id)
    if not force and existing.exists() and existing.stat().st_size > 10240:
        return existing
    if force and target.exists():
        target.unlink()

    media = None
    last_err = ""
    cookie_err = ""
    import time as _t

    # f2 接口路被风控时每条要白烧十几秒，连撞两次就冷却一会儿直接走浏览器
    source = getattr(config, "MEDIA_SOURCE", "auto")
    if source == "browser":
        last_err = "已指定用浏览器取直链"
    elif _t.time() < _F2_COOLDOWN["until"]:
        last_err = "f2 接口在冷却中（连续取直链失败）"
    else:
        for attempt in range(2):
            try:
                media = inv.resolve_media(aweme_id)
                _F2_COOLDOWN["fails"] = 0
                break
            except Exception as e:  # noqa: BLE001
                last_err = f"{type(e).__name__}: {str(e)[:160]}"
                if inv._is_cookie_error(e):
                    # 单条 403 往往只是这一条的直链被限，先让浏览器兜底再试一次；
                    # 真的取不到会在下面统一按 Cookie 失效处理，别在这里掐断整批
                    cookie_err = str(e)[:160]
                    _F2_COOLDOWN["fails"] += 1
                    if _F2_COOLDOWN["fails"] >= 2:
                        _F2_COOLDOWN["until"] = _t.time() + _F2_COOLDOWN["sec"]
                    break
                _t.sleep(2 * (attempt + 1))
        else:
            _F2_COOLDOWN["fails"] += 1
            if _F2_COOLDOWN["fails"] >= 2:
                _F2_COOLDOWN["until"] = _t.time() + _F2_COOLDOWN["sec"]
                print(f"  [warn] f2 直链连续失败，{_F2_COOLDOWN['sec']} 秒内改用浏览器取直链")
    if media is None:
        # f2 那条接口路被风控时，改用同一个 Chrome 打开作品页拦 detail 接口
        media = _resolve_via_browser(aweme_id)
    if media is None:
        if cookie_err:
            # 接口路和浏览器路都拿不到，才认定是登录态问题，让整批停下来
            raise inv.CookieInvalid(f"f2 与浏览器均取不到直链：{cookie_err}")
        raise RuntimeError(f"取直链失败 {aweme_id}: {last_err}")

    tried: list[str] = []

    for i, url in enumerate(media.get("audio_urls") or []):
        tmp = _tmp(f"{aweme_id}.a{i}.m4a")
        if verbose:
            print(f"  [下载] 音频流 #{i + 1}")
        if _download(url, tmp):
            out = _to_mp3(tmp, aweme_id)
            if out:
                return out
        tried.append(f"audio#{i}")

    for i, url in enumerate(media.get("video_urls") or []):
        tmp = _tmp(f"{aweme_id}.v{i}.mp4")
        if verbose:
            print(f"  [下载] 视频流 #{i + 1}（抽音频）")
        if _download(url, tmp):
            out = _to_mp3(tmp, aweme_id)
            tmp.unlink(missing_ok=True)
            if out:
                return out
        tried.append(f"video#{i}")
        if i >= 2:  # 前三档码率都拿不到就不再往下试
            break

    raise RuntimeError(f"下载/转码失败 {aweme_id}，已试 {tried}")


def resample_for_asr(src: Path, out: Path, sample_rate: int = 0,
                     start_ms: int = 0, duration_ms: int = 0) -> Path:
    """ASR 要 16k 单声道，单独产一个临时文件，用完删。
    start_ms / duration_ms 用于实时接口兜底时的切片。"""
    import subprocess

    sr = sample_rate or config.ASR_SAMPLE_RATE
    if out.exists():
        out.unlink()
    cmd = [_ext()._ffmpeg_path, "-y"]
    if start_ms:
        cmd += ["-ss", f"{start_ms / 1000:.3f}"]
    if duration_ms:
        cmd += ["-t", f"{duration_ms / 1000:.3f}"]
    cmd += ["-i", str(src), "-ac", "1", "-ar", str(sr),
            "-c:a", "pcm_s16le", str(out)]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if res.returncode != 0:
        raise RuntimeError(f"重采样失败: {res.stderr[-300:]}")
    return out


def probe_duration(path: Path) -> int:
    """没有 ffprobe，用 ffmpeg -i 的 stderr 里 Duration 兜底，返回毫秒"""
    import re
    import subprocess

    if not path.exists():
        return 0
    res = subprocess.run([_ext()._ffmpeg_path, "-i", str(path)],
                         capture_output=True, text=True)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", res.stderr)
    if not m:
        return 0
    h, mnt, sec = m.groups()
    return int((int(h) * 3600 + int(mnt) * 60 + float(sec)) * 1000)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="下载并转码音频")
    ap.add_argument("aweme_id")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    print(ensure_audio(args.aweme_id, force=args.force))
