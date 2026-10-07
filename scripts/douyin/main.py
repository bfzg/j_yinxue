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

    # 发布全部待发布条目到 uniCloud
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
    """导出前端兜底 JSON（线上真源已经是 uniCloud，这里只留离线种子）"""
    import exporter
    import pipeline_db as db

    print("\n" + "=" * 60)
    print("  导出 src/static/data/*.json")
    print("=" * 60)
    conn = db.connect()
    try:
        print(json.dumps(exporter.export_all(conn, only_published=False),
                         ensure_ascii=False))
    finally:
        conn.close()


def step_publish():
    """发布：音频 + 正文推云存储，元数据推云数据库，版本号 +1"""
    import cloud_release
    import exporter
    import pipeline_db as db

    print("\n" + "=" * 60)
    print("  发布到 uniCloud")
    print("=" * 60)
    conn = db.connect()
    try:
        def _report(msg: str):
            print(f"  {msg}")
        result = cloud_release.release(conn, reporter=_report)
        print("\n  重新导出兜底 JSON…")
        exporter.export_all(conn)
        print("\n" + json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        conn.close()


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
