# 抖音 → 音频 + 文章 采集流水线

把抖音创作者主页的视频，自动变成「一集音频 + 一篇排版好的文章」，并按标题/合集自动归成栏目，
最后导出成前端（uni-app）直接读的 json。

```
账号主页 ──采集元数据──▶ sqlite ──下载音频流──▶ m4a ──语音转写──▶ 全文 ──通义千问──▶ 排版文章
                          │                                                    │
                          └──────────── 按合集/标题正则自动分栏目 ◀────────────
                                              │
                                              └──▶ articles.json / playlist.json / columns.json
```

视频号（微信）相关代码已删除，本目录只服务抖音。TTS 暂未接入。

---

## 一、你需要准备什么

| 项目 | 是否必需 | 放哪 | 说明 |
| --- | --- | --- | --- |
| 抖音登录态 | **必需**（采主页列表时） | 面板点「扫码登录」，或 `run.py login` | Cookie 自动写回 `cookie.txt`。建议用小号，见下方风险提示 |
| 百炼 API Key | **必需** | 同目录 `bailian.key`（已 gitignore），或环境变量 `DASHSCOPE_API_KEY` | 用于 ASR 和文章生成 |
| 腾讯云 COS | 可选 | `config.py` 的 `COS_*` | 不配也能跑，导出 json 里用本地/抖音直链，仅本地预览够用 |
| ffmpeg | 必需 | 见 `config.FFMPEG`（本机在 `/Volumes/cc/dev/ffmpeg/ffmpeg`） | 抽音频、转 mp3。macOS 没有 ffprobe，时长用 `ffmpeg -i` 解析 |

Python 环境固定用本目录的 venv：

```bash
cd /Volumes/cc/code/j_yinxue/scripts/douyin
.venv/bin/python -m pip install -r requirements.txt   # 需要科学上网时加 HTTPS_PROXY
```

### 模型选型（都是国产、按量计费、有免费额度）

| 用途 | 模型 | 单价 | 备注 |
| --- | --- | --- | --- |
| 语音转写 | `paraformer-v2`（百炼录音文件识别） | 0.00008 元/秒 ≈ 0.29 元/小时 | 34 分钟一集约 0.16 元。开通后有免费额度 |
| 兜底转写 | `paraformer-realtime-v2` | 同上量级 | 文件转写失败时自动切片重试，`ASR_BACKEND="auto"` |
| 文章生成 | `qwen-plus` | 输入 0.8 元/百万 token，输出 2 元/百万 token | 一篇长文约 0.01 元。`llm.py` 里有单价表，跑完自动算钱 |

粗算：库里现有 128 集约 31.6 小时，全部跑完 ASR + 文章合计 **不到 15 元**。

---

## 二、可视化面板

```bash
.venv/bin/python run.py panel      # http://127.0.0.1:8766
```

一屏六件事：

1. **登录态 / Cookie** —— 扫码登录、翻页体检、粘贴 Cookie 并热注入 Chrome、「释放浏览器」（清掉占死配置目录的残留进程）。
2. **账号** —— 添加/删除账号、每个账号单独设抓取上限、一键采集全部。
3. **作品队列** —— 筛选、勾选、单条重跑、批量处理，点开看文章正文和播放器。顶栏「全部待处理」把所有还没成文的作品一次排进队列（旁边的小圆标就是待处理条数），跑的过程中可随时点「停止」。
4. **栏目** —— 自动归栏、改名、锁定（锁定的栏目不再被自动规则覆盖）、新建手工栏目。
5. **发布 / 导出** —— 上传 COS、重建前端 json；「日志」页看实时输出，处理进度、耗时、失败原因都在顶栏。
6. **音频体积** —— 选落库档位（AAC 单声道 32k/40k/48k、MP3 96k、原状），看全库预估，一键把存量音频原地重编瘦身，不用重新下载（见第七节）。

面板只监听 `127.0.0.1`，本机自用；`/media/*` 路由放开了跨域，方便前端本地预览（见第六节）。

---

## 三、命令行

