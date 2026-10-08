"""
文章自动生成模块

使用 阿里云百炼平台 qwen-turbo 模型，将 Whisper 转写文本
转化为排版精美的结构化文章。

百炼 API 兼容 OpenAI 格式:
  base_url = https://dashscope.aliyuncs.com/compatible-mode/v1
  model   = qwen-turbo-latest  (¥0.3/百万tokens input, 极便宜)
"""
import json
import re
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
import config


class ArticleWriter:
    """将 Whisper 字幕文本整理为结构化文章"""

    def __init__(self, api_key: str = None):
        """
        Args:
            api_key: 百炼 API Key，默认从 config 读取
        """
        self.api_key = api_key or config.BAILIAN_API_KEY
        self.base_url = config.BAILIAN_BASE_URL
        self.model = config.BAILIAN_MODEL

    def generate(self, whisper_text: str, title_hint: str = "") -> Optional[dict]:
        """
        从 Whisper 转写文本生成结构化文章

        Args:
            whisper_text: Whisper 转写的完整文本
            title_hint: 可选标题提示（如视频标题）

        Returns:
            dict: {
                "title": "文章标题",
                "summary": "摘要（1-2句话）",
                "content": "格式化文章正文",
                "category": "分类标签",
                "tags": ["标签1", "标签2"]
            }
        """
        if not self.api_key:
            print("  [错误] 未配置百炼 API Key，请在 config.py 中设置 BAILIAN_API_KEY")
            return None

        if not whisper_text or len(whisper_text.strip()) < 50:
            print("  [警告] 转写文本太短，无法生成文章")
            return None

        # 截断过长的文本
        text = whisper_text[:config.ARTICLE_MAX_CHARS]

        print(f"  [文章] 输入文本长度: {len(text)} 字符")
        print(f"  [文章] 调用百炼 {self.model}...")

        # 构建 Prompt
        system_prompt = """你是一位专业的文章编辑。你的任务是将一段口语化的语音转写文本，整理为一篇结构清晰、排版精美的中文文章。

要求：
1. 修正口语化表达，转为书面语，但保留原意的核心观点和语气
2. 去除口头禅、重复、语气词（嗯、啊、这个、那个等）
3. 自动分段：按话题逻辑自然分段，每段不超过200字
4. 保留原文中的金句、重点观点，可以用**加粗**强调
5. 适当添加小标题（用 ## 标记），帮助读者快速理解内容结构
6. 正文末尾不要添加来源、栏目、时长、原视频等任何脚注或版权声明信息，文章就是完整的独立内容
7. 最后输出必须使用以下 JSON 格式：

```json
{
  "title": "吸引人的文章标题（不超过20字）",
  "summary": "文章摘要（1-2句话，概括核心观点）",
  "category": "分类（认知/处世/关系/教育/成长/生活/财富/商业/其他）",
  "tags": ["标签1", "标签2", "标签3"],
  "content": "格式化后的完整文章正文，使用 Markdown 格式"
}
```

注意：务必输出合法的 JSON，不要包含 ```json 包裹标记以外的内容。"""

        user_prompt = f"""请将以下语音转写文本整理为文章：

{title_hint}

文本内容：
{text}"""

        try:
            import httpx

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }

            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.7,
                "max_tokens": 4096,
            }

            start_time = time.time()

            with httpx.Client(timeout=120) as client:
                response = client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                result = response.json()

            elapsed = time.time() - start_time
            print(f"  [文章] LLM 响应耗时: {elapsed:.1f}s")

            # 解析返回内容
            content = result["choices"][0]["message"]["content"]

            # 使用信息
            usage = result.get("usage", {})
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            cost_input = prompt_tokens * 0.3 / 1_000_000
            cost_output = completion_tokens * 1.0 / 1_000_000
            print(f"  [文章] tokens: ↑{prompt_tokens} ↓{completion_tokens}, 估算费用: ¥{cost_input + cost_output:.6f}")

            # 从 LLM 响应中提取 JSON
            article = self._parse_response(content)
            if article:
                print(f"  [文章] ✅ 生成成功: {article.get('title', '')}")
                return article
            else:
                print(f"  [错误] 无法解析 LLM 返回的 JSON")
                print(f"  [原始响应] {content[:500]}")
                return None

        except Exception as e:
            print(f"  [错误] 百炼 API 调用失败: {e}")
            return None

    def _parse_response(self, content: str) -> Optional[dict]:
        """从 LLM 返回文本中提取 JSON"""
        # 尝试提取 ```json ... ``` 包裹的内容
        m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content, re.DOTALL)
        if m:
            json_str = m.group(1).strip()
        else:
            json_str = content.strip()

        # 尝试解析 JSON
        try:
            article = json.loads(json_str)
        except json.JSONDecodeError:
            # 尝试查找第一个 { 到最后一个 }
            start = json_str.find("{")
            end = json_str.rfind("}")
            if start >= 0 and end > start:
                try:
                    article = json.loads(json_str[start:end + 1])
                except json.JSONDecodeError:
                    return None
            else:
                return None

        # 确保必填字段存在
        if not article.get("content"):
            return None

        return {
            "title": article.get("title", "无标题"),
            "summary": article.get("summary", ""),
            "category": article.get("category", "其他"),
            "tags": article.get("tags", []),
            "content": article.get("content", ""),
        }

    @staticmethod
    def read_srt_text(srt_path: str | Path) -> str:
        """
        从 SRT 字幕文件中提取纯文本内容

        Args:
            srt_path: SRT 字幕文件路径

        Returns:
            纯文本内容（去掉了时间戳和序号）
        """
        srt_path = Path(srt_path)
        if not srt_path.exists():
            return ""

        text = srt_path.read_text(encoding="utf-8")

        # 去掉 SRT 序号、时间戳、空行
        lines = text.splitlines()
        text_lines = []
        for line in lines:
            line = line.strip()
            # 跳过纯数字行（序号）
            if line.isdigit():
                continue
            # 跳过时间戳行
            if "-->" in line or re.match(r'^\d{2}:\d{2}:\d{2}', line):
                continue
            if line:
                text_lines.append(line)

        return "\n".join(text_lines)

    @staticmethod
    def read_whisper_json(json_path: str | Path) -> str:
        """
        从 Whisper JSON 输出中提取文本

        如果 faster-whisper 输出为 JSON 格式
        """
        json_path = Path(json_path)
        if not json_path.exists():
            return ""

        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return " ".join(seg.get("text", "") for seg in data)
            elif isinstance(data, dict):
                return data.get("text", "")
        except:
            pass

        return ""


