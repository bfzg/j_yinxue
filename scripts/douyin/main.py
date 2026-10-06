"""
抖音视频 → 播客音频 → 字幕 → 文章 → 发布
全自动流程入口

用法:
    # 使用 venv 中的 python
    .venv/bin/python main.py

    # 完整流程（爬取→提取→转写→文章→发布）
    python main.py --step all

    # 只生成文章（从已有 SRT）
    python main.py --step article

    # 只发布最新文章
    python main.py --step publish

    # 管理面板
    python main.py --step panel
"""
import argparse
import json
import subprocess
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

    cos_base = config.COS_BASE_URL

    playlist_path = Path(__file__).parent.parent.parent / "src" / "static" / "data" / "playlist.json"
    if playlist_path.exists():
        existing = json.loads(playlist_path.read_text(encoding="utf-8"))
    else:
        existing = {"app": {}, "settings": {}, "items": []}

    items = []
    for i, mp3_path in enumerate(audio_files, 1):
        name = mp3_path.stem
        date_str = ""
        title = name
        if name[:4].isdigit() and len(name) > 19:
            date_str = name[:10]
            title_part = name[19:]
            title_part = title_part.replace("_video", "")
            if "_#" in title_part:
                title = title_part.split("_#")[0].strip()
            elif "#" in title_part:
                title = title_part.split("#")[0].strip()
            else:
                title = title_part.strip()
            title = title.strip("_").strip()

        url_filename = urllib.parse.quote(name + ".mp3")
        audio_url = cos_base + "/" + url_filename
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

    existing["items"] = items
    playlist_path.parent.mkdir(parents=True, exist_ok=True)
    playlist_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  已生成: {playlist_path}")
    print(f"  共 {len(items)} 条音频")


def _get_duration(mp3_path: Path) -> int:
    """获取音频时长（秒）"""
    import re

    ffmpeg_bin = "ffmpeg"
    result = subprocess.run(["which", "ffmpeg"], capture_output=True, text=True)
    if result.returncode != 0 or not result.stdout.strip():
        ffmpeg_bin = "/Volumes/cc/dev/ffmpeg/ffmpeg"

    try:
        result = subprocess.run([ffmpeg_bin, "-i", str(mp3_path)], capture_output=True, text=True, timeout=30)
        match = re.search(r"Duration:\s+(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
        if match:
            h, m, s = match.groups()
            return int(int(h) * 3600 + int(m) * 60 + float(s))
    except Exception:
        pass
    return 0


def step_transcribe(audio_files: list = None) -> list:
    """第三步: 生成字幕"""
    from transcriber import SubtitleTranscriber

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


def step_article():
    """第四步: 从 SRT 字幕生成文章"""
    from article_writer import batch_generate, ArticleWriter

    print("\n" + "=" * 60)
    print("  第四步: 生成文章 (Whisper → 百炼 qwen-turbo)")
    print("=" * 60)

    srt_files = sorted(config.SUBTITLES_DIR.glob("*.srt"))
    if not srt_files:
        print("  [提示] 没有找到字幕文件，请先运行 --step transcribe")
        return

    # 过滤：跳过已生成文章的 SRT
    pending = []
    for srt in srt_files:
        article_json = config.ARTICLES_DIR / (srt.stem + ".article.json")
        if not article_json.exists():
            pending.append(srt)

    if not pending:
        print("  [提示] 所有 SRT 都已生成文章，无需处理")
        return

    print(f"  待处理: {len(pending)} 个")
    articles = batch_generate(pending, config.ARTICLES_DIR)

    print(f"\n  完成: 共生成 {len(articles)} 篇文章")


def step_publish():
    """第五步: 发布文章到 COS + 更新前端 JSON"""
    from uploader import publish

    print("\n" + "=" * 60)
    print("  第五步: 发布到 COS + 更新 JSON")
    print("=" * 60)

    article_files = sorted(config.ARTICLES_DIR.glob("*.article.json"))
    if not article_files:
        print("  [提示] 没有找到文章元数据文件，请先运行 --step article")
        return

    success_count = 0
    for article_file in article_files:
        # 查找对应的 MP3
        # article.json 文件名: "2026-07-11 21-43-00_xxx_video.article.json"
        # MP3 文件名:      "2026-07-11 21-43-00_xxx_video.mp3"
        mp3_name = article_file.stem.replace(".article", "") + ".mp3"
        mp3_path = config.AUDIO_DIR / mp3_name

        if not mp3_path.exists():
            print(f"\n  [跳过] 找不到对应 MP3: {mp3_name}")
            continue

        print(f"\n  [{success_count + 1}/{len(article_files)}] 发布: {article_file.name}")
        if publish(str(article_file), str(mp3_path)):
            success_count += 1

    print(f"\n  完成: 成功发布 {success_count} 篇")


def step_panel():
    """启动管理面板"""
    from panel.server import main
    main()


def main():
    parser = argparse.ArgumentParser(
        description="抖音视频 → 播客 → 文章 全自动 Pipeline"
    )
    parser.add_argument(
        "--step",
        choices=["scrape", "extract", "boost", "transcribe", "article", "publish", "playlist", "panel", "all"],
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

    if args.step == "panel":
        step_panel()
        return

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

    if args.step in ("article", "all"):
        step_article()

    if args.step == "playlist":
        step_playlist()

    if args.step in ("publish", "all"):
        step_publish()

    elapsed = time.time() - start_time
    print("\n" + "=" * 60)
    print(f"  全部完成! 总耗时: {int(elapsed // 60)}分{int(elapsed % 60)}秒")
    print("=" * 60)


if __name__ == "__main__":
    main()
