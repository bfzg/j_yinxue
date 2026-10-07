# 九哥隐学

九哥隐学原创文章阅读小程序。

## 数据来源

线上数据全部来自 uniCloud（支付宝云空间 `env-00jxu1ytdn0v`），不再手工维护 json：

- 元数据（标题、摘要、栏目、时长、地址）在云数据库 `jy_episodes` / `jy_columns`，
  小程序通过云函数 `POST /jy-content` 读取。
- 音频 `.m4a` 与正文 `.txt` 在云存储，永久直链前缀
  `https://env-00jxu1ytdn0v.normal.cloudstatic.cn/jiugeyinxue/`。
- 发布动作由本地采集流水线完成，见 [scripts/douyin/README.md](/Volumes/cc/code/j_yinxue/scripts/douyin/README.md) 第十节。
  每次发布 `dataVersion` +1，小程序下次进首页自动拉新数据，**不用重新发版**。

读取顺序在 [src/composables/useSiteData.ts](/Volumes/cc/code/j_yinxue/src/composables/useSiteData.ts)：

```
云数据库 > 本地缓存(jy:*) > 打包的 src/static/data/*.json（兜底种子）
```

`src/static/data/*.json` 只是云端不可达时的兜底，由面板「离线兜底导出」生成，
正常情况下不需要动它。

单条数据字段（`jy_episodes` 返回结构与旧 json 一致）：

- `id`：抖音 `aweme_id`，发布后不要改。
- `title` / `summary` / `category` / `cover`：列表展示字段。
- `articleUrl`：正文 `.txt` 直链，UTF-8，前端用 `uni.request` 现取现解析。
- `audioUrl`：音频 `.m4a` 直链，微信小程序可直接播放。
- `columnId` / `columnName` / `episodeNo` / `episodeTotal`：栏目与集序。
- `sort`：全站排序，取值为负的发布时间戳，升序即最新在前。
- `enabled`：是否展示。

## 运行


```bash
pnpm install
pnpm dev:mp-weixin
```

文章阅读页支持微信背景音频播放，构建产物包含 `requiredBackgroundModes: ['audio']`。
