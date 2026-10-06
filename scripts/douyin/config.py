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

# 账号配置（多账号 + 每账号抓取数量上限，面板可视化编辑）
ACCOUNTS_FILE = BASE_DIR / "accounts.json"

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

# 元数据文件（记录已处理的视频，支持断点续传）
METADATA_FILE = OUTPUT_DIR / "metadata.json"

# 下载配置
MAX_VIDEOS = 2             # 最多下载多少个视频（0 = 不限，测试时改小）
DOWNLOAD_QUALITY = "720p"  # 视频质量: 360p / 540p / 720p / 1080p

# 音频提取配置
MP3_QUALITY = 2            # ffmpeg -q:a 参数 (0=最高, 9=最低, 2=高质量约190kbps)
DELETE_VIDEO_AFTER_EXTRACT = True  # 提取音频后删除视频文件

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

# ===== COS 配置 =====
COS_BUCKET = "audio-1256405210"
COS_REGION = "ap-shanghai"
COS_BASE_URL = "https://audio-1256405210.cos.ap-shanghai.myqcloud.com/jiugeyinxue"
COS_AUDIO_PREFIX = "jiugeyinxue"
COS_TXT_PREFIX = "jiugeyinxue/txt"

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

# 本地预览：没配 COS 密钥时，导出的音视频/正文地址用面板的 /media 路由顶上，
# 这样 uni-app 起个 H5 服务就能直接试听试读，不必先上云
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
              LEGACY_DIR, DB_DIR]:
        d.mkdir(parents=True, exist_ok=True)