# ===== 批量处理入口 =====
def batch_generate(srt_files: list[str | Path], output_dir: str | Path) -> list:
    """
    批量从 SRT 文件生成文章

    Args:
        srt_files: SRT 文件路径列表
        output_dir: 文章输出目录

    Returns:
        生成的文章信息列表
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    writer = ArticleWriter()
    results = []

    for i, srt_path in enumerate(srt_files, 1):
        srt_path = Path(srt_path)
        txt_path = output_dir / (srt_path.stem + ".txt")

        if txt_path.exists():
            print(f"  [{i}/{len(srt_files)}] 跳过（已存在）: {txt_path.name}")
            article = json.loads(txt_path.read_text(encoding="utf-8"))
            results.append(article)
            continue

        print(f"\n[{i}/{len(srt_files)}] 处理: {srt_path.name}")

        # 读取转写文本
        whisper_text = ArticleWriter.read_srt_text(srt_path)
        if not whisper_text:
            print(f"  [跳过] SRT 为空: {srt_path.name}")
            continue

        # 生成文章
        article = writer.generate(whisper_text)
        if not article:
            continue

        # 保存为 JSON（含完整文章信息）和纯文本

        # 1. 保存完整 article JSON
        json_path = txt_path  # 保存为 .txt 但实际是 JSON
        article_path = txt_path.with_suffix(".article.json")

        # 保存元数据 JSON（title, summary, content 等）
        with open(article_path, "w", encoding="utf-8") as f:
            json.dump(article, f, ensure_ascii=False, indent=2)

        # 2. 纯文本版（前端 articleUrl 引用 .txt）
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(article["content"])

        results.append(article)
        print(f"  [完成] 文章已保存: {txt_path.name}")

    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="从 SRT 字幕生成文章")
    parser.add_argument("srt", nargs="*", help="SRT 文件路径（可多个）")
    parser.add_argument("-d", "--dir", help="SRT 目录（批量处理该目录下所有 .srt）")
    args = parser.parse_args()

    srt_files = []
    if args.dir:
        srt_dir = Path(args.dir)
        srt_files.extend(sorted(srt_dir.glob("*.srt")))
    elif args.srt:
        srt_files = [Path(s) for s in args.srt]

    if not srt_files:
        # 默认从 config 的 subtitles 目录读取
        srt_files = sorted(config.SUBTITLES_DIR.glob("*.srt"))
        if not srt_files:
            print("没有找到 SRT 文件")
            print("用法: python article_writer.py <srt文件1> <srt文件2> ...")
            print("  或: python article_writer.py --dir <srt目录>")
            sys.exit(1)

    articles = batch_generate(srt_files, config.ARTICLES_DIR)
    print(f"\n✅ 共生成 {len(articles)} 篇文章")
