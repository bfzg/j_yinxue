"""
语音转文字（ASR）

两条路，配置里 ASR_BACKEND=auto 自动选：
  * paraformer-v2 文件转写：把音频传到百炼自带的临时存储（免费、不需要
    COS 密钥），再提交异步任务。实测 168 秒音频 4 秒出结果，最便宜。
    已经配好自己的对象存储时会自动改用 COS 直链。
  * paraformer-realtime-v2：实时接口兜底。它对长音频不稳，所以按
    ASR_CHUNK_SEC 切片后拼接时间戳。

产物统一：
  output/subtitles/{aweme_id}.txt   带时间戳的逐句稿（人工校对用）
  output/subtitles/{aweme_id}.json  sentences 结构化数据（文章分节用）
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
import config

import dashscope
dashscope.api_key = config.BAILIAN_API_KEY

# 百炼临时存储的上传凭证可以复用，但服务端给的有效期只有 300 秒：
# 批量跑到第五分钟起整批 403，退回 realtime 又会偶发断连，必须按时间主动换新
_CERT: Optional[dict] = None
_CERT_EXPIRES_AT: float = 0.0


def txt_path(aweme_id: str) -> Path:
    return config.SUBTITLES_DIR / f"{aweme_id}.txt"


def json_path(aweme_id: str) -> Path:
    return config.SUBTITLES_DIR / f"{aweme_id}.json"


def _fmt_ms(ms: int) -> str:
    s = int(ms) // 1000
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _cos_ready() -> bool:
    return bool(os.environ.get("COS_SECRET_ID")
                or os.environ.get("TENCENTCLOUD_SECRET_ID"))


def _sentences_out(aweme_id: str, sentences: list[dict]) -> dict:
    """落盘并返回摘要信息"""
    config.ensure_dirs()
    tp, jp = txt_path(aweme_id), json_path(aweme_id)
    lines = [f"[{_fmt_ms(s.get('begin_time', 0))}]"
             f"[{_fmt_ms(s.get('end_time', 0))}] {s.get('text', '').strip()}"
             for s in sentences if (s.get("text") or "").strip()]
    tp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    jp.write_text(json.dumps({"aweme_id": aweme_id, "sentences": sentences},
                             ensure_ascii=False, indent=2), encoding="utf-8")
    return {"text_path": str(tp), "json_path": str(jp),
            "n_sentences": len(lines),
            "chars": sum(len(l) for l in lines),
            "duration_ms": sentences[-1].get("end_time", 0) if sentences else 0}


# ---------- 后端 1：paraformer-v2 文件转写 ----------

def _upload_temp(mp3: Path) -> str:
    """传到百炼自带的临时存储，返回 oss:// 地址（只要 API Key，不占你任何配额）

    证书 300 秒过期，所以每次上传前先看剩余时间；真失败了就把缓存清掉重取，
    连续三轮还不行才让上层退回 realtime。
    """
    global _CERT, _CERT_EXPIRES_AT
    from dashscope.utils.oss_utils import OssUtils

    last = "接口没返回地址"
    for attempt in range(3):
        try:
            if _CERT is None or time.time() > _CERT_EXPIRES_AT - 20:
                info = OssUtils.get_upload_certificate(
                    model=config.ASR_FILE_MODEL, api_key=config.BAILIAN_API_KEY)
                _CERT = info.output or {}
                ttl = int(_CERT.get("expire_in_seconds") or 300)
                _CERT_EXPIRES_AT = time.time() + max(60, ttl)
            url, cert = OssUtils.upload(model=config.ASR_FILE_MODEL,
                                        file_path=str(mp3),
                                        api_key=config.BAILIAN_API_KEY,
                                        upload_certificate=_CERT)
            if url:
                _CERT = cert or _CERT
                return url
        except Exception as e:  # noqa: BLE001
            last = str(e)[:160]
            _CERT = None      # 大概率是证书过期，下一轮重取
        if attempt < 2:
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"上传百炼临时存储失败: {last}")


def _public_url(aweme_id: str, mp3: Path) -> tuple[str, bool]:
    """返回 (可下载地址, 是否需要 oss 解析头)"""
    if _cos_ready():
        try:
            from uploader import CosUploader

            up = CosUploader()
            key = f"{config.COS_AUDIO_PREFIX}/asr/{aweme_id}{mp3.suffix}"
            import media as _media
            if up.upload_file(mp3, key, content_type=_media.media_type(mp3)):
                return (f"https://{config.COS_BUCKET}.cos.{config.COS_REGION}"
                        f".myqcloud.com/{key}", False)
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] COS 不可用（{str(e)[:100]}），改用百炼临时存储")
    return _upload_temp(mp3), True


def _transcribe_file(aweme_id: str, mp3: Path) -> list[dict]:
    from dashscope.audio.asr import Transcription
    import httpx

    url, temp = _public_url(aweme_id, mp3)
    kwargs = {}
    if temp:
        # oss:// 地址只有带这个头，服务端才会替你取文件
        kwargs["headers"] = {"X-DashScope-OssResourceResolve": "enable"}
    resp = Transcription.async_call(model=config.ASR_FILE_MODEL, file_urls=[url],
                                   language_hints=["zh", "en"], **kwargs)
    task_id = (resp.output or {}).get("task_id")
    if not task_id:
        raise RuntimeError(f"提交转写任务失败: "
                           f"{getattr(resp, 'message', '') or resp}"[:300])

    deadline = time.time() + 1800
    while time.time() < deadline:
        r = Transcription.fetch(task=task_id)
        output = r.output or {}
        status = (output.get("task_status") or "").upper()
        if status == "SUCCEEDED":
            results = output.get("results") or []
            first = results[0] if results else {}
            sub = (first.get("subtask_status") or "").upper()
            if sub and sub != "SUCCEEDED":
                raise RuntimeError(
                    f"子任务失败 {sub}: "
                    f"{json.dumps(first, ensure_ascii=False)[:300]}")
            turl = first.get("transcription_url") or (
                (first.get("output") or {}).get("transcription_url"))
            if not turl:
                raise RuntimeError(f"任务成功但没有转写地址: {r}")
            data = httpx.get(turl, timeout=60).json()
            sentences: list[dict] = []
            for tr in data.get("transcripts") or []:
                sentences.extend(tr.get("sentences") or [])
            if not sentences:
                props = data.get("properties") or {}
                sentences = [{"begin_time": 0,
                              "end_time": int(props.get(
                                  "original_duration_in_milliseconds") or 0),
                              "text": tr.get("text", "")}
                             for tr in data.get("transcripts") or []
                             if (tr.get("text") or "").strip()]
            return sentences
        if status in ("FAILED", "ERROR", "CANCELED"):
            raise RuntimeError("转写任务失败: "
                               f"{json.dumps(output, ensure_ascii=False)[:300]}")
        time.sleep(3)
    raise RuntimeError("转写任务等待超时")


# ---------- 后端 2：paraformer-realtime-v2 本地文件 ----------

def _transcribe_realtime(aweme_id: str, mp3: Path) -> list[dict]:
    """实时接口对长音频不稳，切片识别再把时间戳整体后移拼回去"""
    from dashscope.audio.asr import Recognition
    import media

    total_ms = media.probe_duration(mp3) or 0
    chunk_ms = max(30, int(getattr(config, "ASR_CHUNK_SEC", 120))) * 1000
    n_chunks = max(1, math.ceil(total_ms / chunk_ms)) if total_ms else 1
    tmp = config.OUTPUT_DIR / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)

    out: list[dict] = []
    for i in range(n_chunks):
        offset = i * chunk_ms
        wav = tmp / f"{aweme_id}.asr{i}.wav"
        try:
            for attempt in range(3):
                try:
                    media.resample_for_asr(mp3, wav, start_ms=offset,
                                           duration_ms=chunk_ms)
                    rec = Recognition(model=config.ASR_REALTIME_MODEL, callback=None,
                                      format="wav", sample_rate=config.ASR_SAMPLE_RATE,
                                      language_hints=["zh", "en"])
                    result = rec.call(file=str(wav))
                    break
                except Exception as e:  # noqa: BLE001
                    # websocket 偶发被服务端关掉，重试基本能过
                    if attempt == 2:
                        raise
                    print(f"  [warn] realtime 第 {i + 1}/{n_chunks} 段重试"
                          f"（{str(e)[:80]}）")
                    time.sleep(2 * (attempt + 1))
        finally:
            wav.unlink(missing_ok=True)

        code = getattr(result, "status_code", 200)
        if code != 200:
            raise RuntimeError(f"realtime 识别失败 code={code} "
                               f"{getattr(result, 'message', '')}"[:300])
        sentences = result.get_sentence() or []
        if isinstance(sentences, dict):
            sentences = [sentences]
        for item in sentences:
            if not isinstance(item, dict):
                continue
            item = dict(item)
            item["begin_time"] = int(item.get("begin_time") or 0) + offset
            item["end_time"] = int(item.get("end_time") or 0) + offset
            out.append(item)
        if not sentences and n_chunks > 1:
            raise RuntimeError(f"realtime 第 {i + 1}/{n_chunks} 段没有识别结果")
    return out


def ensure_transcript(aweme_id: str, audio: Path, force: bool = False) -> dict:
    """幂等转写。返回 {backend, n_sentences, chars, seconds, text_path, json_path}"""
    audio = Path(audio)
    if not audio.exists():
        raise FileNotFoundError(f"音频不存在: {audio}")

    jp = json_path(aweme_id)
    if jp.exists() and not force:
        try:
            n = len(json.loads(jp.read_text(encoding="utf-8")).get("sentences") or [])
            return {"backend": "cached", "n_sentences": n,
                    "text_path": str(txt_path(aweme_id)), "json_path": str(jp),
                    "chars": txt_path(aweme_id).stat().st_size, "seconds": 0}
        except Exception:  # noqa: BLE001
            pass

    want = config.ASR_BACKEND
    if want == "auto":
        want = "file"
    t0 = time.time()

    if want == "file":
        try:
            sentences = _transcribe_file(aweme_id, audio)
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] paraformer-v2 不可用（{str(e)[:120]}），改用 realtime")
            want = "realtime"
            sentences = []

    if want == "realtime":
        sentences = _transcribe_realtime(aweme_id, audio)

    if not sentences:
        raise RuntimeError("转写结果为空，可能是纯音乐或音频损坏")
    meta = _sentences_out(aweme_id, sentences)
    meta.update(backend=("paraformer-v2" if want == "file"
                         else config.ASR_REALTIME_MODEL),
                seconds=round(time.time() - t0))
    return meta


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="音频转文字")
    ap.add_argument("aweme_id")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    config.ensure_dirs()
    import media
    print(json.dumps(ensure_transcript(args.aweme_id,
                                       media.audio_path_any(args.aweme_id),
                                       force=args.force),
                     ensure_ascii=False, indent=2))
