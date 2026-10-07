"""
uniCloud 云函数客户端（只给本地面板 / 命令行用）

对接两个 URL 化入口，都在 config.UNICLOUD_BASE_URL 下：
  /jy-content 数据读写：读动作公开，写动作必须带管理令牌
  /jy-upload  云存储上传：除 ping 外全要令牌

三条纪律：
  1. 一律串行 + 指数退避。支付宝云默认域名是共享限流池，并发一高就 503；
  2. 上传完必须用云函数回传的 size / md5 复核，字节不对就换一种编码重试，
     宁可重传，也不能让线上出现半个音频；
  3. 本地预览地址（http://127.0.0.1:8766/...）绝不发进云端，云侧也会一律拒收。
"""
from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import time
from pathlib import Path
from typing import Any, Iterable, Optional

import httpx

import config


class CloudError(RuntimeError):
    """云函数返回非 0 code，或传输层彻底失败"""

    def __init__(self, message: str, code: int = -1, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


# base64 后体积约 *4/3，再留点余量给 JSON 包装；超过就走原始字节直传
JSON_SAFE_BYTES = 20 * 1024 * 1024
# 云函数侧 MAX_BYTES 是 30MB，再大就别发了，先在面板里转码
UPLOAD_MAX_BYTES = 28 * 1024 * 1024

_RETRY_CODES = {429, 500, 502, 503, 504}

# 读动作不需要令牌，写动作一律带上
_READ_ACTIONS = {"ping", "version", "manifest", "columns", "column",
                 "episodes", "articles", "playlist", "article"}

_client: Optional[httpx.Client] = None


def _http() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(timeout=httpx.Timeout(300.0, connect=15.0),
                               follow_redirects=True)
    return _client


def content_url() -> str:
    return config.UNICLOUD_BASE_URL.rstrip("/") + config.UNICLOUD_CONTENT_PATH


def upload_url() -> str:
    return config.UNICLOUD_BASE_URL.rstrip("/") + config.UNICLOUD_UPLOAD_PATH


def token() -> str:
    tok = config.admin_token()
    if not tok:
        raise CloudError(
            f"缺少管理令牌：把云函数的 ADMIN_TOKEN 写进 {config.UNICLOUD_KEY_FILE}，"
            "或设置环境变量 DY_UNICLOUD_TOKEN")
    return tok


def configured() -> dict:
    """面板展示用的配置回显（不回显令牌本体，只说配没配）"""
    return {
        "base_url": config.UNICLOUD_BASE_URL,
        "space_id": config.UNICLOUD_SPACE_ID,
        "content_path": config.UNICLOUD_CONTENT_PATH,
        "upload_path": config.UNICLOUD_UPLOAD_PATH,
        "storage_host": config.CLOUD_STORAGE_HOST,
        "prefix": config.CLOUD_PATH_PREFIX,
        "token_configured": bool(config.admin_token()),
        "token_file": str(config.UNICLOUD_KEY_FILE),
    }


def guess_type(path: str | Path) -> str:
    """m4a 必须给对 MIME，否则 iOS / 小程序 audio 可能直接拒播"""
    suffix = Path(str(path)).suffix.lower()
    if suffix == ".m4a":
        return "audio/mp4"
    if suffix == ".mp3":
        return "audio/mpeg"
    if suffix == ".txt":
        return "text/plain; charset=utf-8"
    if suffix == ".json":
        return "application/json"
    return mimetypes.guess_type(str(path))[0] or "application/octet-stream"


def _retryable_status(status: int) -> bool:
    return status in _RETRY_CODES or status >= 500


def _brief(text: str) -> str:
    """支付宝云没绑定 URL 时返回一整页 nginx HTML，直接拼进报错会没法看"""
    body = (text or "").strip()
    if body.startswith("<") or "<html" in body.lower():
        return "网关返回 HTML 错误页（通常是函数未部署或 URL 化路径没绑定）"
    return " ".join(body.split())[:180]


def _post(url: str, *, payload: dict | None = None, content: bytes | None = None,
          params: dict | None = None, headers: dict | None = None) -> Any:
    """发一次请求，把 HTTP 层和云函数层的错误统一成 CloudError"""
    try:
        if content is not None:
            resp = _http().post(url, content=content, params=params, headers=headers)
        else:
            resp = _http().post(url, json=payload or {}, params=params, headers=headers)
    except (httpx.TimeoutException, httpx.TransportError) as e:
        raise CloudError(f"网络失败: {type(e).__name__}: {str(e)[:160]}",
                         retryable=True) from e

    if resp.status_code >= 400:
        raise CloudError(f"HTTP {resp.status_code}: {_brief(resp.text)}",
                         code=resp.status_code,
                         retryable=_retryable_status(resp.status_code))
    try:
        env = resp.json()
    except ValueError as e:
        raise CloudError(f"返回不是 JSON: {_brief(resp.text)}") from e
    if not isinstance(env, dict):
        raise CloudError(f"返回结构不对: {str(env)[:200]}")
    if env.get("code", 0) != 0:
        try:
            code_int = int(env.get("code"))
        except (TypeError, ValueError):
            code_int = -1
        raise CloudError(str(env.get("message") or "请求失败")[:300], code=code_int,
                         retryable=code_int in _RETRY_CODES)
    return env.get("data")


def _retry(what: str, fn, attempts: int = 4, sleep0: float = 1.5,
           reporter=None) -> Any:
    """指数退避重试；reporter 每轮把进度回灌给面板任务卡"""
    last: Optional[CloudError] = None
    for i in range(attempts):
        try:
            return fn()
        except CloudError as e:
            last = e
            if not e.retryable or i == attempts - 1:
                break
            gap = sleep0 * (2 ** i)
            if reporter:
                reporter(f"{what} 第 {i + 1} 次失败（{str(e)[:80]}），{gap:.0f}s 后重试")
            time.sleep(gap)
    # 把最后一次的状态码带出去，调用方才知道是「没部署」还是「令牌不对」
    raise CloudError(f"{what} 失败: {last}",
                     code=getattr(last, "code", -1),
                     retryable=getattr(last, "retryable", False)) from last


# ---------- /jy-content ----------

def call_content(action: str, *, attempts: int = 4, reporter=None,
                 **params: Any) -> Any:
    """调一次数据接口，返回 data 字段"""
    body: dict[str, Any] = {"action": action}
    body.update({k: v for k, v in params.items() if v is not None})
    if action not in _READ_ACTIONS:
        body["token"] = token()
    return _retry(f"content/{action}", lambda: _post(content_url(), payload=body),
                  attempts=attempts, reporter=reporter)


# ---------- /jy-upload ----------

def _verify(res: Any, expect_bytes: int, expect_md5: str) -> bool:
    if not isinstance(res, dict):
        return False
    if int(res.get("size") or -1) != expect_bytes:
        return False
    if str(res.get("md5") or "") != expect_md5:
        return False
    return bool(res.get("fileID"))


def _put_params(cloud_path: str, content_type: str) -> dict:
    return {"action": "put", "token": token(), "cloudPath": cloud_path,
            "contentType": content_type or ""}


def put_bytes(cloud_path: str, data: bytes, content_type: str = "",
              *, reporter=None) -> dict:
    """
    传一个字节流到云存储，返回 {fileID, url, cloudPath, size, md5}

    先走 JSON + base64（网关行为最可控）；文件太大或直传被拒时退原始 body，
    支付宝云会把非文本 body 置 isBase64Encoded，此时参数只能从 querystring 取。
    """
    if not cloud_path.startswith(f"{config.CLOUD_PATH_PREFIX}/"):
        raise CloudError(f"cloudPath 必须以 {config.CLOUD_PATH_PREFIX}/ 开头: {cloud_path}")
    if len(data) > UPLOAD_MAX_BYTES:
        raise CloudError(f"文件 {len(data) / 1048576:.1f}MB 超过上限，"
                         "请先在面板「音频体积」里转码")
    md5 = hashlib.md5(data).hexdigest()

    def _via_json():
        body = _put_params(cloud_path, content_type)
        body.update({"encoding": "base64",
                     "content": base64.b64encode(data).decode("ascii")})
        return _post(upload_url(), payload=body)

    def _via_raw():
        return _post(upload_url(), content=data,
                     params=_put_params(cloud_path, content_type),
                     headers={"Content-Type": content_type or "application/octet-stream"})

    def _once():
        modes = [_via_raw, _via_json] if len(data) > JSON_SAFE_BYTES else [_via_json, _via_raw]
        last_err: Optional[Exception] = None
        for mode in modes:
            try:
                res = mode()
            except CloudError as e:
                if e.retryable:
                    raise          # 限流类交给 _retry 整轮退避，别在这里换编码
                last_err = e
                if reporter:
                    reporter(f"{cloud_path} 被拒（{str(e)[:80]}），换一种编码")
                continue
            if _verify(res, len(data), md5):
                return res
            last_err = CloudError(f"云函数回传校验不符: {str(res)[:200]}")
        raise CloudError(str(last_err or "上传失败"))

    return _retry(f"upload {cloud_path}", _once, attempts=3, reporter=reporter)


def put_file(local: str | Path, cloud_path: str = "", content_type: str = "",
             *, reporter=None) -> dict:
    """传一个本地文件；cloud_path 缺省按文件名落到 tmp 前缀下"""
    p = Path(local)
    if not p.exists():
        raise CloudError(f"本地文件不存在: {p}")
    return put_bytes(cloud_path or f"{config.CLOUD_PATH_PREFIX}/tmp/{p.name}",
                     p.read_bytes(), content_type or guess_type(p), reporter=reporter)


def put_text(cloud_path: str, text: str,
             content_type: str = "text/plain; charset=utf-8", *, reporter=None) -> dict:
    return put_bytes(cloud_path, text.encode("utf-8"), content_type, reporter=reporter)


def head_file(cloud_path: str) -> dict:
    return _post(upload_url(), payload={"action": "head", "token": token(),
                                        "cloudPath": cloud_path})


def delete_files(file_ids: Iterable[str]) -> dict:
    ids = [str(one) for one in file_ids if one]
    if not ids:
        return {"requested": 0, "fileList": []}
    return _retry("upload/delete",
                  lambda: _post(upload_url(),
                                payload={"action": "delete", "token": token(),
                                       "fileList": ids}))


# ---------- 探活 / 线上读链路体检 ----------

def health(*, attempts: int = 1) -> dict:
    """把小程序真正会踩的读动作逐个走一遍，只探活是查不出这类问题的

    column / episodes 用的是按 _id 批量查，运行时不认 in 写法时 ping 照样绿灯，
    合集详情页却是整页失败，所以这里必须打到具体动作上。
    """
    steps: list[dict[str, Any]] = []

    def run(name: str, thunk, brief) -> Any:
        try:
            data = thunk()
        except CloudError as e:
            steps.append({"action": name, "ok": False, "error": str(e)[:220]})
            return None
        try:
            note = brief(data)
        except Exception:  # noqa: BLE001  摘要取字段失败不能连累体检结论
            note = ""
        steps.append({"action": name, "ok": True, "detail": note})
        return data

    ver = run("version", lambda: call_content("version", attempts=attempts),
              lambda d: f"v{d.get('version')} / 批量查询能力={d.get('inMode')}")
    run("manifest", lambda: call_content("manifest", attempts=attempts),
        lambda d: (f"分集 {d['counts']['episodes']} 条 / 栏目 {d['counts']['columns']} 个"
                   f" / dataVersion {d.get('dataVersion')}"))
    cols = run("columns", lambda: call_content("columns", attempts=attempts),
               lambda d: f"{len(d.get('items') or [])} 个栏目")

    target = next((one for one in ((cols or {}).get("items") or [])
                   if one.get("nEpisodes")), None)
    col = None
    if target:
        col = run("column",
                  lambda: call_content("column", id=target["id"], attempts=attempts),
                  lambda d: (f"{d['item']['name']} 取到 {len(d['item']['episodes'])} 集"
                             f" / 取数路径={d.get('fetchMode')}"))
    else:
        steps.append({"action": "column", "ok": False,
                      "error": "线上还没有带分集的栏目，无法验证合集详情页"})

    first: dict[str, Any] = {}
    if col and (col.get("item") or {}).get("episodes"):
        first = col["item"]["episodes"][0]
        aid = str(first.get("awemeId") or "")
        run("episodes", lambda: call_content("episodes", ids=aid, attempts=attempts),
            lambda d: f"{len(d.get('items') or [])} 条")
        run("article", lambda: call_content("article", id=aid, attempts=attempts),
            lambda d: f"{(d.get('item') or {}).get('title', '')[:20]}")
        # 正文兜底通道：CDN 边缘节点把 403 错误页缓存住时，小程序靠这条路自救
        run("articleText", lambda: call_content("articleText", id=aid, attempts=attempts),
            lambda d: (f"{d.get('chars')} 字"
                       + ("" if not d.get("retries") else
                          f"（CDN 没直接给，绕 {d.get('host')}"
                          + ("+换缓存键" if d.get("bust") else "")
                          + f"、第 {d.get('retries')} 次才取到）")))

    # 地址审计：封面/音频/正文只要还有一个落在抖音域，线上就是随时会掉的
    host = config.CLOUD_STORAGE_HOST
    item = (col or {}).get("item") or {}
    audited = {"栏目封面": item.get("cover"),
               "音频": first.get("audioUrl"), "正文": first.get("articleUrl")}
    have = [k for k, v in audited.items() if v]
    off = [k for k in have if not str(audited[k]).startswith(host)]
    steps.append({"action": "url来源", "ok": not off,
                  "detail": (f"{len(have) - len(off)}/{len(have)} 已在云存储" if have
                             else "这一轮没取到地址（上面column失败时属正常）")
                           + ("；还在用外链: " + "、".join(off) if off else "")})

    out: dict[str, Any] = {"ok": all(one["ok"] for one in steps), "steps": steps,
                           "storageHost": host}
    stale = [one["action"] for one in steps
             if not one["ok"] and "未知 action" in str(one.get("error", ""))]
    redeploy = ("请在 HBuilderX 里右键 "
                "uniCloud-alipay/cloudfunctions/function-jy-content →「上传部署」")
    if isinstance(ver, dict) and "inMode" not in ver:
        out["hint"] = "线上还是旧版云函数（version 里没有 inMode），" + redeploy
    elif stale:
        out["hint"] = (f"线上云函数缺少 {'、'.join(stale)} 动作，版本落后了，") + redeploy
    return out


def ping() -> dict:
    """两个函数探活 + 线上读链路体检，部署完第一时间就能验"""
    out: dict[str, Any] = {"content": None, "upload": None}
    try:
        out["content"] = call_content("ping", attempts=1)
    except CloudError as e:
        out["content"] = {"error": str(e)[:200]}
    try:
        out["upload"] = _post(upload_url(), payload={"action": "ping"})
    except CloudError as e:
        out["upload"] = {"error": str(e)[:200]}
    out["config"] = configured()
    try:
        out["health"] = health()
    except Exception as e:  # noqa: BLE001  体检本身出错别把探活结果一起带走
        out["health"] = {"ok": False, "error": str(e)[:200]}
    return out


def batches(items: list, size: int = 120):
    """云函数单次上限 300 条，默认切 120 一组，给请求体留足余量"""
    for i in range(0, len(items), size):
        yield items[i:i + size]
