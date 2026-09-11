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
- `content`：正文段落数组，每个数组元素是一段文字，不要把整篇内容放成一个超长字符串。
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
  "content": [
    "这是第一段正文。",
    "这是第二段正文。段落之间会自动留出阅读间距。"
  ],
  "enabled": true,
  "sort": 1
}
```

原音频数据仍保留在 `src/static/data/playlist.json`，供后续恢复音频功能时使用；当前文章版首页不会读取或展示音频。

## 运行

```bash
pnpm install
pnpm dev:mp-weixin
```

当前版本已移除微信后台音频声明和播放器入口，构建产物不再包含 `requiredBackgroundModes: ['audio']`。
