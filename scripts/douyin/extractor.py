"""
音频提取模块 - 使用 ffmpeg 从视频中提取音频为 MP3
"""
import subprocess
import os
import sys
from pathlib import Path
from typing import Optional


class AudioExtractor:
    """从视频中提取音频，输出 MP3 文件"""

    def __init__(self, mp3_quality: int = 2, delete_video: bool = True,
                 loudnorm: bool = True, target_lufs: float = -14.0):
        """
        Args:
            mp3_quality: ffmpeg -q:a 参数 (0=最高 9=最低, 2=高质量约190kbps)
            delete_video: 提取完成后是否删除视频文件
            loudnorm: 是否启用 loudnorm 音量标准化 (推荐 True)
            target_lufs: 目标响度 (LUFS), 播客推荐 -16~-14, 默认 -14
        """
        self.mp3_quality = mp3_quality
        self.delete_video = delete_video
        self.loudnorm = loudnorm
        self.target_lufs = target_lufs
        self._ffmpeg_path = self._find_ffmpeg()

    def _find_ffmpeg(self) -> str:
        """查找 ffmpeg 可执行文件"""
        # 优先用 PATH 中的 ffmpeg
        result = subprocess.run(
            ["which", "ffmpeg"],
            capture_output=True, text=True
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()

        # 回退到固定路径
        fallback = "/Volumes/cc/dev/ffmpeg/ffmpeg"
        if os.path.isfile(fallback) and os.access(fallback, os.X_OK):
            return fallback

        raise FileNotFoundError(
            "找不到 ffmpeg，请确认已安装并配置到 PATH。\n"
            "安装方法: brew install ffmpeg 或从 https://osxexperts.net 下载 ARM 版"
        )

    def extract(self, video_path: str | Path, output_dir: str | Path) -> Optional[Path]:
        """
        从视频提取音频

        Args:
            video_path: 视频文件路径
            output_dir: MP3 输出目录

        Returns:
            MP3 文件路径，失败返回 None
        """
        video_path = Path(video_path)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # 输出文件名与视频同名，扩展名改为 .mp3
        mp3_path = output_dir / (video_path.stem + ".mp3")

        if mp3_path.exists():
            print(f"  [跳过] 音频已存在: {mp3_path.name}")
            if self.delete_video and video_path.exists():
                video_path.unlink()
            return mp3_path

        if not video_path.exists():
            print(f"  [错误] 视频文件不存在: {video_path}")
            return None

        # ffmpeg 命令: 去掉视频画面，只提取音频，编码为 MP3
        cmd = [
            self._ffmpeg_path,
            "-i", str(video_path),        # 输入文件
            "-vn",                         # 去掉视频画面
            "-acodec", "libmp3lame",       # MP3 编码器
        ]

        # 音量标准化 (loudnorm): 提升音量到播客标准
        if self.loudnorm:
            cmd.extend(["-af", f"loudnorm=I={self.target_lufs}:TP=-1.5:LRA=11"])

        cmd.extend([
            "-q:a", str(self.mp3_quality), # 音频质量
            "-y",                          # 覆盖已存在文件
            str(mp3_path)
        ])

        print(f"  [提取] {video_path.name} → {mp3_path.name}")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300
        )

        if result.returncode != 0:
            print(f"  [错误] ffmpeg 提取失败: {result.stderr[-500:]}")
            return None

        if not mp3_path.exists():
            print(f"  [错误] MP3 文件未生成: {mp3_path}")
            return None

        # 删除视频文件
        if self.delete_video and video_path.exists():
            video_path.unlink()
            print(f"  [清理] 已删除视频: {video_path.name}")

        return mp3_path

    def extract_batch(self, video_paths: list, output_dir: str | Path) -> list:
        """
        批量提取音频

        Args:
            video_paths: 视频文件路径列表
            output_dir: MP3 输出目录

        Returns:
            成功提取的 MP3 文件路径列表
        """
        results = []
        total = len(video_paths)

        for i, video_path in enumerate(video_paths, 1):
            print(f"\n[{i}/{total}] 处理: {Path(video_path).name}")
            mp3_path = self.extract(video_path, output_dir)
            if mp3_path:
                results.append(mp3_path)

        return results

    def boost_batch(self, mp3_dir: str | Path) -> list:
        """
        对已有的 MP3 文件批量应用音量增益 (loudnorm)

        原地处理: 读取 → loudnorm → 覆盖写回

        Args:
            mp3_dir: MP3 文件所在目录

        Returns:
            成功处理的文件路径列表
        """
        mp3_dir = Path(mp3_dir)
        mp3_files = sorted(mp3_dir.glob("*.mp3"))
        if not mp3_files:
            print("  [提示] 没有找到 MP3 文件")
            return []

        results = []
        total = len(mp3_files)

        for i, mp3_path in enumerate(mp3_files, 1):
            print(f"\n[{i}/{total}] 增益: {mp3_path.name}")

            # 写入临时文件再替换，避免损坏原文件
            tmp_path = mp3_path.with_suffix(".tmp.mp3")

            cmd = [
                self._ffmpeg_path,
                "-i", str(mp3_path),
                "-af", f"loudnorm=I={self.target_lufs}:TP=-1.5:LRA=11",
                "-acodec", "libmp3lame",
                "-q:a", str(self.mp3_quality),
                "-y",
                str(tmp_path),
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

            if result.returncode != 0:
                print(f"  [错误] 增益失败: {result.stderr[-300:]}")
                tmp_path.unlink(missing_ok=True)
                continue

            if not tmp_path.exists():
                print(f"  [错误] 输出文件未生成")
                continue

            # 替换原文件
            tmp_path.replace(mp3_path)
            print(f"  [完成] 已提升音量 → {self.target_lufs} LUFS")
            results.append(mp3_path)

        return results