```bash
.venv/bin/python run.py status                     # 总览
.venv/bin/python run.py add "https://www.douyin.com/user/MS4wLjAB..." --name 史苑轻谈 --limit 0
.venv/bin/python run.py set <sec_user_id> --limit 200     # 只扒最近 200 集
.venv/bin/python run.py login                      # 弹 Chrome 扫码
.venv/bin/python run.py check                       # 登录态体检
.venv/bin/python run.py scan                        # 采集作品元数据（不下载）
.venv/bin/python run.py columns                     # 按标题/合集自动分栏目
.venv/bin/python run.py process --limit 20          # 下载音频 → 转写 → 生成文章
.venv/bin/python run.py process --stage failed       # 只重跑失败的那些
.venv/bin/python run.py process --column mix:7675...  --steps article --force   # 整栏目重生成文章
.venv/bin/python run.py export                      # 只重建前端 json
.venv/bin/python run.py publish                     # 上传 COS + 导出
.venv/bin/python run.py reset                       # 关掉残留的采集浏览器
```

---

## 四、配置项

`config.py` 里可以直接改，也支持环境变量（前缀 `DY_`）：

| 变量 | 默认 | 作用 |
| --- | --- | --- |
| `SCAN_ENGINE` | `browser` | 主页列表怎么采。`browser`=本机 Chrome 真滚动拦接口（抗风控）；`api`=f2 纯接口翻页；`auto` |
| `MEDIA_SOURCE` | `auto` | 取播放直链。`auto` 先走 f2 接口、失败再用浏览器；`browser` 强制浏览器；`api` 只用接口 |
| `F2_COOLDOWN_SEC` | `600` | f2 直链连续失败两次后，冷却多久内直接走浏览器，省掉每条白烧的十几秒 |
| `max_items`（账号级） | `0` | 单账号抓取上限，0 = 全量。天涯神贴隐学设的 200 |
| `PAGE_DELAY` / `DOWNLOAD_DELAY` | `2.5` / `1.5` | 翻页与单集处理间隔，调小容易触发风控 |
| `SCAN_IDLE_LIMIT` / `SCAN_DEADLINE_MIN` | `12` / `40` | 浏览器采集「连续多少轮无新增算到底」和单账号最长分钟数 |
| `BAILIAN_MODEL` | `qwen-plus` | 文章生成模型 |
| `ASR_BACKEND` | `auto` | `file` 只用 paraformer-v2，`auto` 失败退回 realtime |
| `DY_AUDIO_PROFILE` | `aac40_mono` | 音频档位，取值见第七节档位表（也可写进 `settings.json`）；旧名 `MP3_QUALITY` 只在 `mp3_stereo` 档生效 |
| `DY_LOCAL_MEDIA_BASE` | `http://127.0.0.1:8766` | 本地预览导出时写进 json 的音视频/正文基址 |

---

## 五、栏目是怎么自动归的

`columns.py` 四级规则，优先级从高到低：

1. **抖音合集（mix）** —— 作者在抖音里建过合集的，直接按合集归，集数用合集里的 `episode_index`，最准。史苑轻谈的《王立群读汉武帝》《大风歌》走的就是这条。
2. **标题正则** —— 没有合集时，从标题里抽系列名和集数：
   - `【王立群读汉武帝】第二十集：借刀杀人` → 栏目《王立群读汉武帝》，第 20 集（中文数字也认）
   - `汉武帝刘邦的一生 05`、`Part 3`、`（12）`、`上中下` 这类自编号都能认
   - 同系列编号必须大体不重复，否则判成巧合不开栏，避免乱并
3. **qwen-plus 语义聚类** —— 剩下没有编号的按标题批量丢给模型归主题，凑够 3 条（`--min-ep` 可调）才开栏目，零散的不硬凑。
4. **单篇兜底** —— 还是没归上的进该账号的「· 单篇」桶。

每次重跑会先清空上一轮的自动归栏结果（原生合集 / 面板手工挪动 / 已锁定的保留），否则新采的视频永远接不上旧系列。空壳栏目自动删掉。

面板里改过名或点过「锁定」的栏目，自动规则不会再动它；也可以手工新建栏目再把作品拖进去。

