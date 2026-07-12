"""
配置管理 - 抖音爬虫项目
"""
from pathlib import Path
import os

# 项目根目录
BASE_DIR = Path(__file__).parent

# 抖音用户主页 URL
DOUYIN_USER_URL = "https://www.douyin.com/user/MS4wLjABAAAA7bhEyXsPq05h36FhPyW1JgIu0yHkMCJ2nPnLOlAUhA0UHMxSx3EGv5tmYAYAFoEL"

# 从 sec_user_id 提取（URL 最后一段路径）
SEC_USER_ID = "MS4wLjABAAAA7bhEyXsPq05h36FhPyW1JgIu0yHkMCJ2nPnLOlAUhA0UHMxSx3EGv5tmYAYAFoEL"

# Cookie 文件路径（把 Cookie 粘贴到这个文件里）
COOKIE_FILE = BASE_DIR / "cookie.txt"

# 输出目录
OUTPUT_DIR = BASE_DIR / "output"
VIDEOS_DIR = OUTPUT_DIR / "videos"
AUDIO_DIR = OUTPUT_DIR / "audio"
SUBTITLES_DIR = OUTPUT_DIR / "subtitles"

# 元数据文件（记录已处理的视频，支持断点续传）
METADATA_FILE = OUTPUT_DIR / "metadata.json"

# 下载配置
MAX_VIDEOS = 2             # 最多下载多少个视频（0 = 不限，测试时改小）
DOWNLOAD_QUALITY = "720p"  # 视频质量: 360p / 540p / 720p / 1080p

# 音频提取配置
MP3_QUALITY = 2            # ffmpeg -q:a 参数 (0=最高, 9=最低, 2=高质量约190kbps)
DELETE_VIDEO_AFTER_EXTRACT = True  # 提取音频后删除视频文件

# 字幕配置
WHISPER_MODEL = "medium"   # tiny / base / small / medium / large-v3
WHISPER_LANGUAGE = "zh"     # 中文
WHISPER_DEVICE = "cpu"      # cpu / cuda (Mac 只能用 cpu)
WHISPER_COMPUTE_TYPE = "int8"  # int8 / float16 / float32 (int8 最快最省内存)

# 请求配置
REQUEST_TIMEOUT = 30        # 网络请求超时（秒）
DOWNLOAD_TIMEOUT = 300      # 下载超时（秒）
RETRY_COUNT = 3             # 失败重试次数
RETRY_DELAY = 5            # 重试间隔（秒）


def get_cookie() -> str:
    """从 cookie.txt 读取 Cookie（自动跳过注释行和空行）"""
    if not COOKIE_FILE.exists():
        raise FileNotFoundError(
            f"Cookie 文件不存在: {COOKIE_FILE}\n"
            "请创建该文件并粘贴你的抖音 Cookie。"
        )
    lines = COOKIE_FILE.read_text(encoding="utf-8").strip().splitlines()
    # 过滤掉注释行和空行，拼接剩余内容
    cookie_parts = [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]
    cookie = " ".join(cookie_parts).strip()
    if not cookie:
        raise ValueError(
            f"Cookie 文件为空或只有注释: {COOKIE_FILE}\n"
            "请粘贴你的抖音 Cookie（以 ttwid= 或 passport_csrf_token= 开头的那一整行）。"
        )
    return cookie


def ensure_dirs():
    """确保所有输出目录存在"""
    for d in [OUTPUT_DIR, VIDEOS_DIR, AUDIO_DIR, SUBTITLES_DIR]:
        d.mkdir(parents=True, exist_ok=True)
