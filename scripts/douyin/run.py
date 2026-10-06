"""
抖音 → 音频 + 文章 流水线统一入口

  .venv/bin/python run.py status                      看进度总览
  .venv/bin/python run.py accounts                    列出账号
  .venv/bin/python run.py add "<主页链接>" --name 名字 --style 定位 --max 200
  .venv/bin/python run.py login                       弹 Chrome 扫码，Cookie 自动回写
  .venv/bin/python run.py check [--sec xxx]           登录态/翻页体检
  .venv/bin/python run.py scan --all                  采集全部启用账号（只抓元数据）
  .venv/bin/python run.py columns --all               AI 按标题自动分栏目
  .venv/bin/python run.py process --sec xxx --newest 5   下载→转写→成文
  .venv/bin/python run.py export                      导出前端 json
  .venv/bin/python run.py publish --id <aweme_id>     上传 COS（人工确认后才发）
  .venv/bin/python run.py panel                       打开可视化面板

采集默认走本机 Chrome（config.SCAN_ENGINE），API 引擎用 --engine api 切换。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config


def _conn():
    import pipeline_db as db
    return db.connect()


def cmd_status(args):
    import pipeline_db as db
    conn = _conn()
    print(json.dumps(db.stats(conn), ensure_ascii=False, indent=2))
    for r in db.list_accounts(conn):
        print(f"{r['name']:<12} 已入库 {r['item_count']:<5} 已处理 "
              f"{r['processed_count']:<5} 栏目 {r['column_count']:<3} "
              f"上限 {r['max_items'] or '不限':<5} {r['scan_note'] or ''}")
    conn.close()


def cmd_accounts(args):
    import accounts as accmod
    import pipeline_db as db
    rows = accmod.sync_to_db()
    conn = _conn()
    for r in db.list_accounts(conn):
        print(f"{r['name']:<12} max_items={r['max_items']:<5} 入库={r['item_count']:<5} "
              f"启用={'是' if r['enabled'] else '否'}  {r['sec_user_id']}")
    conn.close()


def cmd_add(args):
    import accounts as accmod
    acc = accmod.add(args.raw, name=args.name, style=args.style,
                     max_items=args.max, enabled=not args.disabled)
    print(json.dumps(acc, ensure_ascii=False, indent=2))
    if args.probe:
        import inventory as inv
        print(json.dumps(inv.probe_account(acc["sec_user_id"]),
                         ensure_ascii=False, indent=2))


def cmd_rm(args):
    import accounts as accmod
    print("已删除" if accmod.remove(args.sec_user_id) else "账号不存在")


def cmd_set(args):
    import accounts as accmod
    fields = {}
    if args.name:
        fields["name"] = args.name
    if args.style:
        fields["style"] = args.style
    if args.max is not None:
        fields["max_items"] = args.max
    if args.enabled is not None:
        fields["enabled"] = args.enabled
    print(json.dumps(accmod.update(args.sec_user_id, **fields),
                     ensure_ascii=False, indent=2))


def cmd_cookie(args):
    import accounts as accmod
    if args.show:
        print(json.dumps(accmod.cookie_preview(), ensure_ascii=False, indent=2))
        return
    raw = sys.stdin.read() if args.raw == "-" else args.raw
    accmod.set_cookie(raw)
    print(f"Cookie 已写入 {config.COOKIE_FILE}")
    if args.sync:
        import browser_scan
        print(json.dumps(browser_scan.call(browser_scan.login, probe_sec="",
                                          timeout_sec=10, fresh=False),
                         ensure_ascii=False))


def cmd_login(args):
    import browser_scan
    res = browser_scan.call(browser_scan.login, headless=False,
                            probe_sec=args.sec or "", timeout_sec=args.wait,
                            fresh=not args.keep_session)
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_check(args):
    import accounts as accmod
    import browser_scan
    sec = args.sec
    if not sec:
        rows = accmod.enabled_accounts()
        if not rows:
            print("还没有启用中的账号")
            return
        sec = rows[0]["sec_user_id"]
    res = browser_scan.call(browser_scan.health_check, sec,
                            headless=True if args.headless else None)
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_reset(args):
    """面板和命令行共用两个 Chrome 配置，残留进程会把后面的浏览器操作全卡住"""
    import browser_scan
    dead = browser_scan.call(browser_scan.kill_profile_chrome, timeout=60)
    print("已关闭残留浏览器：" + (", ".join(map(str, dead)) if dead else "没有需要清理的"))
    print(f"配置目录：{browser_scan.PROFILE_DIR} / {browser_scan.ANON_DIR}")


def cmd_scan(args):
    import accounts as accmod
    import inventory as inv
    if args.all or not args.sec:
        res = inv.scan_all(engine=args.engine,
                           headless=True if args.headless else None)
    else:
        res = [inv.scan_account(args.sec, max_items=args.max, engine=args.engine,
                               headless=True if args.headless else None)]
    for r in res:
        print(f"[{r.get('name', '')}] {r.get('scan_note', '')} 引擎={r.get('engine')}")


def cmd_columns(args):
    import accounts as accmod
    import columns as colmod
    import pipeline_db as db
    conn = _conn()
    targets = [args.sec] if args.sec else [a["sec_user_id"] for a in accmod.enabled_accounts()]
    for sec in targets:
        print(f"\n[{sec[:16]}…] 分栏目")
        res = colmod.build_columns(conn, sec_user_id=sec,
                                  use_ai=not args.no_ai, verbose=not args.quiet)
        print("  " + json.dumps(res, ensure_ascii=False))
    conn.close()


def cmd_process(args):
    import processor
    steps = args.steps.split(",") if args.steps else None
    res = processor.run(steps=steps, sec_user_id=args.sec, column_id=args.column,
                        ids=args.ids or [], limit=args.limit or 0,
                        newest=args.newest or 0, stage=args.stage or None,
                        force=args.force,
                        delay=args.delay, verbose=not args.quiet)
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_publish(args):
    import exporter
    import pipeline_db as db
    conn = _conn()
    ids = list(args.ids or [])
    if args.column:
        ids += [r["aweme_id"] for r in db.list_videos(
            conn, "column_id=? AND stage='article'", (args.column,), limit=2000)]
    if args.sec:
        ids += [r["aweme_id"] for r in db.list_videos(
            conn, "sec_user_id=? AND stage='article'", (args.sec,),
            limit=args.limit or 2000)]
    ids = list(dict.fromkeys(ids))
    if not ids:
        print("没有待发布的条目（stage=article）。先跑 process 生成文章。")
    ok = fail = 0
    for aweme_id in ids:
        try:
            urls = exporter.publish_one(conn, aweme_id, verbose=not args.quiet)
            ok += 1
            print(f"  [ok] {aweme_id} {urls['audio_url'][:80]}")
        except Exception as e:  # noqa: BLE001
            fail += 1
            print(f"  [!] {aweme_id} {type(e).__name__}: {str(e)[:160]}")
    print(f"发布成功 {ok} / 失败 {fail}")
    if ok:
        print(json.dumps(exporter.export_all(conn), ensure_ascii=False))
    conn.close()


def cmd_export(args):
    import exporter
    import pipeline_db as db
    conn = _conn()
    print(json.dumps(exporter.export_all(conn, only_published=not args.all_stages),
                     ensure_ascii=False, indent=2))
    conn.close()


def cmd_panel(args):
    sys.path.insert(0, str(Path(__file__).parent / "panel"))
    import server
    server.main(port=args.port)


def build_parser():
    ap = argparse.ArgumentParser(description="抖音采集 → 音频 + 文章 流水线")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status").set_defaults(func=cmd_status)
    sub.add_parser("accounts").set_defaults(func=cmd_accounts)

    p = sub.add_parser("add", help="添加账号")
    p.add_argument("raw", help="抖音主页链接或 sec_user_id")
    p.add_argument("--name")
    p.add_argument("--style", default="", help="账号定位，例：认知 / 哲学 / 中国传统文化")
    p.add_argument("--max", type=int, default=0, help="抓取上限，0=全部")
    p.add_argument("--disabled", action="store_true", help="先不启用")
    p.add_argument("--probe", action="store_true", help="添加后立刻试探")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("rm", help="删除账号")
    p.add_argument("sec_user_id")
    p.set_defaults(func=cmd_rm)

    p = sub.add_parser("set", help="改账号配置")
    p.add_argument("sec_user_id")
    p.add_argument("--name")
    p.add_argument("--style")
    p.add_argument("--max", type=int, default=None)
    p.add_argument("--enabled", type=lambda x: x not in ("0", "false", "no"), default=None)
    p.set_defaults(func=cmd_set)

    p = sub.add_parser("cookie", help="查看/写入 Cookie")
    p.add_argument("raw", nargs="?", default="", help="整串 Cookie，- 表示从管道读")
    p.add_argument("--show", action="store_true")
    p.add_argument("--sync", action="store_true", help="写入后顺手注入浏览器并回读")
    p.set_defaults(func=cmd_cookie)

    p = sub.add_parser("login", help="弹 Chrome 扫码登录")
    p.add_argument("--sec", default="", help="登录后停留的主页")
    p.add_argument("--wait", type=int, default=900, help="等扫码的秒数")
    p.add_argument("--keep-session", action="store_true", help="不清旧 Cookie")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("check", help="登录态体检")
    p.add_argument("--sec")
    p.add_argument("--headless", action="store_true")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("reset", help="关掉残留的采集浏览器，释放 Chrome 配置目录")
    p.set_defaults(func=cmd_reset)

    p = sub.add_parser("scan", help="采集作品元数据")
    p.add_argument("--sec")
    p.add_argument("--all", action="store_true")
    p.add_argument("--max", type=int, default=None)
    p.add_argument("--engine", choices=["browser", "api", "auto"], default=None)
    p.add_argument("--headless", action="store_true")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("columns", help="按标题自动分栏目")
    p.add_argument("--sec")
    p.add_argument("--no-ai", action="store_true", help="只做规则匹配，不调模型")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_columns)

    p = sub.add_parser("process", help="下载音频 → 转写 → 生成文章")
    p.add_argument("--sec")
    p.add_argument("--column")
    p.add_argument("--ids", nargs="*", default=[])
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--newest", type=int, default=0)
    p.add_argument("--stage", default="", help="只处理某阶段，如 failed")
    p.add_argument("--steps", default="audio,transcript,article")
    p.add_argument("--force", action="store_true")
    p.add_argument("--delay", type=float, default=None)
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_process)

    p = sub.add_parser("publish", help="上传 COS 并导出前端数据")
    p.add_argument("ids", nargs="*", default=[])
    p.add_argument("--sec")
    p.add_argument("--column")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser("export", help="只重建前端 json")
    p.add_argument("--all-stages", action="store_true", help="未发布的也导出（本地预览）")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("panel", help="启动可视化面板")
    p.add_argument("--port", type=int, default=config.PANEL_PORT)
    p.set_defaults(func=cmd_panel)
    return ap


if __name__ == "__main__":
    config.ensure_dirs()
    args = build_parser().parse_args()
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\n已中断")