归栏往往晚于文章生成，所以 `build_columns()` 收尾会把已生成正文里的「- 栏目：」那一行回填成最新结果，不重跑模型、不再烧 token。

---

## 六、本地预览（不配 COS 也能听、能读）

没配腾讯云密钥时，「导出（含未发布，本地预览）」/ `run.py export --all-stages`
会把直链写成面板自己的地址：

```
audioUrl   = http://127.0.0.1:8766/media/audio?aweme_id=<id>
articleUrl = http://127.0.0.1:8766/media/article/text?aweme_id=<id>
```

前端起 H5 后点进文章页就能直接试听试读，不用先上云。`/media/article/text`
返回纯文本 Markdown，和线上 `.txt` 的解析路径完全一致；`/media/article`
（返回 JSON）留给面板自己用。要给局域网里的手机看，把 base 换成本机 IP：

```bash
DY_LOCAL_MEDIA_BASE=http://192.168.1.20:8766 .venv/bin/python run.py export --all-stages
```

正式上线仍然要配 `COS_SECRET_ID` / `COS_SECRET_KEY` 再跑 `run.py publish`，
届时同一条目会被 COS 直链覆盖回去（导出按 id upsert）。

> 小程序注意：正文是用 `uni.request` 拉 `.txt` 的，微信只允许白名单域名。
> 本地直链只能在开发者工具里勾掉「不校验合法域名」时用；正式上线必须先
> `publish`，再把 COS 域名加进小程序后台的 request / downloadFile 白名单。

## 七、音频体积：档位与存量瘦身

抖音给的是双声道音频流，照原样落库每集约 21MB，1023 集全量约 20GB。纯语音没必要
双声道，也没必要 160kbps，本地 ffmpeg 重编成单声道 AAC 就只剩四分之一，**不用重新下载**。

全库 276.5 小时在各档位下的体积（面板「音频体积」页能看到同一张表）：

| 档位 | 容器 | 码率 | 全库预估 | 平均每集 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `aac40_mono`（默认） | .m4a | 41kbps | 4.8GB | 4.8MB | 语音几乎无损，小程序/H5/iOS 通吃，推荐 |
| `aac48_mono` | .m4a | 50kbps | 5.8GB | 5.7MB | 多留一点高频余量 |
| `aac32_mono` | .m4a | 33kbps | 3.8GB | 3.8MB | 最省，气音略闷，长篇讲述够用 |
| `mp3_96_mono` | .mp3 | 96kbps | 11.1GB | 10.9MB | 非要 .mp3 容器时用这个 |
| `mp3_stereo` | .mp3 | 146kbps | 19.1GB | 18.7MB | 改造前的原状，只用于回滚 |

为什么不用更小的 opus（同音质还能再省两成）：微信小程序的 `audio` /
`InnerAudioContext` 不支持 opus/ogg，播不出来。`compress.py` 对 `ogg`/`opus` 容器做了
硬性拒绝。采样率最低只到 24kHz —— 实测 16kHz 时 6kHz 以上能量掉 4.6dB，人声明显发闷；
24kHz 只丢 11kHz 以后的空气感尾巴，32kHz 与 44.1kHz 的差异在 0.6dB 以内，听不出。

用法：

```bash
.venv/bin/python compress.py --dry-run              # 先看账，不动文件
.venv/bin/python compress.py --workers 4            # 全量瘦身，约十几分钟
.venv/bin/python compress.py --profile aac32_mono   # 换档位
.venv/bin/python compress.py --column <column_id>   # 只转某个栏目
.venv/bin/python compress.py --keep-source          # 保留原来的大 mp3
```

安全边界：已经是目标档位且码率不超过目标 1.3 倍的直接跳过，重复跑没有副作用；先写
`output/tmp/compact`，校验时长差 <2.5 秒且确实变小才替换原文件并回写 `audio_path`
（只改 `audio_path`，不动 `stage`/`error`/`updated_at`，面板排序不会乱）。已 `published`
的条目默认不动，要一起转加 `--include-published`，转完重发一次让 COS 直链指到新文件。

