# 九哥隐学

九哥隐学原创播客小程序。

## 数据维护

播放列表数据在 `src/static/data/playlist.json`。

每一期音频使用下面字段：

- `id`：稳定 ID，用于记录上次播放位置，发布后不要随意修改。
- `title`：标题。
- `description`：简介。
- `cover`：封面 HTTPS 地址，可为空。
- `audioUrl`：音频 HTTPS 地址。
- `duration`：音频时长，单位秒。
- `sort`：顺序播放排序。
- `publishedAt`：发布日期。
- `enabled`：是否展示和播放。

## 运行

```bash
pnpm install
pnpm dev:mp-weixin
```

微信后台播放已在 `manifest.config.ts` 配置 `requiredBackgroundModes: ['audio']`，正式版提交仍需要微信审核通过。
