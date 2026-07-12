"""
抖音视频 → 播客音频 → 字幕
主流程入口

用法:
    # 使用 venv 中的 python
    .venv/bin/python main.py

    # 或先激活 venv
    source .venv/bin/activate
    python main.py

    # 只爬取下载（不提取音频、不生成字幕）
    python main.py --step scrape

    # 只爬取 2 个视频测试
    python main.py --step scrape --max 2

    # 只提取音频（从已下载的视频中）
    python main.py --step extract

    # 对已有 MP3 批量音量增益
    python main.py --step boost

    # 只生成字幕（从已有的 MP3 中）
    python main.py --step transcribe

    # 生成 playlist.json
    python main.py --step playlist
"""
import argparse
import json
import sys
import time
from pathlib import Path

# 确保能导入同目录模块
sys.path.insert(0, str(Path(__file__).parent))

import config


def load_metadata() -> list:
    """加载已处理的视频元数据"""
    if config.METADATA_FILE.exists():
        with open(config.METADATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_metadata(metadata: list):
    """保存元数据"""
    config.METADATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(config.METADATA_FILE, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)


def step_scrape() -> list:
    """第一步: 爬取并下载视频"""
    from scraper import DouyinScraper

    cookie = config.get_cookie()

    scraper = DouyinScraper(
        cookie=cookie,
        output_dir=config.VIDEOS_DIR,
        max_videos=config.MAX_VIDEOS,
    )

    print("=" * 60)
    print("  第一步: 爬取抖音视频")
    print("=" * 60)

    videos = scraper.scrape_user_videos(config.SEC_USER_ID)

    print(f"\n  完成: 共下载 {len(videos)} 个视频")
    return videos


def step_extract(videos: list = None) -> list:
    """第二步: 提取音频"""
    from extractor import AudioExtractor

    # 如果没有传入视频列表，从目录中递归扫描
    if not videos:
        video_files = sorted(config.VIDEOS_DIR.rglob("*.mp4"))
        if not video_files:
            print("\n  [提示] 没有找到视频文件，请先运行 --step scrape")
            return []
        videos = [{"video_path": str(v)} for v in video_files]

    extractor = AudioExtractor(
        mp3_quality=config.MP3_QUALITY,
        delete_video=config.DELETE_VIDEO_AFTER_EXTRACT,
    )

    print("\n" + "=" * 60)
    print("  第二步: 提取音频 (视频 → MP3)")
    print("=" * 60)

    video_paths = [v["video_path"] if isinstance(v, dict) else str(v) for v in videos]
    audio_files = extractor.extract_batch(video_paths, config.AUDIO_DIR)

    print(f"\n  完成: 共提取 {len(audio_files)} 个音频文件")
    return audio_files


def step_boost() -> list:
    """对已有的 MP3 文件批量应用音量增益"""
    from extractor import AudioExtractor

    extractor = AudioExtractor(
        mp3_quality=config.MP3_QUALITY,
        loudnorm=True,
        target_lufs=-14.0,
    )

    print("\n" + "=" * 60)
    print("  音量增益 (loudnorm → -14 LUFS)")
    print("=" * 60)

    audio_files = extractor.boost_batch(config.AUDIO_DIR)

    print(f"\n  完成: 共处理 {len(audio_files)} 个音频文件")
    return audio_files


def step_playlist():
    """根据音频文件生成 playlist.json"""
    import urllib.parse
    from datetime import datetime

    print("\n" + "=" * 60)
    print("  生成 playlist.json")
    print("=" * 60)

    audio_files = sorted(config.AUDIO_DIR.glob("*.mp3"))
    if not audio_files:
        print("  [提示] 没有找到音频文件，请先运行 --step extract")
        return

    # COS 配置
    cos_base = "https://audio-1256405210.cos.ap-shanghai.myqcloud.com/jiugeyinxue/"

    # 读取现有 playlist 保留 app/settings 部分
    playlist_path = Path(__file__).parent.parent.parent / "src" / "static" / "data" / "playlist.json"
    if playlist_path.exists():
        existing = json.loads(playlist_path.read_text(encoding="utf-8"))
    else:
        existing = {"app": {}, "settings": {}, "items": []}

    # 解析音频文件名，提取标题和日期
    items = []
    for i, mp3_path in enumerate(audio_files, 1):
        name = mp3_path.stem  # 不含扩展名

        # 文件名格式: 2024-05-11 21-46-58_道德经解读与实际运用_#天涯神贴_#认知_#道德经_video
        # 提取日期
        date_str = ""
        title = name
        if name[:4].isdigit() and len(name) > 19:
            date_str = name[:10]  # 2024-05-11
            # 去掉 "2024-05-11 21-46-58_" 前缀 (前19字符)
            title_part = name[19:]
            # 去掉 _video 后缀
            title_part = title_part.replace("_video", "")
            # 去掉 # 标签部分: 取第一个 _# 之前的内容
            if "_#" in title_part:
                title = title_part.split("_#")[0].strip()
            elif "#" in title_part:
                title = title_part.split("#")[0].strip()
            else:
                title = title_part.strip()
            # 去掉首尾的下划线
            title = title.strip("_").strip()

        # URL 编码文件名
        url_filename = urllib.parse.quote(name + ".mp3")
        audio_url = cos_base + url_filename

        # 获取时长 (用 ffprobe)
        duration = _get_duration(mp3_path)

        item = {
            "id": f"ep{i:03d}",
            "title": title,
            "description": "",
            "cover": "",
            "audioUrl": audio_url,
            "duration": duration,
            "sort": i,
            "publishedAt": date_str,
            "enabled": True,
        }
        items.append(item)
        print(f"  [{i:03d}] {title} ({duration}s)")

    # 合并: 保留原有 app/settings，替换 items
    existing["items"] = items

    # 写入 playlist.json
    playlist_path.parent.mkdir(parents=True, exist_ok=True)
    playlist_path.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"\n  已生成: {playlist_path}")
    print(f"  共 {len(items)} 条音频")


