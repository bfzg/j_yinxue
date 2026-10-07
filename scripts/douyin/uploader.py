"""
COS 上传 + 前端 JSON 更新模块

功能:
1. 上传音频 MP3 到腾讯云 COS
2. 上传文章 .txt 到腾讯云 COS
3. 更新 articles.json
4. 更新 playlist.json

前置条件:
- 腾讯云 COS 已经配置好（Bucket 已存在）
- 需要 COS 的密钥（SecretId + SecretKey）
- 项目中已有 mp3 音频和 .txt 文章
"""
import json
import sys
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
import config


_MIME = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac",
         ".txt": "text/plain; charset=utf-8", ".json": "application/json"}


def _guess_type(path: Path) -> str:
    """按扩展名猜 MIME，未知一律 octet-stream"""
    return _MIME.get(Path(path).suffix.lower(), "application/octet-stream")


class CosUploader:
    """腾讯云 COS 上传工具"""

    def __init__(self, secret_id: str = None, secret_key: str = None):
        """
        Args:
            secret_id: 腾讯云 SecretId（可选，默认从 COS 工具链自动获取）
            secret_key: 腾讯云 SecretKey（可选）
        """
        self.secret_id = secret_id
        self.secret_key = secret_key
        self._client = None

    def _get_client(self):
        """获取 COS 客户端"""
        if self._client:
            return self._client

        try:
            from qcloud_cos import CosConfig, CosS3Client
        except ImportError:
            print("  [错误] 需要安装 cos-python-sdk-v5: pip install cos-python-sdk-v5")
            raise

        # 优先使用环境变量中的密钥
        import os
        secret_id = self.secret_id or os.environ.get("COS_SECRET_ID") or os.environ.get("TENCENTCLOUD_SECRET_ID")
        secret_key = self.secret_key or os.environ.get("COS_SECRET_KEY") or os.environ.get("TENCENTCLOUD_SECRET_KEY")

        if not secret_id or not secret_key:
            print("  [警告] 未配置 COS 密钥！")
            print("  COS 密钥可通过以下方式设置：")
            print("    1. 环境变量: COS_SECRET_ID / COS_SECRET_KEY")
            print("    2. 或在 uploader.py 中直接传入 secret_id/secret_key")
            print("  [回退] 跳过 COS 上传，仅更新本地 JSON")
            return None

        config_ = CosConfig(
            Region=config.COS_REGION,
            SecretId=secret_id,
            SecretKey=secret_key,
        )
        self._client = CosS3Client(config_)
        return self._client

    def upload_file(self, local_path: str | Path, cos_key: str,
                    content_type: str = "") -> bool:
        """
        上传单个文件到 COS

        Args:
            local_path: 本地文件路径
            cos_key: COS 上的对象键（如 jiugeyinxue/xxx.m4a）
            content_type: 显式 MIME。m4a 留空会被 COS 标成 octet-stream，
                          iOS / H5 的 audio 标签可能因此拒播

        Returns:
            是否上传成功
        """
        local_path = Path(local_path)
        if not local_path.exists():
            print(f"  [错误] 文件不存在: {local_path}")
            return False

        client = self._get_client()
        if not client:
            return False

        try:
            # 检查文件是否已存在（通过 ETag 比较，简化：先尝试 Head）
            try:
                head = client.head_object(
                    Bucket=config.COS_BUCKET,
                    Key=cos_key,
                )
                # 已存在，对比大小决定是否覆盖
                remote_size = int(head.get("Content-Length", 0))
                local_size = local_path.stat().st_size
                if remote_size == local_size:
                    print(f"  [跳过] COS 已存在且大小一致: {cos_key}")
                    return True
            except:
                pass  # 文件不存在，继续上传

            print(f"  [上传] {local_path.name} → cos://{cos_key}")
            response = client.upload_file(
                Bucket=config.COS_BUCKET,
                Key=cos_key,
                LocalFilePath=str(local_path),
                EnableMD5=True,
                ContentType=content_type or _guess_type(local_path),
            )
            print(f"  [完成] {cos_key}")
            return True

        except Exception as e:
            print(f"  [错误] COS 上传失败: {cos_key}: {e}")
            return False

    def upload_mp3(self, mp3_path: str | Path) -> Optional[str]:
        """
        上传音频文件到 COS

        Returns:
            COS 上的 URL，上传失败返回 None
        """
        mp3_path = Path(mp3_path)
        cos_key = f"{config.COS_AUDIO_PREFIX}/{urllib.parse.quote(mp3_path.name)}"
        success = self.upload_file(mp3_path, cos_key)
        if success:
            return f"{config.COS_BASE_URL}/{urllib.parse.quote(mp3_path.name)}"
        return None

    def upload_txt(self, txt_path: str | Path) -> Optional[str]:
        """
        上传文章 .txt 文件到 COS

        Returns:
            COS 上的 URL，上传失败返回 None
        """
        txt_path = Path(txt_path)
        cos_key = f"{config.COS_TXT_PREFIX}/{urllib.parse.quote(txt_path.name)}"
        success = self.upload_file(txt_path, cos_key)
        if success:
            return f"{config.COS_BASE_URL}/txt/{urllib.parse.quote(txt_path.name)}"
        return None


