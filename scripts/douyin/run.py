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
  .venv/bin/python run.py cloud check                 上传云函数前自检（语法/令牌/版本差）
  .venv/bin/python run.py articles --dry-run          查存量文章的旧尾注（栏目/时长/原视频）
  .venv/bin/python run.py cloud test                  两个云函数探活 + 线上读链路体检
  .venv/bin/python run.py cloud health                只跑线上读链路体检（失败退出码 1）
  .venv/bin/python run.py cloud init                   建集合 + 写 meta
  .venv/bin/python run.py cloud status                 线上 vs 本地条数
  .venv/bin/python run.py cloud covers                 合集封面转存到云存储
  .venv/bin/python run.py cloud publish --all          音频+正文传云存储并推元数据
  .venv/bin/python run.py cloud texts --all            只重推已上线正文（改了排版用它，音频不碰）
  .venv/bin/python run.py cloud sync                   只同步元数据（不重传文件）
  .venv/bin/python run.py cloud offline <id>...        云端下架
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


def cmd_articles(args):
    """抹掉存量文章末尾的旧尾注；文件一改 mtime 变新，下次发布会自动重推正文"""
    import article_formatter as af

    conn = _conn()
    try:
        out = af.strip_footers(conn, apply_changes=not args.dry_run, verbose=False)
    finally:
        conn.close()
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_publish(args):
    """兼容旧命令名：等价于 cloud publish"""
    args.all = True
    cmd_cloud(args)


def cmd_cloud(args):
    """uniCloud 云端发布：传文件 → 写云数据库 → dataVersion +1"""
    import cloud_client as cc
    import cloud_release
    import pipeline_db as db

    try:
        _cmd_cloud(args, cc, cloud_release, db)
    except cc.CloudError as e:
        print(f"云端调用失败：{e}")
        if e.code == 404:
            print("  → 云函数还没部署或没开 URL 化。先在 HBuilderX 上传 "
                  "function-jy-content / function-jy-upload，")
            print("    再到支付宝云控制台把两个函数的访问路径绑成 "
                  "/jy-content 和 /jy-upload（允许 POST）。")
            print("    清单见 scripts/douyin/README.md 第十节。")
        elif e.code == 401:
            print("  → 令牌不匹配：检查 scripts/douyin/unicloud.key 与两个函数里 "
                  "secret.js 的 ADMIN_TOKEN 是否一致。")
        raise SystemExit(1)


def _cmd_cloud(args, cc, cloud_release, db):
    act = args.action
    if act == "test":
        print(json.dumps(cc.ping(), ensure_ascii=False, indent=2))
        return
    if act == "health":
        res = cc.health()
        print(json.dumps(res, ensure_ascii=False, indent=2))
        if not res["ok"]:
            raise SystemExit(1)
        return
    if act == "init":
        print(json.dumps(cloud_release.init_site(None), ensure_ascii=False, indent=2))
        return

    if act == "check":
        import cloud_check
        print(json.dumps(cloud_check.check_all(online=True, reporter=_echo(args.quiet)),
                         ensure_ascii=False, indent=2))
        return

    conn = _conn()
    try:
        if act == "status":
            print(json.dumps(cloud_release.compare(conn), ensure_ascii=False, indent=2))
        elif act == "covers":
            import covers
            res = covers.refresh(conn, column_ids=[args.column] if args.column else None,
                                 force=args.force, dry_run=args.dry_run,
                                 reporter=_echo(args.quiet))
            print(json.dumps(res, ensure_ascii=False, indent=2))
        elif act == "sync":
            meta = cloud_release.push_meta(conn, reporter=_echo(args.quiet))
            rel = cc.call_content("pushRelease", note=args.note or "CLI 只同步元数据",
                                  episodes=meta["episodes"]["total"])
            print(json.dumps({"meta": meta, "release": rel}, ensure_ascii=False, indent=2))
        elif act == "publish":
            ids = list(args.ids or [])
            if not ids and not args.sec and not args.column and not getattr(args, "all", False):
                print("没给范围。用 --sec / --column / 位置参数 ids，或加 --all 发全部待发布。")
                return
            res = cloud_release.release(
                conn, ids=ids or None, column_id=args.column or "",
                sec_user_id=args.sec or "", limit=args.limit or 0, force=args.force,
                note=args.note or "CLI 发布", dry_run=args.dry_run, reporter=_echo(args.quiet))
            print(json.dumps(res, ensure_ascii=False, indent=2))
        elif act == "texts":
            ids = list(args.ids or [])
            if not ids and not args.sec and not args.column and not getattr(args, "all", False):
                print("没给范围。用 --sec / --column / 位置参数 ids，或加 --all 刷全部已上线正文。")
                return
            res = cloud_release.release_texts(
                conn, ids=ids or None, column_id=args.column or "",
                sec_user_id=args.sec or "", limit=args.limit or 0,
                note=args.note or "CLI 只刷正文", dry_run=args.dry_run,
                reporter=_echo(args.quiet))
            print(json.dumps(res, ensure_ascii=False, indent=2))
        elif act == "offline":
            ids = list(args.ids or [])
            if not ids:
                print("offline 需要给 aweme_id")
                return
            print(json.dumps(cloud_release.offline(conn, ids, purge_files=args.purge),
                             ensure_ascii=False, indent=2))
        elif act == "retract":
            ids = list(args.ids or [])
            if not ids:
                print("retract 需要给 aweme_id")
                return
            print(json.dumps(cloud_release.retract(conn, ids), ensure_ascii=False, indent=2))
        else:
            print(f"未知动作：{act}")
    finally:
        conn.close()


def _echo(quiet: bool):
    return (lambda m: None) if quiet else (lambda m: print(f"  {m}"))


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

    p = sub.add_parser("articles", help="整理存量文章：去掉旧的栏目/时长/原视频尾注")
    p.add_argument("--dry-run", action="store_true", help="只统计，不写盘")
    p.set_defaults(func=cmd_articles)

    p = sub.add_parser("publish", help="发布到 uniCloud（= cloud publish --all）")
    p.add_argument("ids", nargs="*", default=[])
    p.add_argument("--sec")
    p.add_argument("--column")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--force", action="store_true", help="已推过的也重传")
    p.add_argument("--dry-run", action="store_true", help="只列清单不上传")
    p.add_argument("--note", default="", help="版本备注")
    p.add_argument("--all", action="store_true", help="全部待发布")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser("cloud", help="uniCloud 云端发布与运维")
    p.add_argument("action", choices=["test", "health", "init", "status", "publish",
                                      "sync", "covers", "offline", "retract", "check",
                                      "texts"])
    p.add_argument("ids", nargs="*", default=[])
    p.add_argument("--sec")
    p.add_argument("--column")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--force", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--purge", action="store_true", help="offline 时连云存储文件一起删")
    p.add_argument("--note", default="")
    p.add_argument("--all", action="store_true", help="publish 时不限范围")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_cloud)

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
