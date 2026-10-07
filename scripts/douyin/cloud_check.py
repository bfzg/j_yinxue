"""
云函数上传前自检：语法、令牌一致性、本地与线上的版本差

这里刻意不打包。HBuilderX 的上传单元就是
`uniCloud-alipay/cloudfunctions/<函数名>` 这个目录本身（右键 → 上传部署），
中间再产一个 zip 只会制造「到底该传哪个」的误会，还容易传到过期包。
改成把三件真会踩的事一次查清：

  1. 每个 .js 能不能过 `node --check`（语法错传上去就是整函数 500）
  2. 三处 ADMIN_TOKEN 是否同一串（不一致写接口直接 401，且很难猜）
  3. 线上函数版本是否落后本地（落后就点名要重传哪个目录）
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import config

FUNC_ROOT = config.BASE_DIR.parent.parent / "uniCloud-alipay" / "cloudfunctions"
FUNCTIONS = ("function-jy-content", "function-jy-upload")
# 只查自己写的代码：node_modules 里第三方包不需要过语法检查
SKIP_PARTS = ("node_modules", "__pycache__")


def _js_files(name: str) -> list[Path]:
    src = FUNC_ROOT / name
    return sorted(p for p in src.rglob("*.js") if not set(p.parts) & set(SKIP_PARTS))


def _check_syntax(name: str, node: str | None) -> list[str]:
    if not node:
        return ["没找到 node，跳过语法检查"]
    bad = []
    for path in _js_files(name):
        r = subprocess.run([node, "--check", str(path)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            bad.append(f"{path.name}: "
                       + (r.stderr or r.stdout).strip().splitlines()[-1][:160])
    return bad


def _token_of(path: Path, node: str | None) -> str:
    """用 node 真去 require 一次，避免正则漏掉引号/模板串这类写法"""
    if not (node and path.exists()):
        return ""
    r = subprocess.run([node, "-e",
                        f"process.stdout.write(String(require({json.dumps(str(path))}).ADMIN_TOKEN||''))"],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def _fingerprint(value: str) -> str:
    """只出指纹不出明文，令牌不该出现在终端和日志里"""
    return hashlib.md5(value.encode()).hexdigest()[:8] if value else ""


def _local_version(name: str) -> str:
    pkg = FUNC_ROOT / name / "package.json"
    try:
        return str(json.loads(pkg.read_text()).get("version") or "")
    except Exception:  # noqa: BLE001  读不出来就当未知，别把自检整体带崩
        return ""


def _online_version(name: str) -> str:
    """问线上自己报的版本；函数比这版还老时根本没有 version 字段"""
    import cloud_client as cc
    try:
        if name == "function-jy-content":
            data = cc.call_content("version", attempts=1)
        else:
            data = cc._post(cc.upload_url(), payload={"action": "ping"})
        return str(((data or {}).get("data") or {}).get("version")
                   or (data or {}).get("version") or "")
    except Exception:  # noqa: BLE001  没网/没配令牌时不该挡住本地检查
        return ""


def check_one(name: str, *, node: str | None, token_fp: str, reporter=None) -> dict[str, Any]:
    src = FUNC_ROOT / name
    files = _js_files(name)
    syntax = _check_syntax(name, node)
    secret_token = _token_of(src / "secret.js", node)
    has_secret = (src / "secret.js").exists()

    out: dict[str, Any] = {
        "dir": str(src),
        "files": len(files),
        "version": _local_version(name),
        "syntax": "ok" if not syntax else syntax,
        "secret": ("ok" if has_secret and secret_token else
                   ("缺 secret.js" if not has_secret else "读不到 ADMIN_TOKEN")),
        "tokenMatch": bool(secret_token) and _fingerprint(secret_token) == token_fp,
    }
    out["online"] = _online_version(name)
    # 线上报不出来 = 那个函数还是没带 version 的旧版，一样得重传
    out["stale"] = bool(out["version"]) and out["online"] != out["version"]

    if reporter:
        mark = "需重传" if out["stale"] else "已一致"
        reporter(f"{name}：v{out['version'] or '?'} / 线上 {out['online'] or '未上报'}"
                 f" → {mark}；{len(files)} 个 js，语法{out['syntax'] if isinstance(out['syntax'], str) else '有错'}")
    return out


def check_all(*, online: bool = True, reporter=None) -> dict[str, Any]:
    node = shutil.which("node")
    local_token = config.admin_token()
    token_fp = _fingerprint(local_token)

    result: dict[str, Any] = {"node": bool(node),
                              "tokenSource": str(config.UNICLOUD_KEY_FILE),
                              "token": ("未配置" if not local_token
                                        else f"已配置 {len(local_token)} 字符 / {_fingerprint(local_token)}"),
                              "functions": {}}
    for name in FUNCTIONS:
        one = check_one(name, node=node, token_fp=token_fp, reporter=reporter)
        if not online:
            one.pop("online", None)
            one.pop("stale", None)
        result["functions"][name] = one

    problems: list[str] = []
    if not local_token:
        problems.append(f"没读到管理令牌：写进 {config.UNICLOUD_KEY_FILE}")
    for name, one in result["functions"].items():
        if one["secret"] != "ok":
            problems.append(f"{name}：{one['secret']}（从 secret.example.js 复制并填同一串令牌）")
        elif not one["tokenMatch"]:
            problems.append(f"{name}：ADMIN_TOKEN 与 {config.UNICLOUD_KEY_FILE.name} 不是同一串")
        if one["syntax"] != "ok":
            problems.append(f"{name}：语法检查没过 → {one['syntax']}")
        if one.get("stale"):
            problems.append(f"{name}：线上 {one['online']} 落后本地 {one['version']}，需要重新上传")

    stale = [n for n, one in result["functions"].items() if one.get("stale")]
    result["problems"] = problems
    result["ok"] = not problems
    result["next_step"] = (
        "在 HBuilderX 里右键 uniCloud-alipay/cloudfunctions/"
        + ("、".join(stale) if stale else FUNCTIONS[0])
        + " → 上传部署（上传的是这个目录，不是任何 zip）；"
          "URL 化路径保持 /jy-content、/jy-upload 不变"
        if stale or not result["ok"] else
        "本地与线上函数一致，不用重传")
    return result


def _versions() -> dict[str, str]:
    return {name: _local_version(name) for name in FUNCTIONS}


if __name__ == "__main__":
    print(json.dumps(check_all(reporter=lambda m: print("  " + m, flush=True)),
                     ensure_ascii=False, indent=2))