class JsonUpdater:
    """更新前端的 articles.json 和 playlist.json"""

    def __init__(self):
        self.articles_path = Path(__file__).parent.parent.parent / "src" / "static" / "data" / "articles.json"
        self.playlist_path = Path(__file__).parent.parent.parent / "src" / "static" / "data" / "playlist.json"

    def add_or_update_article(self, article_info: dict) -> bool:
        """
        添加或更新文章到 articles.json

        article_info 格式:
        {
            "id": "ep014",                    // 自动生成
            "title": "文章标题",
            "summary": "摘要",
            "category": "分类",
            "cover": "",
            "publishedAt": "2026-07-12",
            "articleUrl": "COS URL",
            "audioUrl": "COS URL",
            "author": "",
            "enabled": True,
            "sort": 14,
        }
        """
        articles = self._load_json(self.articles_path)

        # 生成新 ID
        existing_ids = [item.get("id", "") for item in articles.get("items", [])]
        max_num = 0
        for aid in existing_ids:
            if aid.startswith("ep") and aid[2:].isdigit():
                max_num = max(max_num, int(aid[2:]))
        new_num = max_num + 1
        new_id = f"ep{new_num:03d}"

        # 构建文章条目
        entry = {
            "id": new_id,
            "title": article_info.get("title", ""),
            "summary": article_info.get("summary", ""),
            "category": article_info.get("category", "其他"),
            "cover": article_info.get("cover", ""),
            "publishedAt": article_info.get("publishedAt", datetime.now().strftime("%Y-%m-%d")),
            "articleUrl": article_info.get("articleUrl", ""),
            "audioUrl": article_info.get("audioUrl", ""),
            "author": article_info.get("author", ""),
            "enabled": True,
            "sort": new_num,
        }

        # 添加到列表
        articles.setdefault("items", []).append(entry)

        # 写入
        self._save_json(self.articles_path, articles)
        print(f"  [articles.json] ✅ 已添加: {entry['title']} ({entry['id']})")
        return True

    def add_or_update_playlist(self, audio_info: dict) -> bool:
        """
        添加或更新音频到 playlist.json

        audio_info 格式:
        {
            "title": "标题",
            "audioUrl": "COS URL",
            "duration": 865,         // 秒
            "publishedAt": "2026-07-12",
        }
        """
        playlist = self._load_json(self.playlist_path)

        # 生成新 ID
        existing_ids = [item.get("id", "") for item in playlist.get("items", [])]
        max_num = 0
        for pid in existing_ids:
            if pid.startswith("ep") and pid[2:].isdigit():
                max_num = max(max_num, int(pid[2:]))
        new_num = max_num + 1
        new_id = f"ep{new_num:03d}"

        entry = {
            "id": new_id,
            "title": audio_info.get("title", ""),
            "description": audio_info.get("description", ""),
            "cover": audio_info.get("cover", ""),
            "audioUrl": audio_info.get("audioUrl", ""),
            "duration": audio_info.get("duration", 0),
            "sort": new_num,
            "publishedAt": audio_info.get("publishedAt", datetime.now().strftime("%Y-%m-%d")),
            "enabled": True,
        }

        playlist.setdefault("items", []).append(entry)

        self._save_json(self.playlist_path, playlist)
        print(f"  [playlist.json] ✅ 已添加: {entry['title']} ({entry['id']})")
        return True

    def _load_json(self, path: Path) -> dict:
        """加载 JSON 文件"""
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except:
                return {"app": {}, "items": []}
        return {"app": {}, "items": []}

    def _save_json(self, path: Path, data: dict):
        """保存 JSON 文件"""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  [保存] {path.relative_to(Path(__file__).parent.parent.parent)}")


