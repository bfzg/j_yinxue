"""
配置管理 - 抖音爬虫项目
"""
from pathlib import Path
import os
import urllib.parse

# 项目根目录
BASE_DIR = Path(__file__).parent

# 抖音用户主页 URL
DOUYIN_USER_URL = "https://www.douyin.com/user/MS4wLjABAAAA7bhEyXsPq05h36FhPyW1JgIu0yHkMCJ2nPnLOlAUhA0UHMxSx3EGv5tmYAYAFoEL"

# 从 sec_user_id 提取（URL 最后一段路径）
SEC_USER_ID = "MS4wLjABAAAA7bhEyXsPq05h36FhPyW1JgIu0yHkMCJ2nPnLOlAUhA0UHMxSx3EGv5tmYAYAFoEL"

# Cookie 文件路径（把 Cookie 粘贴到这个文件里）
COOKIE_FILE = BASE_DIR / "cookie.txt"

# 账号配置（多账号 + 每账号抓取数量上限，面板可视化编辑）
ACCOUNTS_FILE = BASE_DIR / "accounts.json"

# 面板运行时开关
SETTINGS_FILE = BASE_DIR / "settings.json"

# 流程唯一真源数据库
DB_DIR = BASE_DIR / "db"
DB_FILE = DB_DIR / "pipeline.sqlite"

# 输出目录
OUTPUT_DIR = BASE_DIR / "output"
VIDEOS_DIR = OUTPUT_DIR / "videos"
AUDIO_DIR = OUTPUT_DIR / "audio"
SUBTITLES_DIR = OUTPUT_DIR / "subtitles"
ARTICLES_DIR = OUTPUT_DIR / "articles"
LEGACY_DIR = OUTPUT_DIR / "legacy"
# 合集封面转存前的本地缓存，一个合集一张，重推时不用回抖音再抓一遍
COVERS_DIR = OUTPUT_DIR / "covers"

# 元数据文件（记录已处理的视频，支持断点续传）
METADATA_FILE = OUTPUT_DIR / "metadata.json"

# 下载配置
MAX_VIDEOS = 2             # 最多下载多少个视频（0 = 不限，测试时改小）
DOWNLOAD_QUALITY = "720p"  # 视频质量: 360p / 540p / 720p / 1080p

# 音频提取配置
MP3_QUALITY = 2            # ffmpeg -q:a 参数 (0=最高, 9=最低, 2=高质量约190kbps)
DELETE_VIDEO_AFTER_EXTRACT = True  # 提取音频后删除视频文件

# ===== 音频落库格式 =====
# 实测基准：原来的双声道 165kbps MP3，每集平均 21MB、每小时 74MB。
# 纯语音内容降到单声道 AAC 40kbps 听不出区别，体积只剩四分之一。
# 改这里（或面板「音频体积」卡下拉）只对新增生效，
# 存量文件用 scripts/douyin/compress.py 原地转，不用重新下载。
def _read_settings() -> dict:
    """settings.json 存面板可改的运行时开关，缺文件按默认值跑"""
    import json

    f = SETTINGS_FILE
    if not f.exists():
        return {}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


