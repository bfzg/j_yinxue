"""
浏览器采集引擎 —— 用本机 Chrome 真实滚动抖音主页，拦截作品接口的 JSON

为什么需要它：纯 API 翻页在风控下第二页起只返回 {"status_code": 0}，
合集接口更直接被 ArgusSecurityPlugin 挡掉；Cookie 一过期，浏览器里
作品接口干脆返回空 body。页面自己算 a_bogus/msToken，只要登录态有效
就能一路滚到全部作品，也最不容易触发验证码。

登录态两条路，任选：
  1) .venv/bin/python browser_scan.py --login     弹出 Chrome 扫一次码
  2) 面板里粘贴 Cookie（写进 cookie.txt），首次访问时注入浏览器
两条路成功后都会把新 Cookie 回写 cookie.txt，f2 那条 API 路就能跟着用。

Playwright 同步 API 不能跨线程，所以所有浏览器操作都排进一个单线程
执行器（run_on_browser_thread），顺带让同一个 Chrome 复用。
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).parent))
import config
import pipeline_db as db

PROFILE_DIR = config.BASE_DIR / ".pw-chrome"
# 匿名会话用独立 profile：登录态被风控降级时，匿名反而还能拿到首页数据
ANON_DIR = config.BASE_DIR / ".pw-anon"
POST_URL_KEY = "/aweme/v1/web/aweme/post/"
PROFILE_URL_KEY = "/aweme/v1/web/user/profile/other/"
DETAIL_URL_KEY = "/aweme/v1/web/aweme/detail/"
MIX_URL_KEY = "/aweme/v1/web/mix/aweme/"

IDLE_LIMIT = 12          # 连续多少轮没有新增就认为滚到底
POLL_MS = 1200           # 每轮滚动后的等待
DEADLINE_SEC = 40 * 60   # 单账号最长采集时间
VIEWPORT = {"width": 1440, "height": 900}
LOGIN_COOKIE_KEYS = ("sessionid", "sessionid_ss")

_STOP = threading.Event()
_LOGGED: list[str] = []   # 最近日志，面板直接展示

# detail 接口先试哪个会话，跑一次就知道：匿名能用就绝不动用户的
# sessionid（那是账号资产，少用一次少一分风控风险），匿名拿不到才退回登录态。
# 命中非首选时把顺序纠正过来，后面不再每条空等一轮超时。
_DETAIL_ORDER = ["anon", "login"]


def log(msg: str):
    _LOGGED.append(f"{time.strftime('%H:%M:%S')} {msg}")
    del _LOGGED[:-40]


def recent_log(n: int = 20) -> list[str]:
    return _LOGGED[-n:]


def request_stop():
    """面板点停止：当前这一轮滚完就收工"""
    _STOP.set()


def _clear_stop():
    _STOP.clear()


# ---------- 浏览器会话 ----------

_SCROLL_JS = """
() => {
  const scrollableOf = (n) => {
    while (n && n !== document.documentElement) {
      const st = getComputedStyle(n);
      if (/(auto|scroll)/.test(st.overflowY) && n.scrollHeight > n.clientHeight + 40)
        return n;
      n = n.parentElement;
    }
    return null;
  };
  let box = document.querySelector('.route-scroll-container')
         || document.querySelector('[data-e2e="scroll-list"]');
  if (box && box.scrollHeight <= box.clientHeight + 40) box = null;
  if (!box) {
    const list = document.querySelector('[data-e2e="user-post-list"]');
    box = list ? scrollableOf(list) : null;
  }
  if (!box) {                       // 兜底：挑滚动余量最大的容器
    let best = null, room = 0;
    document.querySelectorAll('div,main,section').forEach((n) => {
      const st = getComputedStyle(n);
      if (!/(auto|scroll)/.test(st.overflowY)) return;
      const r = n.scrollHeight - n.clientHeight;
      if (r > 200 && r > room) { room = r; best = n; }
    });
    box = best;
  }
  if (box) {
    box.scrollTop = box.scrollHeight;
    return {mode: 'container', top: box.scrollTop, height: box.scrollHeight,
            client: box.clientHeight};
  }
  window.scrollTo(0, document.body.scrollHeight);
  return {mode: 'window', top: window.scrollY, height: document.body.scrollHeight};
}
"""


def cookie_items(raw: str) -> list[dict]:
    """把浏览器里复制出来的 Cookie 串转成 Playwright 的 cookie 列表"""
    out = []
    for part in (raw or "").replace("\n", " ").split(";"):
        part = part.strip()
        if not part or "=" not in part or part.startswith("#"):
            continue
        name, value = part.split("=", 1)
        name, value = name.strip(), value.strip()
        if not name or not value:
            continue
        out.append({"name": name, "value": value, "domain": ".douyin.com", "path": "/"})
    return out


def paste_cookie() -> list[dict]:
    try:
        return cookie_items(config.get_cookie())
    except Exception:
        return []


def _in_douyin(domain: str) -> bool:
    d = (domain or "").lower()
    return "douyin.com" in d or "iesdouyin.com" in d


def save_cookies_to_file(ctx) -> int:
    """把浏览器里的抖音 Cookie 回写 cookie.txt，让 f2 那条 API 路跟着受益"""
    try:
        items = [c for c in ctx.cookies() if _in_douyin(c.get("domain", ""))]
    except Exception:
        return 0
    if not items:
        return 0
    header = ("# 由 browser_scan 自动回写，也可自己粘贴（一行 name=value; name=value）\n")
    pairs = "; ".join(f'{c["name"]}={c["value"]}' for c in items)
    config.COOKIE_FILE.write_text(header + pairs + "\n", encoding="utf-8")
    return len(items)


def has_login(ctx) -> bool:
    try:
        ck = {c["name"]: c.get("value") for c in ctx.cookies() if _in_douyin(c.get("domain", ""))}
    except Exception:
        return False
    return any(ck.get(k) for k in LOGIN_COOKIE_KEYS)


def _profile_owner(d: Path) -> Optional[int]:
    """这个 Chrome 配置目录正被哪个进程占着（没有则返回 None）

    面板和命令行共用 .pw-chrome，后启动的那个只会拿到一个立刻自杀的实例，
    报出来是莫名其妙的 TargetClosedError，不如在这里提前翻译成人话。
    SingletonLock 里的 pid 在 macOS 上写成「主机名-进程号」，解析不可靠，
    所以直接拿 ps 里的 --user-data-dir 精确匹配。
    """
    try:
        out = subprocess.run(["ps", "-axo", "pid=,command="],
                             capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return None
    flag = f"--user-data-dir={d.resolve()}"
    for line in out.splitlines():
        pid_s, _, cmd = line.strip().partition(" ")
        if flag not in cmd:
            continue
        try:
            pid = int(pid_s)
        except ValueError:
            continue
        if pid != os.getpid():
            return pid
    return None


def kill_profile_chrome() -> list[int]:
    """关掉残留的采集浏览器，释放两个 Chrome 配置目录，让面板/CLI 能重新接手"""
    killed: list[int] = []
    for d in (PROFILE_DIR, ANON_DIR):
        owner = _profile_owner(d)
        if owner:
            try:
                os.kill(owner, signal.SIGTERM)
                killed.append(owner)
            except OSError:
                pass
    if killed:
        time.sleep(1.5)
        for pid in killed:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    for lock in (PROFILE_DIR / "SingletonLock", ANON_DIR / "SingletonLock"):
        try:
            lock.unlink()
        except OSError:
            pass
    close_session()
    return killed


def _launch(pw, headless: bool, anon: bool = False):
    profile = ANON_DIR if anon else PROFILE_DIR
    kwargs = dict(
        headless=headless,
        args=["--disable-blink-features=AutomationControlled", "--no-first-run",
              "--no-default-browser-check", "--disable-gpu"],
        viewport=VIEWPORT,
        ignore_default_args=["--enable-automation"],
    )
    profile.mkdir(parents=True, exist_ok=True)
    owner = _profile_owner(profile)
    if owner:
        raise RuntimeError(
            f"Chrome 配置 {profile.name} 正被进程 {owner} 占用"
            "（多半是面板 run.py panel 在跑）。请在面板里操作，或者先停面板；"
            "若有残留浏览器杀不掉，跑 browser_scan.py --reset")
    try:
        return pw.chromium.launch_persistent_context(str(profile),
                                                     channel="chrome", **kwargs)
    except Exception:
        return pw.chromium.launch_persistent_context(str(profile), **kwargs)


class _Collector:
    """接 response 事件，把作品 JSON 收进内存"""

    def __init__(self, sec_user_id: str = ""):
        self.sec = sec_user_id
        self.items: dict[str, dict] = {}
        self.order: list[str] = []
        self.details: dict[str, dict] = {}
        self.pages = 0
        self.empty = 0
        self.profile: dict = {}
        self.blocked = False

    def on_response(self, response):
        url = response.url
        try:
            if POST_URL_KEY in url:
                self.pages += 1
                body = response.body()
                if not body:
                    self.empty += 1
                    return
                data = json.loads(body)
                if data.get("status_code") not in (0, None) or "aweme_list" not in data:
                    self.blocked = True
                for aweme in data.get("aweme_list") or []:
                    self._add(aweme)
            elif DETAIL_URL_KEY in url:
                body = response.body()
                if not body:
                    self.empty += 1
                    return
                detail = (json.loads(body).get("aweme_detail") or {})
                if detail.get("aweme_id"):
                    self.details[str(detail["aweme_id"])] = detail
            elif PROFILE_URL_KEY in url:
                body = response.body()
                if not body:
                    return
                user = (json.loads(body).get("user") or {})
                if user:
                    self.profile = {
                        "nickname": user.get("nickname") or "",
                        "aweme_count": int(user.get("aweme_count") or 0),
                        "mix_count": int(user.get("mix_count") or 0),
                        "follower_count": int(user.get("follower_count") or 0),
                    }
        except Exception:
            pass

    def _add(self, aweme):
        import inventory
        rec = inventory.parse_aweme(aweme, self.sec)
        aid = rec.get("aweme_id")
        if not aid or aid in self.items:
            return
        self.items[aid] = rec
        self.order.append(aid)

    @property
    def count(self) -> int:
        return len(self.order)


class BrowserSession:
    """一个可复用的持久 Chrome。绑定创建它的线程，跨线程请走执行器"""

    def __init__(self, headless: bool = False, anon: bool = False):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self.headless = headless
        self.anon = anon
        self.thread = threading.get_ident()
        self.ctx = _launch(self._pw, headless, anon)
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        self.collector: Optional[_Collector] = None
        self.page.on("response", self._on_response)
        if not anon:
            self.inject_cookie()

    def _on_response(self, response):
        if self.collector:
            self.collector.on_response(response)

    def inject_cookie(self, force: bool = False) -> int:
        """把 cookie.txt 注入浏览器。已有登录态时默认不覆盖，避免旧粘贴帮倒忙"""
        if not force and has_login(self.ctx):
            return 0
        items = paste_cookie()
        if not items:
            return 0
        try:
            self.ctx.add_cookies(items)
            return len(items)
        except Exception:
            return 0

    def goto(self, url: str, collector: _Collector, wait_ms: int = 2500):
        self.collector = collector
        self.page.goto(url, wait_until="domcontentloaded", timeout=90_000)
        self.page.wait_for_timeout(wait_ms)
        dismiss(self.page)

    def scroll_once(self) -> Optional[dict]:
        try:
            info = self.page.evaluate(_SCROLL_JS)
            self.page.mouse.wheel(0, 1400)
            self.page.wait_for_timeout(POLL_MS)
            return info
        except Exception:
            return None

    def close(self):
        for fn in (getattr(self.ctx, "close", None), getattr(self._pw, "stop", None)):
            try:
                if fn:
                    fn()
            except Exception:
                pass


_SESSIONS: dict[bool, Optional[BrowserSession]] = {False: None, True: None}
_SESSION_LOCK = threading.RLock()


def get_session(headless: Optional[bool] = None,
                anon: bool = False) -> BrowserSession:
    """取（或第一次创建）可复用的 Chrome。headless=None 表示「别换窗口」

    登录态和匿名态是两套 profile，各自最多一个会话；匿名会话跟着现有窗口的
    有头/无头模式走，免得凭空多弹一个 Chrome。
    """
    with _SESSION_LOCK:
        cur = _SESSIONS.get(bool(anon))
        if cur is not None:
            same_thread = cur.thread == threading.get_ident()
            same_mode = headless is None or cur.headless == bool(headless)
            if same_thread and same_mode:
                return cur
            cur.close()
            _SESSIONS[bool(anon)] = None
        if headless is None:
            other = next((v for v in _SESSIONS.values() if v is not None), None)
            hl = bool(other.headless) if other else False
        else:
            hl = bool(headless)
        _SESSIONS[bool(anon)] = sess = BrowserSession(headless=hl, anon=bool(anon))
        return sess


def close_session(anon: Optional[bool] = None):
    with _SESSION_LOCK:
        keys = [bool(anon)] if anon is not None else list(_SESSIONS)
        for key in keys:
            s = _SESSIONS.get(key)
            if s is not None:
                _SESSIONS[key] = None
                try:
                    s.close()
                except Exception:
                    pass


_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dy-browser")


def run_on_browser_thread(fn: Callable, *args, **kwargs):
    """所有浏览器操作排队进同一个线程，这样 Chrome 只开一次"""
    return _EXECUTOR.submit(fn, *args, **kwargs)


def on_browser_thread() -> bool:
    """调用方能不能直接操作浏览器（没有会话、或者会话就建在本线程时才行）"""
    return not any(s is not None and s.thread != threading.get_ident()
                   for s in _SESSIONS.values())


def call(fn: Callable, *args, timeout: Optional[float] = None, **kwargs):
    """
    跨线程安全地调浏览器函数。

    Playwright 的同步对象绑死创建它的线程，面板后台处理线程要取直链时
    必须把活儿转交给浏览器线程，否则会抛 "Cannot use ... from different thread"。
    """
    if on_browser_thread():
        return fn(*args, **kwargs)
    return run_on_browser_thread(fn, *args, **kwargs).result(timeout=timeout)


def dismiss(page):
    """关掉登录弹窗/公告条，别让它挡住滚动"""
    for sel in ('[data-e2e="login-guide-close"]', '.login-guide .half-circle-left',
                '[aria-label="关闭"]', 'div[class*="login"] svg'):
        try:
            el = page.locator(sel).first
            if el.count() and el.is_visible():
                el.click(timeout=1000)
        except Exception:
            pass
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass


# ---------- 采集 ----------

def _flush(conn, col: _Collector, written: set) -> int:
    new = 0
    for aid in col.order:
        if aid in written:
            continue
        db.upsert_video(conn, col.items[aid])
        written.add(aid)
        new += 1
    if new:
        conn.commit()
    return new


def _scan_attempt(conn, sec_user_id: str, max_items: int = 0,
                  headless: Optional[bool] = None,
                  on_progress: Optional[Callable[[int, int, int], None]] = None,
                  verbose: bool = True, idle_limit: int = IDLE_LIMIT,
                  deadline_sec: int = DEADLINE_SEC, anon: bool = False) -> dict:
    """滚一遍主页收作品接口。登录态/匿名由调用方决定，这里只管滚"""
    col = _Collector(sec_user_id)
    before = {r["aweme_id"] for r in conn.execute(
        "SELECT aweme_id FROM videos WHERE sec_user_id=?", (sec_user_id,)).fetchall()}
    written = set(before)
    started = time.time()
    reason = ""
    sess = get_session(headless, anon=anon)
    logged = has_login(sess.ctx)

    try:
        sess.goto(f"https://www.douyin.com/user/{sec_user_id}", col)
        if not col.count and (col.empty or not logged):
            reason = "not_logged_in"
            log("作品接口返回空：请先扫码登录或粘贴新 Cookie")

        idle = 0
        last = col.count
        while not reason:
            target = max_items if max_items > 0 else 0
            if target and col.count >= target:
                break
            if _STOP.is_set():
                reason = "stopped"
                break
            if time.time() - started > deadline_sec:
                reason = "timeout"
                break
            info = sess.scroll_once()
            if info is None:
                reason = "scroll_error"
                break
            _flush(conn, col, written)
            if on_progress:
                on_progress(col.count, target, col.count - len(before))
            elif verbose:
                print(f"  … 已收到 {col.count} 条（第 {col.pages} 页）", flush=True)
            if col.count == last:
                idle += 1
                if verbose:
                    print(f"    暂无新增 {idle}/{idle_limit} {info}", flush=True)
                if idle >= idle_limit:
                    break
                if idle in (3, 6, 9):
                    try:
                        sess.page.mouse.move(720, 500)
                        sess.page.mouse.wheel(0, 3000)
                        sess.page.wait_for_timeout(POLL_MS)
                    except Exception:
                        pass
            else:
                idle, last = 0, col.count
    finally:
        _flush(conn, col, written)
        sess.collector = None

    expect = col.profile.get("aweme_count") or 0
    cap = min(expect, max_items) if (max_items and expect) else (max_items or expect)
    truncated = bool(cap and col.count < cap)
    if truncated and not reason:
        reason = "stopped_early"
    result = {
        "scanned": col.count,
        "new": col.count - len(before),
        "total_new": col.count - len(before),
        "truncated": truncated,
        "reason": reason,
        "pages": col.pages,
        "empty": col.empty,
        "blocked": col.blocked,
        "logged_in": logged,
        "anon": anon,
        "nickname": col.profile.get("nickname", ""),
        "aweme_count": expect,
        "mix_count": col.profile.get("mix_count", 0),
        "follower_count": col.profile.get("follower_count", 0),
        "mixes": {},
        "seconds": int(time.time() - started),
    }
    for rec in col.items.values():
        if rec.get("mix_name"):
            result["mixes"][rec["mix_name"]] = result["mixes"].get(rec["mix_name"], 0) + 1
    if col.profile and not anon:
        conn.execute(
            """UPDATE accounts SET nickname=?, aweme_count=?, mix_count=?,
                     follower_count=?, last_cursor=? WHERE sec_user_id=?""",
            (col.profile.get("nickname"), expect, col.profile.get("mix_count"),
             col.profile.get("follower_count"), int(time.time()), sec_user_id),
        )
        conn.commit()
    log(f"[scan] {result['nickname'] or sec_user_id[:12]} 收到 {col.count} 条 "
        f"/ 主页 {expect} 条，翻页 {col.pages}，{result['seconds']}s"
        + ("，匿名" if anon else "") + (f"，{reason}" if reason else ""))
    return result


def scan(sec_user_id: str, max_items: int = 0, headless: Optional[bool] = None,
         on_progress: Optional[Callable[[int, int, int], None]] = None,
         verbose: bool = True, idle_limit: int = IDLE_LIMIT,
         deadline_sec: int = DEADLINE_SEC, anon: bool = False) -> dict:
    """
    滚动采集单账号主页作品。返回：
      {scanned, new, total_new, truncated, reason, pages, nickname, aweme_count,
       mix_count, follower_count, mixes, seconds, logged_in, anon}

    带 sessionid 却一条作品都翻不出来，说明这个登录态已经被风控降级，
    此时匿名比它强（至少还有首页），自动退一次，别让整个采集空手而归。
    """
    config.ensure_dirs()
    _clear_stop()
    conn = db.connect()
    try:
        args = (sec_user_id, max_items, headless, on_progress, verbose,
                idle_limit, deadline_sec)
        res = _scan_attempt(conn, *args, anon=anon)
        if not anon and res["logged_in"] and not res["scanned"]:
            log("登录态被风控降级，改用匿名会话重试（匿名一般只够翻到首页）")
            res = _scan_attempt(conn, *args, anon=True)
            if res["scanned"]:
                res["reason"] = "degraded_login"
        return res
    finally:
        conn.close()


def scan_many(items: list[dict], headless: Optional[bool] = None,
              on_account: Optional[Callable[[dict, dict], None]] = None) -> list[dict]:
    """一次开浏览器，连扫多个账号。items: [{sec_user_id, max_items, name}]"""
    out = []
    for it in items:
        res = scan(it["sec_user_id"], max_items=it.get("max_items") or 0,
                   headless=headless)
        res["name"] = it.get("name", "")
        res["sec_user_id"] = it["sec_user_id"]
        if on_account:
            on_account(it, res)
        out.append(res)
        if _STOP.is_set():
            break
    return out


# ---------- 直链兜底：浏览器取作品详情 ----------

def _detail_once(aweme_id: str, headless: Optional[bool], timeout: int,
                 anon: bool) -> Optional[dict]:
    sess = get_session(headless, anon=anon)
    col = _Collector("")
    sess.goto(f"https://www.douyin.com/video/{aweme_id}", col, wait_ms=1500)
    deadline = time.time() + timeout
    detail = None
    try:
        while time.time() < deadline:
            detail = col.details.get(str(aweme_id))
            if detail:
                break
            sess.page.wait_for_timeout(500)
    finally:
        sess.collector = None
    return detail


def resolve_detail(aweme_id: str, headless: Optional[bool] = None,
                   timeout: int = 30) -> dict:
    """API 取不到直链时，用浏览器打开作品页，拦 detail 接口

    被风控降级的 sessionid 会让接口直接返回空，而且 sessionid 本身是账号
    资产，能省就用，所以默认匿名优先、登录态兜底。
    """
    for i, mode in enumerate(list(_DETAIL_ORDER)):
        detail = _detail_once(aweme_id, headless, timeout, anon=(mode == "anon"))
        if detail:
            if i:
                _DETAIL_ORDER.remove(mode)
                _DETAIL_ORDER.insert(0, mode)
                log(f"取直链改用{mode}会话，后续沿用")
            return detail
        log(f"{mode}会话没拿到 {aweme_id} 的详情接口，换另一个再试")
    raise RuntimeError(f"浏览器没拿到 {aweme_id} 的详情接口（登录态和匿名都失败）")


# ---------- 登录 / 体检 ----------

def login(headless: bool = False, timeout_sec: int = 900,
          probe_sec: str = "", fresh: bool = True) -> dict:
    """
    弹出 Chrome 让用户扫码，成功后把 Cookie 回写 cookie.txt

    fresh=True：先清掉浏览器里所有抖音 Cookie。旧 sessionid 只要还留在
    profile 里，页面就仍然显示"已登录"，二维码根本不会出现，扫码按钮也就
    永远救不回来，所以默认清一次再让你扫。
    """
    sess = get_session(False if not headless else True)
    url = (f"https://www.douyin.com/user/{probe_sec}" if probe_sec
           else "https://www.douyin.com/?recommend=1")
    cleared = 0
    if fresh and has_login(sess.ctx):
        try:
            sess.ctx.clear_cookies()
            cleared = 1
        except Exception:
            pass
    sess.collector = None
    try:
        sess.page.goto(url, wait_until="domcontentloaded", timeout=90_000)
    except Exception:
        pass
    sess.page.wait_for_timeout(2500)
    # 触发登录框：抖音未登录时主页右上角有"登录"按钮
    for sel in ('[data-e2e="login-button"]', 'button:has-text("登录")'):
        try:
            el = sess.page.locator(sel).first
            if el.count() and el.is_visible():
                el.click(timeout=2000)
                break
        except Exception:
            pass
    log("已打开 Chrome，请用手机抖音扫码登录（窗口先别关）")
    deadline = time.time() + timeout_sec
    n_saved = 0
    ok = False
    while time.time() < deadline:
        try:
            sess.page.wait_for_timeout(2000)
        except Exception:
            log("浏览器窗口被关闭，登录中止")
            break
        if has_login(sess.ctx):
            ok = True
            n_saved = save_cookies_to_file(sess.ctx)
            break
    log(f"登录{'成功' if ok else '未完成'}，回写 cookie.txt {n_saved} 项")
    return {"ok": ok, "cookies_saved": n_saved, "cleared": cleared,
            "message": "登录成功，Cookie 已同步到 cookie.txt" if ok
            else "没检测到登录态：请确认窗口里已扫码并点了确认"}


def probe(sec_user_id: str, headless: Optional[bool] = None,
          rounds: int = 3, timeout: int = 40) -> dict:
    """添加账号前先看一眼：昵称、作品总数、有哪些合集、前几条长什么样"""
    config.ensure_dirs()
    sess = get_session(headless)
    col = _Collector(sec_user_id)
    sess.goto(f"https://www.douyin.com/user/{sec_user_id}", col, wait_ms=3500)
    deadline = time.time() + timeout
    while time.time() < deadline and (col.count < 20 or rounds > 0):
        sess.scroll_once()
        rounds -= 1
        if rounds <= 0 and col.count >= 20:
            break
    sess.collector = None
    mixes: dict[str, int] = {}
    samples = []
    for aid in col.order:
        rec = col.items[aid]
        if rec.get("mix_name"):
            mixes[rec["mix_name"]] = mixes.get(rec["mix_name"], 0) + 1
        if len(samples) < 15:
            samples.append({"aweme_id": rec["aweme_id"], "title": rec["title"],
                            "ep_no": rec.get("ep_no"), "mix_name": rec.get("mix_name"),
                            "seconds": round((rec.get("duration_ms") or 0) / 1000),
                            "kind": rec.get("kind", "video")})
    expect = col.profile.get("aweme_count") or 0
    return {
        "ok": bool(col.count),
        "engine": "browser",
        "nickname": col.profile.get("nickname", ""),
        "aweme_count": expect,
        "mix_count": col.profile.get("mix_count", 0),
        "follower_count": col.profile.get("follower_count", 0),
        "mixes": [{"name": k, "n": v} for k, v in
                  sorted(mixes.items(), key=lambda kv: -kv[1])],
        "samples": samples, "seen": col.count,
        "logged_in": has_login(sess.ctx),
        "message": ("正常" if col.count else
                    ("浏览器里没有登录态，请先扫码登录" if not has_login(sess.ctx)
                     else "接口没返回作品，多半 Cookie 过期")),
    }


def health_check(sec_user_id: str, headless: Optional[bool] = None) -> dict:
    """采集前轻量体检：只看能不能翻页（滚 4 轮），不入库"""
    config.ensure_dirs()
    sess = get_session(headless)
    col = _Collector(sec_user_id)
    sess.goto(f"https://www.douyin.com/user/{sec_user_id}", col, wait_ms=3000)
    for _ in range(4):
        sess.scroll_once()
    sess.collector = None
    logged = has_login(sess.ctx)
    paged = col.pages >= 2 and col.count > 24
    # 已登录却一条都翻不出来：再拿匿名会话探一次，好区分「Cookie 过期」
    # 和「这个 sessionid 被降级，但匿名还能凑合用首页」
    anon_items = 0
    if logged and not col.count:
        anon_items = _anon_count(sec_user_id, headless)

    if not logged:
        msg = "浏览器里没有登录态：请粘贴 Cookie 或点扫码登录"
    elif col.empty and not col.count:
        if anon_items:
            msg = (f"登录态已被风控降级（接口返回空）。匿名模式还能拿到首页 "
                   f"{anon_items} 条，但要抓完全量必须重新扫码")
        else:
            msg = "登录态已失效，匿名也拿不到作品：换网络或换个账号重新扫码"
    elif not paged:
        msg = f"只拿到第一页（{col.count} 条）：登录态可能被降级，建议重新扫码"
    else:
        msg = f"翻页正常：{col.pages} 次请求 / {col.count} 条"
    return {"ok": paged, "logged_in": logged, "pages": col.pages,
            "items": col.count, "empty": col.empty, "anon_items": anon_items,
            "nickname": col.profile.get("nickname", ""),
            "aweme_count": col.profile.get("aweme_count", 0),
            "message": msg}


def _anon_count(sec_user_id: str, headless: Optional[bool],
                timeout: int = 25) -> int:
    """匿名会话能收到多少条作品（不入库，只体检用）"""
    try:
        sess = get_session(headless, anon=True)
    except Exception as e:  # noqa: BLE001
        log(f"匿名会话起不来: {type(e).__name__}")
        return 0
    col = _Collector(sec_user_id)
    try:
        sess.goto(f"https://www.douyin.com/user/{sec_user_id}", col, wait_ms=3000)
        sess.scroll_once()
    except Exception:
        pass
    finally:
        sess.collector = None
    return col.count


def login_state() -> dict:
    """不访问抖音，只看本地持久化目录里有没有登录 Cookie"""
    p = PROFILE_DIR / "Default" / "Cookies"
    return {"profile": str(PROFILE_DIR), "profile_exists": p.exists(),
            "cookie_file": str(config.COOKIE_FILE),
            "cookie_exists": config.COOKIE_FILE.exists()}


# ---------- CLI ----------

def _cli():
    import argparse

    ap = argparse.ArgumentParser(description="抖音主页浏览器采集")
    ap.add_argument("--sec", help="sec_user_id 或主页链接")
    ap.add_argument("--all", action="store_true", help="采集 accounts.json 里所有启用的账号")
    ap.add_argument("--max", type=int, default=None, help="本次上限（默认取账号配置）")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--login", action="store_true", help="打开窗口扫码登录一次")
    ap.add_argument("--check", action="store_true", help="只做翻页体检")
    ap.add_argument("--sync-cookie", action="store_true", help="把浏览器 Cookie 写回 cookie.txt")
    ap.add_argument("--keep-session", action="store_true",
                    help="扫码登录时不清旧 Cookie（仅在登录态本来就正常时用）")
    ap.add_argument("--reset", action="store_true",
                    help="关掉残留的采集浏览器并释放 Chrome 配置目录")
    args = ap.parse_args()

    import accounts as accmod

    hl = True if args.headless else None   # None = 沿用现有窗口，不来回重开

    if args.reset:
        dead = kill_profile_chrome()
        print("已关闭残留浏览器进程：" + (", ".join(map(str, dead)) if dead else "没有需要清理的"))
        return

    if args.login:
        print(json.dumps(login(probe_sec=args.sec or "",
                              fresh=not args.keep_session),
                         ensure_ascii=False, indent=2))
        return

    if args.sync_cookie:
        sess = get_session(hl)
        print(f"已写入 {save_cookies_to_file(sess.ctx)} 项 Cookie -> {config.COOKIE_FILE}")
        return

    if args.all:
        conn = db.connect()
        accmod.sync_to_db(conn)
        rows = [dict(r) for r in db.list_accounts(conn) if r.get("enabled", 1)]
        conn.close()
        plan = [{"sec_user_id": a["sec_user_id"], "name": a["name"],
                 "max_items": args.max if args.max is not None else (a.get("max_items") or 0)}
                for a in rows]
        for res in scan_many(plan, headless=hl):
            print(f"[{res['name']}] 收到 {res['scanned']} / 主页 {res['aweme_count']}，"
                  f"新增 {res['new']}，翻页 {res['pages']}，{res['seconds']}s"
                  + (f"，提前结束：{res['reason']}" if res["truncated"] else ""))
        return

    raw = args.sec
    if not raw:
        ap.error("需要 --sec 或 --all")
    sec = accmod.parse_sec_user_id(raw) or raw
    if args.check:
        print(json.dumps(health_check(sec, hl), ensure_ascii=False, indent=2))
        return
    res = scan(sec, max_items=args.max or 0, headless=hl)
    print(json.dumps({k: v for k, v in res.items() if k != "mixes"},
                     ensure_ascii=False, indent=2))
    print("合集：", json.dumps(res["mixes"], ensure_ascii=False))


if __name__ == "__main__":
    _cli()