def publish(article_json_path: str | Path, mp3_path: str | Path = None,
            secret_id: str = None, secret_key: str = None) -> bool:
    """
    一站式发布：上传文章 + 上传音频 → 更新 JSON

    Args:
        article_json_path: 文章元数据 JSON 路径（article_writer.py 输出的 .article.json）
        mp3_path: 对应音频 MP3 路径（可选，已有音频时可跳过）
        secret_id: COS SecretId（可选）
        secret_key: COS SecretKey（可选）

    Returns:
        是否全部成功
    """
    article_json_path = Path(article_json_path)
    if not article_json_path.exists():
        print(f"  [错误] 文章 JSON 不存在: {article_json_path}")
        return False

    article_data = json.loads(article_json_path.read_text(encoding="utf-8"))
    txt_path = article_json_path.with_suffix(".txt")

    uploader = CosUploader(secret_id, secret_key)
    updater = JsonUpdater()

    # 1. 上传文章 .txt
    article_url = None
    if txt_path.exists():
        article_url = uploader.upload_txt(txt_path)

    # 2. 上传音频
    audio_url = None
    if mp3_path:
        mp3_path = Path(mp3_path)
        if mp3_path.exists():
            audio_url = uploader.upload_mp3(mp3_path)

    # 3. 更新 articles.json
    updater.add_or_update_article({
        "title": article_data.get("title", ""),
        "summary": article_data.get("summary", ""),
        "category": article_data.get("category", "其他"),
        "publishedAt": datetime.now().strftime("%Y-%m-%d"),
        "articleUrl": article_url or "",
        "audioUrl": audio_url or "",
    })

    # 4. 更新 playlist.json
    if audio_url:
        duration = 0
        if mp3_path:
            duration = _get_duration(mp3_path)
        updater.add_or_update_playlist({
            "title": article_data.get("title", ""),
            "audioUrl": audio_url,
            "duration": duration,
            "publishedAt": datetime.now().strftime("%Y-%m-%d"),
        })

    print(f"\n✅ 发布完成!")
    print(f"  文章: {article_url or '未上传'}")
    print(f"  音频: {audio_url or '未上传'}")
    return True


def _get_duration(mp3_path: str | Path) -> int:
    """获取音频时长（秒）"""
    import subprocess as sp
    import re

    mp3_path = Path(mp3_path)
    if not mp3_path.exists():
        return 0

    # 查找 ffmpeg
    ffmpeg_bin = "ffmpeg"
    result = sp.run(["which", "ffmpeg"], capture_output=True, text=True)
    if result.returncode != 0 or not result.stdout.strip():
        ffmpeg_bin = "/Volumes/cc/dev/ffmpeg/ffmpeg"

    try:
        result = sp.run([ffmpeg_bin, "-i", str(mp3_path)], capture_output=True, text=True, timeout=30)
        match = re.search(r"Duration:\s+(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
        if match:
            h, m, s = match.groups()
            return int(int(h) * 3600 + int(m) * 60 + float(s))
    except:
        pass
    return 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="发布文章和音频到 COS + 更新 JSON")
    parser.add_argument("article_json", help="文章元数据 JSON 路径 (*.article.json)")
    parser.add_argument("--mp3", help="对应音频 MP3 路径")
    parser.add_argument("--secret-id", help="COS SecretId")
    parser.add_argument("--secret-key", help="COS SecretKey")
    args = parser.parse_args()

    publish(
        article_json_path=args.article_json,
        mp3_path=args.mp3,
        secret_id=args.secret_id,
        secret_key=args.secret_key,
    )