AUDIO_PROFILES = {
    "aac40_mono": {
        "label": "AAC 40k 单声道（推荐）",
        "short": "AAC 40k 单声道",
        "ext": "m4a",
        "media_type": "audio/mp4",
        "ffmpeg": ["-ac", "1", "-ar", "32000", "-c:a", "aac", "-b:a", "40k",
                   "-movflags", "+faststart"],
        "kbps": 41,
        "note": "体积 1/4，语音几乎无损，小程序/H5 通吃",
    },
    "aac48_mono": {
        "label": "AAC 48k 单声道 44.1kHz",
        "short": "AAC 48k 单声道",
        "ext": "m4a",
        "media_type": "audio/mp4",
        "ffmpeg": ["-ac", "1", "-ar", "44100", "-c:a", "aac", "-b:a", "48k",
                   "-movflags", "+faststart"],
        "kbps": 50,
        "note": "比 40k 多一点高频余量，体积 2/7",
    },
    "aac32_mono": {
        "label": "AAC 32k 单声道 24kHz（最省）",
        "short": "AAC 32k 单声道",
        "ext": "m4a",
        "media_type": "audio/mp4",
        "ffmpeg": ["-ac", "1", "-ar", "24000", "-c:a", "aac", "-b:a", "32k",
                   "-movflags", "+faststart"],
        "kbps": 33,
        "note": "体积 1/5，气音略闷，长串讲够用",
    },
    "mp3_64_mono": {
        "label": "MP3 64k 单声道（推荐平衡）",
        "short": "MP3 64k 单声道",
        "ext": "mp3",
        "media_type": "audio/mpeg",
        "ffmpeg": ["-ac", "1", "-ar", "44100", "-c:a", "libmp3lame", "-b:a", "64k"],
        "kbps": 64,
        "note": "mp3 兼容格式，体积约 5MB/11分钟，小程序全设备支持",
    },
    "mp3_48_mono": {
        "label": "MP3 48k 单声道（推荐）",
        "short": "MP3 48k 单声道",
        "ext": "mp3",
        "media_type": "audio/mpeg",
        "ffmpeg": ["-ac", "1", "-ar", "32000", "-c:a", "libmp3lame", "-b:a", "48k"],
        "kbps": 48,
        "note": "体积约 4MB/11分钟，接近原 m4a 大小",
    },
    "mp3_32_mono": {
        "label": "MP3 32k 单声道（最省体积）",
        "short": "MP3 32k 单声道",
        "ext": "mp3",
        "media_type": "audio/mpeg",
        "ffmpeg": ["-ac", "1", "-ar", "24000", "-c:a", "libmp3lame", "-b:a", "32k"],
        "kbps": 32,
        "note": "体积约 2.6MB/11分钟，纯语音可接受",
    },
    "mp3_96_mono": {
        "label": "MP3 96k 单声道",
        "short": "MP3 96k 单声道",
        "ext": "mp3",
        "media_type": "audio/mpeg",
        "ffmpeg": ["-ac", "1", "-ar", "44100", "-c:a", "libmp3lame", "-b:a", "96k"],
        "kbps": 96,
        "note": "想保持 .mp3 容器时用这个，体积约 4/7",
    },
    "mp3_stereo": {
        "label": "MP3 -q:a 2 双声道（原状）",
        "short": "MP3 双声道（原状）",
        "ext": "mp3",
        "media_type": "audio/mpeg",
        "ffmpeg": ["-c:a", "libmp3lame", "-q:a", str(MP3_QUALITY)],
        "kbps": 165,
        "note": "改造前的基准，体积最大，只用于回滚",
    },
}
AUDIO_PROFILE_DEFAULT = "mp3_48_mono"
AUDIO_PROFILE = (os.getenv("DY_AUDIO_PROFILE", "")
                 or _read_settings().get("audio_profile", "")
                 or AUDIO_PROFILE_DEFAULT)
if AUDIO_PROFILE not in AUDIO_PROFILES:
    AUDIO_PROFILE = AUDIO_PROFILE_DEFAULT


def audio_profile(name: str = "") -> dict:
    """取当前（或指定）音频档位，附带 name 方便写进日志"""
    key = name or AUDIO_PROFILE
    prof = dict(AUDIO_PROFILES.get(key) or AUDIO_PROFILES[AUDIO_PROFILE_DEFAULT])
    prof["name"] = key if key in AUDIO_PROFILES else AUDIO_PROFILE_DEFAULT
    return prof


# 存量转码时是否保留原来的大文件（面板勾选框控制）
COMPRESS_KEEP_SOURCE = False

# 抓取与下载行为
PAGE_DELAY = 2.5           # 主页翻页间隔（秒），防风控
DOWNLOAD_DELAY = 1.5       # 单集处理间隔（秒）
PREFER_AUDIO_STREAM = True # 优先下载纯音频流（比整条视频小一个数量级）
F2_COOLDOWN_SEC = int(os.getenv("DY_F2_COOLDOWN_SEC", "600"))  # f2 直链连续失败后的冷却时长
# 取直链来源：browser（浏览器拦 detail，最抗风控）/ api（f2 接口）/ auto（先 api 后 browser）
MEDIA_SOURCE = os.getenv("DY_MEDIA_SOURCE", "auto")
QUIET_F2_NOTIFY = os.getenv("DY_QUIET_F2", "1") == "1"   # 屏蔽 f2 的 Bark 推送报错

# ===== 语音转写（ASR）=====
# auto: 先走 paraformer-v2 文件转写（百炼临时存储免密钥，最便宜最快），
#       失败再退回 paraformer-realtime 切片识别
ASR_BACKEND = "auto"
ASR_FILE_MODEL = "paraformer-v2"
ASR_REALTIME_MODEL = "paraformer-realtime-v2"
ASR_SAMPLE_RATE = 16000
ASR_CHUNK_SEC = 120      # realtime 兜底时单次请求的最大音频长度

# 字幕配置
WHISPER_MODEL = "medium"   # tiny / base / small / medium / large-v3
WHISPER_LANGUAGE = "zh"     # 中文
WHISPER_DEVICE = "cpu"      # cpu / cuda (Mac 只能用 cpu)
WHISPER_COMPUTE_TYPE = "int8"  # int8 / float16 / float32 (int8 最快最省内存)

