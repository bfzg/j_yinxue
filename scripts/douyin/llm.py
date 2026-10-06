"""
百炼（DashScope）兼容模式客户端

只用 httpx，不引 openai sdk。集中做三件事：超时重试、把返回的 markdown
代码块里的 JSON 抠出来、统计 token 花销（面板要显示成本）。
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).parent))
import config

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.M)

# 累计用量，进程内有效；长任务结束时写进 events 表
USAGE: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0,
                         "calls": 0, "failed": 0, "seconds": 0.0}

# qwen-plus 每百万 token 人民币单价（输入/输出）
PRICE_PER_M = {"qwen-plus": (0.8, 2.0), "qwen-turbo": (0.3, 0.6),
              "qwen-max": (2.4, 9.6)}


def cost_yuan() -> float:
    pin, pout = PRICE_PER_M.get(config.BAILIAN_MODEL, (0.8, 2.0))
    return round(USAGE["prompt_tokens"] / 1e6 * pin
                 + USAGE["completion_tokens"] / 1e6 * pout, 4)


def summary() -> str:
    return (f"调用 {USAGE['calls']} 次 / 失败 {USAGE['failed']} 次 / "
            f"tokens {USAGE['prompt_tokens']}+{USAGE['completion_tokens']} / "
            f"约 ¥{cost_yuan()}")


def chat(messages: list[dict], model: Optional[str] = None,
         temperature: float = 0.3, max_tokens: int = 4000,
         json_mode: bool = False, timeout: int = 120,
         retries: int = 3) -> str:
    """返回纯文本。失败抛 RuntimeError"""
    import httpx

    payload: dict[str, Any] = {
        "model": model or config.BAILIAN_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    last_err: Optional[str] = None
    for attempt in range(1, retries + 1):
        t0 = time.time()
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(
                    f"{config.BAILIAN_BASE_URL}/chat/completions",
                    headers={"Authorization": f"Bearer {config.BAILIAN_API_KEY}",
                             "Content-Type": "application/json"},
                    json=payload,
                )
                if resp.status_code != 200:
                    raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
                data = resp.json()
            usage = data.get("usage") or {}
            USAGE["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
            USAGE["completion_tokens"] += int(usage.get("completion_tokens") or 0)
            USAGE["calls"] += 1
            USAGE["seconds"] += time.time() - t0
            choices = data.get("choices") or []
            text = ((choices[0].get("message") or {}).get("content") or "").strip()
            if not text:
                raise RuntimeError(f"空返回: {json.dumps(data, ensure_ascii=False)[:200]}")
            return text
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
            USAGE["failed"] += 1
            if attempt < retries:
                time.sleep(min(10, 2 * attempt))
    raise RuntimeError(f"百炼调用失败（重试 {retries} 次）: {last_err}")


def _loose_json(text: str) -> Any:
    """先按严格 JSON 解析，失败就抠出第一个完整的 {} 或 []"""
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        pass
    cleaned = _FENCE_RE.sub("", text).strip()
    try:
        return json.loads(cleaned)
    except Exception:  # noqa: BLE001
        pass
    for opener, closer in (("[", "]"), ("{", "}")):
        start = cleaned.find(opener)
        if start < 0:
            continue
        depth = 0
        for i in range(start, len(cleaned)):
            if cleaned[i] == opener:
                depth += 1
            elif cleaned[i] == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(cleaned[start:i + 1])
                    except Exception:  # noqa: BLE001
                        break
    raise ValueError(f"无法解析为 JSON: {text[:200]}")


def chat_json(prompt: str, system: str = "", model: Optional[str] = None,
              temperature: float = 0.2, max_tokens: int = 4000) -> Any:
    """要求模型输出 JSON，并容错解析"""
    msgs = [{"role": "system",
             "content": (system or "你是严谨的中文内容编辑。") +
                        " 只输出 JSON，不要任何解释文字，不要 markdown 代码块。"}]
    msgs.append({"role": "user", "content": prompt})
    text = chat(msgs, model=model, temperature=temperature, max_tokens=max_tokens,
                json_mode=True)
    return _loose_json(text)