def _get_duration(mp3_path: Path) -> int:
    """用 ffmpeg 获取音频时长（秒）"""
    import subprocess as sp
    import re

    # 查找 ffmpeg 路径
    ffmpeg_bin = "ffmpeg"
    result = sp.run(["which", "ffmpeg"], capture_output=True, text=True)
    if result.returncode != 0 or not result.stdout.strip():
        ffmpeg_bin = "/Volumes/cc/dev/ffmpeg/ffmpeg"

    try:
        result = sp.run(
            [ffmpeg_bin, "-i", str(mp3_path)],
            capture_output=True, text=True, timeout=30
        )
        # ffmpeg 把信息输出到 stderr
        output = result.stderr
        # 匹配 "Duration: 00:14:25.10"
        match = re.search(r"Duration:\s+(\d+):(\d+):(\d+(?:\.\d+)?)", output)
        if match:
            h, m, s = match.groups()
            return int(int(h) * 3600 + int(m) * 60 + float(s))
    except Exception:
        pass
    return 0


def step_transcribe(audio_files: list = None) -> list:
    """第三步: 生成字幕"""
    from transcriber import SubtitleTranscriber

    # 如果没有传入音频列表，从目录中扫描
    if not audio_files:
        audio_files = sorted(config.AUDIO_DIR.glob("*.mp3"))
        if not audio_files:
            print("\n  [提示] 没有找到音频文件，请先运行 --step extract")
            return []
        audio_files = [str(a) for a in audio_files]

    transcriber = SubtitleTranscriber(
        model_size=config.WHISPER_MODEL,
        language=config.WHISPER_LANGUAGE,
        device=config.WHISPER_DEVICE,
        compute_type=config.WHISPER_COMPUTE_TYPE,
    )

    print("\n" + "=" * 60)
    print(f"  第三步: 生成字幕 (Whisper {config.WHISPER_MODEL})")
    print("=" * 60)

    srt_files = transcriber.transcribe_batch(audio_files, config.SUBTITLES_DIR)

    print(f"\n  完成: 共生成 {len(srt_files)} 个字幕文件")
    return srt_files


def main():
    parser = argparse.ArgumentParser(
        description="抖音视频转播客音频 + 字幕"
    )
    parser.add_argument(
        "--step",
        choices=["scrape", "extract", "boost", "transcribe", "playlist", "all"],
        default="all",
        help="执行哪个步骤 (默认 all = 全部执行)"
    )
    parser.add_argument(
        "--max",
        type=int,
        default=None,
        help="最多下载视频数量 (覆盖 config.py 中的 MAX_VIDEOS)"
    )
    args = parser.parse_args()

    # 命令行参数覆盖配置
    if args.max is not None:
        config.MAX_VIDEOS = args.max

    config.ensure_dirs()

    start_time = time.time()

    if args.step in ("scrape", "all"):
        videos = step_scrape()
    else:
        videos = None

    if args.step in ("extract", "all"):
        audio_files = step_extract(videos)
    else:
        audio_files = None

    if args.step == "boost":
        step_boost()

    if args.step in ("transcribe", "all"):
        step_transcribe(audio_files)

    if args.step == "playlist":
        step_playlist()

    elapsed = time.time() - start_time
    print("\n" + "=" * 60)
    print(f"  全部完成! 总耗时: {int(elapsed // 60)}分{int(elapsed % 60)}秒")
    print(f"  输出目录: {config.OUTPUT_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    main()
