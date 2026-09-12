# 九哥隐学

九哥隐学原创文章阅读小程序。

## 数据维护

文章数据在 `src/static/data/articles.json`，首页和文章详情页都从这里读取。

每篇文章使用下面字段：

- `id`：稳定 ID，发布后不要随意修改。
- `title`：标题。
- `summary`：文章摘要，显示在首页和详情页开头。
- `category`：分类，例如 `认知`、`成长`、`教育`。
- `cover`：预留的封面 HTTPS 地址，可为空。
- `articleUrl`：正文文件地址，建议使用 HTTPS 地址，例如 `https://example.com/articles/ep001.txt`。
- `audioUrl`：对应音频文件地址，建议使用 HTTPS 地址；当前版本只保存地址，不展示和播放。
- `sort`：顺序展示排序。
- `publishedAt`：发布日期。
- `enabled`：是否展示。

示例：

```json
{
  "id": "ep001",
  "title": "文章标题",
  "summary": "用一两句话概括文章内容。",
  "category": "认知",
  "cover": "",
  "publishedAt": "2026-09-11",
  "articleUrl": "https://example.com/articles/ep001.txt",
  "audioUrl": "https://example.com/audio/ep001.mp3",
  "enabled": true,
  "sort": 1
}
```

正文文件建议使用 UTF-8 编码的 `.txt`，段落之间空一行。文章元数据和正文文件分开维护，修改文章时只需要替换正文文件，不需要把长文本塞进 JSON。

原音频数据仍保留在 `src/static/data/playlist.json`，供后续恢复音频功能时使用；当前文章版首页不会读取或展示音频。

## 运行

```bash
pnpm install
pnpm dev:mp-weixin
```

当前版本已移除微信后台音频声明和播放器入口，构建产物不再包含 `requiredBackgroundModes: ['audio']`。