# ===== 百炼平台配置 =====
# Key 不写进代码：优先环境变量 DASHSCOPE_API_KEY，其次同目录 bailian.key
BAILIAN_KEY_FILE = BASE_DIR / "bailian.key"
BAILIAN_API_KEY = (os.getenv("DASHSCOPE_API_KEY", "")
                   or (BAILIAN_KEY_FILE.read_text(encoding="utf-8").strip()
                       if BAILIAN_KEY_FILE.exists() else ""))
BAILIAN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
BAILIAN_MODEL = "qwen-plus"

# 文章生成配置
ARTICLE_ENABLED = True
ARTICLE_MAX_CHARS = 12000

# ===== uniCloud（支付宝云）=====
# 全部媒体与正文落 uniCloud 云存储，数据落云数据库，
# 小程序只读 https://{SPACE}.dev-hz.cloudbasefunction.cn/jy-content，不再手动替换 json。
_S = _read_settings()

UNICLOUD_SPACE_ID = (os.getenv("DY_UNICLOUD_SPACE_ID", "")
                     or _S.get("unicloud_space_id", "")
                     or "env-00jxu1ytdn0v")
# 云函数 URL 化后的入口（空间与别的项目共用，函数和路径都加 jy- 前缀）
UNICLOUD_BASE_URL = (os.getenv("DY_UNICLOUD_BASE_URL", "")
                     or _S.get("unicloud_base_url", "")
                     or f"https://{UNICLOUD_SPACE_ID}.dev-hz.cloudbasefunction.cn").rstrip("/")
UNICLOUD_CONTENT_PATH = "/jy-content"
UNICLOUD_UPLOAD_PATH = "/jy-upload"
# 云存储公共读永久地址前缀：拼上 cloudPath 就是不带签名的永久地址
CLOUD_STORAGE_HOST = (os.getenv("DY_CLOUD_STORAGE_HOST", "")
                      or _S.get("unicloud_storage_host", "")
                      or f"https://{UNICLOUD_SPACE_ID}.normal.cloudstatic.cn").rstrip("/")
# 上传根目录，必须与 function-jy-upload 的 ALLOW_PREFIXES 对齐
CLOUD_PATH_PREFIX = (os.getenv("DY_CLOUD_PREFIX", "")
                     or _S.get("unicloud_prefix", "")
                     or "jiugeyinxue").strip("/")

# 管理令牌不写进代码：优先环境变量 DY_UNICLOUD_TOKEN，其次同目录 unicloud.key
UNICLOUD_KEY_FILE = BASE_DIR / "unicloud.key"


def admin_token() -> str:
    """本地面板唯一需要的手工配置：把令牌贴进 unicloud.key 即可"""
    tok = os.getenv("DY_UNICLOUD_TOKEN", "").strip()
    if not tok and UNICLOUD_KEY_FILE.exists():
        tok = UNICLOUD_KEY_FILE.read_text(encoding="utf-8").strip()
    return tok


def cloud_url(path: str) -> str:
    """云存储文件直链：目录设公共读后不带签名、永不过期，可以直接落库"""
    return "/".join([CLOUD_STORAGE_HOST.rstrip("/"),
                     urllib.parse.quote(str(path).lstrip("/"))])

# 前端静态数据目录（导出 articles / playlist / columns）
FRONTEND_DATA_DIR = BASE_DIR.parent.parent / "src" / "static" / "data"

# ===== 采集引擎 =====
# browser: 本机 Chrome 真实滚动主页拦接口（风控下唯一能翻到全部作品的路，推荐）
# api:     f2 纯接口翻页（Cookie 有效时更快，第二页起常被降级）
# auto:    先 browser，浏览器不可用时退回 api
SCAN_ENGINE = os.getenv("DY_SCAN_ENGINE", "browser")
SCAN_IDLE_LIMIT = 12       # 浏览器连续多少轮无新增算到底
SCAN_DEADLINE_MIN = 40     # 单账号浏览器采集最长分钟数

# 面板服务端口
PANEL_PORT = 8766

# 本地预览：还没推到云存储时，导出的音视频/正文地址用面板的 /media 路由顶上，
# 这样 uni-app 起个 H5 服务就能直接试听试读（这类地址不会写进线上库）
LOCAL_MEDIA_BASE = os.getenv("DY_LOCAL_MEDIA_BASE",
                             f"http://127.0.0.1:{PANEL_PORT}")

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
    for d in [OUTPUT_DIR, VIDEOS_DIR, AUDIO_DIR, SUBTITLES_DIR, ARTICLES_DIR,
              LEGACY_DIR, COVERS_DIR, DB_DIR]:
        d.mkdir(parents=True, exist_ok=True)