面板「音频体积」页：下拉切档位（写 `settings.json`，只影响**以后新增**的音频）、
「只算账」（dry run）、「瘦身存量」（`/api/audio/compact`，进度在任务表里看）。
`/media/audio` 和 COS 上传都按真实后缀给 `Content-Type`，`.m4a` 必须回 `audio/mp4`，
否则 iOS / 微信 H5 会拒绝播放。

---

## 八、产物

| 位置 | 内容 |
| --- | --- |
| `db/pipeline.sqlite` | 唯一真源：账号、作品、栏目、文章、事件日志 |
| 每次运行的输出 | 末尾打印本次百炼花费（`llm.py` 的 `USAGE` 只在进程内累计，不落库） |
| `output/audio/<aweme_id>.m4a` | 抽出来并按档位压好的音频，直接给前端播放器用 |
| `output/subtitles/<aweme_id>.json` | 转写全文 + 时间戳 |
| `output/articles/<aweme_id>.md` | 排版好的文章（标题/导语/小标题/本期要点/值得记住的一句/尾部元信息） |
| `src/static/data/*.json` | 前端读的 `articles.json` / `playlist.json` / `columns.json` |

`md` 正文结构固定为：一级标题 → `>` 导语 → 若干 `##` 小节 → 「本期要点」列表 → 「值得记住的一句」引用 → 尾部栏目/时长/原视频链接。前端 [markdown.ts](/Volumes/cc/code/j_yinxue/src/pages/article/markdown.ts) 按这个子集渲染。

---

## 九、已知限制与风险

- **主页作品列表接口现在必须登录**。未登录时 `aweme/post` 会返回 200 + 空 body（不是报错，是静默降级），所以「采全量」这一步一定要扫码。单作品详情接口匿名可用，已入库的作品不受影响。
- **直链几分钟就 403**，所以下载前一定重新取一次，不要复用列表页里的链接。
- **封号风险**：抖音对高频翻页会风控。建议专门用一个不常用的小号跑采集，`PAGE_DELAY` 别往下调，一次别扒太多账号，看到「翻页体检」变红就停手换 Cookie 或隔一段时间再跑。
- 音频走的是抖音自己的音频流，比下载整条视频小一个数量级；`DELETE_VIDEO_AFTER_EXTRACT=True`，视频文件不落盘。
- 长视频（30 分钟以上）转写没问题，实测 34 分钟一集 ASR 约 32 秒。
- 百炼临时存储的上传证书只有 **300 秒**有效期，批量跑到第五分钟会整批 403；`asr.py` 按剩余时间主动换新，失败也会清掉缓存重取，别改回「一个证书用到底」。
- `paraformer-realtime-v2` 兜底时偶发 `websocket close`，已改成每段最多重试 3 次。批量跑完如果还有 failed，`run.py process --stage failed` 重跑一遍就行。
- 图文作品（`kind='image_text'`）不进处理队列，这条流水线只处理视频。

## 十、以后要接自定义音色（TTS）

现在只做「视频 → 音频 + 文章」，音色是抖音原声。要换成你自己的声音，链路已经留好了口子：

1. 模型选百炼的 `cosyvoice-v2`（国产、支持中文），它自带「声音复刻」：
   给一段 5-20 秒的干净干音，平台训一个专属音色，之后按 `voice` 参数直接调，
   长文本按字符计费，量级和 `paraformer` 差不多（10 分钟文稿约几毛钱）。
   不想复刻就用它的预置音色，中文女声/男声十几个可选。
2. 落地只需新增一步 `tts`：在 `processor.STEP_ORDER` 里排在 `article` 之后，
   读 `output/articles/<id>.md` 的正文（去掉尾部元信息和 `>` 引用块），
   合成到 `output/audio/<id>.voice.mp3`，`exporter` 里 `audioUrl` 优先取这个，
   原声留作对照。前端播放器不用改。
3. 面板账号/栏目级别加一个 `voice` 字段即可，栏目可以各用各的音色。

要接的时候说一声，按这个顺序半天能跑通。
