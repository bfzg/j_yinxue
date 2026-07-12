"""
抖音视频爬虫模块

使用 f2 库爬取抖音用户主页的所有视频并下载。
f2 是专门针对抖音的 Python 爬虫库，自动处理 X-Bogus 签名等反爬机制。

文档: https://github.com/Johnserf-Seed/f2
"""
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional


class DouyinScraper:
    """抖音视频爬虫"""

    def __init__(self, cookie: str, output_dir: str | Path, max_videos: int = 100):
        """
        Args:
            cookie: 抖音登录 Cookie 字符串
            output_dir: 视频下载输出目录
            max_videos: 最多下载视频数量 (0 = 不限)
        """
        self.cookie = cookie
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.max_videos = max_videos

    def scrape_user_videos(self, sec_user_id: str) -> list[dict]:
        """
        爬取并下载用户的所有视频

        Args:
            sec_user_id: 抖音用户 sec_user_id (URL 最后一段)

        Returns:
            下载的视频信息列表 [{"aweme_id", "desc", "video_path"}]
        """
        print(f"  [爬虫] 目标用户: {sec_user_id}")
        print(f"  [爬虫] 输出目录: {self.output_dir}")
        print(f"  [爬虫] 最大数量: {self.max_videos or '不限'}")

        # 优先用 f2 库 (更灵活，能拿到视频元数据)
        try:
            return asyncio.run(self._scrape_with_f2_lib(sec_user_id))
        except Exception as e:
            print(f"  [爬虫] f2 库方式失败: {e}")
            print(f"  [爬虫] 尝试 f2 CLI 方式...")

        # 回退到 f2 CLI
        try:
            return self._scrape_with_f2_cli(sec_user_id)
        except Exception as e:
            print(f"  [爬虫] f2 CLI 方式也失败: {e}")
            print(f"  [爬虫] 请检查 Cookie 是否有效，以及 f2 是否正确安装")
            return []

    # ========== 方式一: f2 库 (推荐) ==========

    async def _scrape_with_f2_lib(self, sec_user_id: str) -> list[dict]:
        """使用 f2 Python 库异步爬取"""
        from f2.apps.douyin.handler import DouyinHandler

        handler = DouyinHandler()

        # 设置 Cookie
        if hasattr(handler, 'set_cookie'):
            handler.set_cookie(self.cookie)
        elif hasattr(handler, 'cookie'):
            handler.cookie = self.cookie

        videos = []
        max_cursor = 0
        page = 1

        while len(videos) < self.max_videos or self.max_videos == 0:
            print(f"\n  [爬虫] 获取第 {page} 页...")

            # 调用 f2 获取用户视频列表
            result = await self._fetch_user_post(handler, sec_user_id, max_cursor)

            if not result:
                print(f"  [爬虫] 没有更多视频了")
                break

            # 解析视频列表
            aweme_list = self._extract_aweme_list(result)
            if not aweme_list:
                print(f"  [爬虫] 第 {page} 页没有视频数据")
                break

            for aweme in aweme_list:
                if 0 < self.max_videos <= len(videos):
                    break

                video_info = self._parse_aweme(aweme)
                if not video_info:
                    continue

                # 下载视频
                video_path = await self._download_video(handler, video_info)
                if video_path:
                    videos.append(video_info)
                    desc_short = video_info["desc"][:30] if video_info["desc"] else video_info["aweme_id"]
                    print(f"  [下载 {len(videos)}] {desc_short}")

            # 更新游标用于翻页
            next_cursor = self._extract_next_cursor(result)
            if next_cursor is None or next_cursor == max_cursor:
                print(f"  [爬虫] 没有更多视频了")
                break
            max_cursor = next_cursor
            page += 1

            # 礼貌延迟
            await asyncio.sleep(1)

        return videos

    async def _fetch_user_post(self, handler, sec_user_id: str, max_cursor: int):
        """调用 f2 获取用户视频列表 (兼容不同版本 API)"""
        # 尝试不同的方法名和参数 (f2 不同版本 API 有差异)
        attempts = [
            ("fetch_user_post", {"sec_user_id": sec_user_id, "max_cursor": max_cursor, "count": 20}),
            ("fetch_user_post_videos", {"sec_user_id": sec_user_id, "max_cursor": max_cursor, "count": 20}),
            ("get_user_posts", {"sec_user_id": sec_user_id, "max_cursor": max_cursor, "count": 20}),
            ("fetch_user_post", {"sec_user_id": sec_user_id, "max_cursor": max_cursor}),
        ]

        for method_name, kwargs in attempts:
            method = getattr(handler, method_name, None)
            if method is None:
                continue
            try:
                result = await method(**kwargs)
                if result:
                    return result
            except TypeError:
                continue
            except Exception as e:
                print(f"  [爬虫] {method_name} 调用失败: {e}")
                continue

        raise RuntimeError(f"无法调用 f2 获取视频列表，请检查 f2 版本和 API")

    def _extract_aweme_list(self, result) -> list:
        """从 f2 返回结果中提取视频列表 (兼容不同返回格式)"""
        # 结果可能是对象、字典或列表
        if isinstance(result, list):
            return result

        if isinstance(result, dict):
            return result.get("aweme_list", result.get("data", []))

        # 对象格式
        for attr in ["aweme_list", "data", "videos", "list"]:
            if hasattr(result, attr):
                val = getattr(result, attr)
                if val:
                    return val if isinstance(val, list) else [val]

        return []

    def _extract_next_cursor(self, result) -> Optional[int]:
        """从结果中提取下一页游标"""
        if isinstance(result, dict):
            return result.get("max_cursor") or result.get("next_cursor")

        for attr in ["max_cursor", "next_cursor", "cursor"]:
            if hasattr(result, attr):
                val = getattr(result, attr)
                if val is not None:
                    return int(val) if val else 0

        return None

    def _parse_aweme(self, aweme) -> Optional[dict]:
        """解析单个视频信息 (兼容对象和字典格式)"""
        def get(obj, key, default=None):
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)

        aweme_id = get(aweme, "aweme_id") or get(aweme, "id")
        if not aweme_id:
            return None

        desc = get(aweme, "desc") or get(aweme, "title") or ""

        # 视频下载 URL
        video_url = self._extract_video_url(aweme)

        # 创建时间
        create_time = get(aweme, "create_time", 0)

        return {
            "aweme_id": str(aweme_id),
            "desc": desc,
            "video_url": video_url,
            "create_time": create_time,
            "video_path": str(self.output_dir / f"{aweme_id}.mp4"),
        }

    def _extract_video_url(self, aweme) -> str:
        """从视频信息中提取下载 URL"""
        def get(obj, key, default=None):
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)

        # 尝试多种路径提取视频 URL
        video = get(aweme, "video") or get(aweme, "video_info")
        if video:
            play_addr = get(video, "play_addr") or get(video, "download_addr")
            if play_addr:
                url_list = get(play_addr, "url_list", [])
                if url_list:
                    return url_list[0] if isinstance(url_list, list) else url_list

        # 直接属性
        for key in ["download_url", "play_url", "video_url"]:
            url = get(aweme, key)
            if url:
                return url

        return ""

    async def _download_video(self, handler, video_info: dict) -> Optional[Path]:
        """下载单个视频"""
        video_path = Path(video_info["video_path"])

        # 已下载则跳过
        if video_path.exists():
            print(f"  [跳过] 已存在: {video_path.name}")
            return video_path

        url = video_info.get("video_url")
        if not url:
            print(f"  [错误] 无下载 URL: {video_info['aweme_id']}")
            return None

        # 尝试用 f2 下载
        try:
            download_method = getattr(handler, "download_video", None)
            if download_method:
                await download_method(url=url, filepath=str(video_path))
                if video_path.exists():
                    return video_path
        except Exception as e:
            print(f"  [警告] f2 下载失败: {e}")

        # 回退到直接 HTTP 下载
        return await self._http_download(url, video_path)

    async def _http_download(self, url: str, filepath: Path) -> Optional[Path]:
        """直接 HTTP 下载视频"""
        import httpx

        headers = {
            "Cookie": self.cookie,
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.douyin.com/",
        }

        try:
            async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()

                filepath.write_bytes(response.content)
                print(f"  [下载完成] {filepath.name} ({response.content.__sizeof__() // 1024}KB)")
                return filepath

        except Exception as e:
            print(f"  [错误] HTTP 下载失败: {e}")
            return None

    # ========== 方式二: f2 CLI (回退) ==========

    def _scrape_with_f2_cli(self, sec_user_id: str) -> list[dict]:
        """使用 f2 CLI 命令行工具爬取"""
        user_url = f"https://www.douyin.com/user/{sec_user_id}"
        cmd = self._build_f2_cmd(user_url)

        print(f"  [f2 CLI] 执行下载...")
        print(f"  [f2 CLI] 模式: post (主页作品)")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
            cwd=str(self.output_dir),
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"f2 CLI 退出码 {result.returncode}\n"
                f"stdout: {result.stdout[-500:]}\n"
                f"stderr: {result.stderr[-500:]}"
            )

        # f2 下载的视频可能保存在子目录中，递归查找
        videos = []
        for f in self.output_dir.rglob("*.mp4"):
            videos.append({
                "aweme_id": f.stem,
                "desc": f.stem,
                "video_path": str(f),
            })

        return videos

    def _build_f2_cmd(self, user_url: str) -> list[str]:
        """构建 f2 CLI 命令，返回 list[str] 格式供 subprocess.run 使用"""
        base_cmd = self._find_f2_cmd_base()

        cmd = base_cmd + [
            "dy",
            "-u", user_url,
            "-k", self.cookie,          # Cookie 字符串直接传入
            "-p", str(self.output_dir),  # 保存路径
            "-M", "post",                # 下载模式: 主页作品
            "-f", "false",               # 不创建单独文件夹
        ]

        if self.max_videos > 0:
            cmd.extend(["-o", str(self.max_videos)])  # 最大下载数

        return cmd

    def _find_f2_cmd_base(self) -> list[str]:
        """查找 f2 可执行文件，返回命令前缀 list"""
        # venv 中的 f2
        venv_f2 = Path(__file__).parent / ".venv" / "bin" / "f2"
        if venv_f2.exists():
            return [str(venv_f2)]

        # PATH 中的 f2
        result = subprocess.run(["which", "f2"], capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            return [result.stdout.strip()]

        # 用 python -m f2
        return [sys.executable, "-m", "f2"]
