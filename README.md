# Pixiv 图片搜索获取插件

面向 Nekro Agent 的 Pixiv/P站图片检索与获取插件。插件通过 Lolicon API v2 检索 Pixiv 作品元数据，按调用条件下载图片到当前聊天的上传目录，并返回可直接交给 `send_msg_file` 使用的 `/app/uploads/...` 路径。

## 功能概述

- 支持按标签、关键词、作者 UID 检索 Pixiv 插画。
- 支持获取 Pixiv 公开排行榜作品，例如日榜第一、周榜第一。
- 支持填写 Pixiv OAuth `refresh_token`，优先使用登录态 App API，失败时退回免登录公开接口。
- 支持控制获取数量、图片尺寸、R18 策略、是否包含 AI 作品。
- 默认只检索全年龄内容，并默认排除 AI 作品。
- 下载图片时使用 Pixiv Referer 与浏览器 User-Agent，提升图片代理链路的兼容性。
- 返回作品标题、PID、页码、作者、尺寸、标签、Pixiv 链接和本地可发送文件路径。
- 下载文件保存到当前聊天频道的上传目录，不污染其他聊天上下文。

## 插件信息

| 项目 | 内容 |
| --- | --- |
| 插件名 | Pixiv图片搜索获取 |
| 模块名 | `nekro_plugin_pixiv_fetcher` |
| 作者 | `Akiyo-dayo` |
| 版本 | `1.0.0` |
| 沙盒方法 | `pixiv_search_and_fetch` |

## 工具接口

```python
pixiv_search_and_fetch(
    tags: list[str],
    keyword: str = "",
    uid: int = 0,
    count: int = 1,
    mode: str = "",
    ranking_mode: str = "",
    ranking_position: int = 0,
    r18: str = "",
    size: str = "",
    include_ai: bool | None = None,
) -> str
```

### 参数说明

| 参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `tags` | `list[str]` | 必填 | Pixiv 标签列表，例如 `["初音ミク"]`、`["明日方舟", "能天使"]`。不知道标签时可传空列表并使用 `keyword`。 |
| `keyword` | `str` | `""` | 额外关键词，会作为检索条件参与搜索。 |
| `uid` | `int` | `0` | Pixiv 作者 UID。传 `0` 表示不限作者。 |
| `count` | `int` | `1` | 本次希望获取的图片数量，实际数量不会超过插件配置 `MAX_RESULTS`。 |
| `mode` | `str` | `""` | 获取模式。空值时使用 WebUI 配置 `DEFAULT_MODE`；`search` 表示条件搜索；`ranking` 表示获取 Pixiv 排行榜。 |
| `ranking_mode` | `str` | `""` | 排行榜类型。空值时使用 WebUI 配置 `DEFAULT_RANKING_MODE`，支持 `daily`、`weekly`、`monthly`、`rookie`、`original`、`male`、`female`。 |
| `ranking_position` | `int` | `0` | 排行榜起始名次。`0` 时使用 WebUI 配置 `DEFAULT_RANKING_POSITION`；`1` 表示排行榜第一。 |
| `r18` | `str` | `""` | 内容策略。空值时使用 WebUI 配置 `DEFAULT_R18_MODE`；`safe`/`全年龄` 只取全年龄；`adult`/`r18` 只取 R18；`mixed`/`all` 混合。若配置 `ALLOW_R18=false`，会强制全年龄。 |
| `size` | `str` | `""` | 图片尺寸。空值时使用 WebUI 配置 `DEFAULT_SIZE`；`regular` 更稳定；`original` 获取原图，体积更大、速度更慢。 |
| `include_ai` | `bool \| None` | `None` | 是否允许 AI 作品。`None` 时使用 WebUI 配置 `DEFAULT_INCLUDE_AI`。 |

### 返回格式

成功时返回文本摘要，每条结果包含：

- Pixiv 作品标题、PID、页码和作者 UID。
- 图片尺寸、R18 标记、AI 类型。
- 主要标签和 Pixiv 原作品链接。
- 已下载图片的 `/app/uploads/...` 文件路径。

Agent 获取返回后，应将 `file` 字段中的路径交给基础交互插件的 `send_msg_file` 发送。

## 配置项

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `API_URL` | `https://api.lolicon.app/setu/v2` | Lolicon API v2 地址。 |
| `TIMEOUT_SECONDS` | `30` | API 请求与图片下载的单次超时时间。 |
| `MAX_RESULTS` | `3` | 单次工具调用最多下载图片数，范围 `1-10`。 |
| `ALLOW_R18` | `false` | 是否允许 R18 内容。关闭时无论调用参数如何都只取全年龄。 |
| `EXCLUDE_AI_BY_DEFAULT` | `true` | 默认排除 AI 作品。 |
| `DEFAULT_SIZE` | `regular` | 默认图片尺寸，可设为 `regular` 或 `original`。 |
| `DEFAULT_MODE` | `search` | WebUI 默认获取模式，可设为 `search` 或 `ranking`。 |
| `DEFAULT_RANKING_MODE` | `daily` | WebUI 默认排行榜类型。 |
| `DEFAULT_RANKING_POSITION` | `1` | WebUI 默认排行榜起始名次。 |
| `DEFAULT_R18_MODE` | `safe` | WebUI 默认 R18 策略。 |
| `DEFAULT_INCLUDE_AI` | `false` | WebUI 默认是否允许 AI 作品。 |
| `PIXIV_REFRESH_TOKEN` | 空 | 可选 secret。填写后优先使用 Pixiv 登录态 App API；留空则使用免登录公开接口。 |

## 使用示例

获取一张初音未来全年龄插画：

```python
pixiv_search_and_fetch(tags=["初音ミク"], count=1)
```

获取两张明日方舟能天使相关插画：

```python
pixiv_search_and_fetch(tags=["明日方舟", "能天使"], count=2)
```

按作者 UID 搜索：

```python
pixiv_search_and_fetch(tags=[], uid=123456, count=1)
```

请求原图：

```python
pixiv_search_and_fetch(tags=["風景"], count=1, size="original")
```

获取 Pixiv 日榜第一：

```python
pixiv_search_and_fetch(tags=[], mode="ranking", ranking_mode="daily", ranking_position=1, count=1)
```

获取 Pixiv 周榜前三：

```python
pixiv_search_and_fetch(tags=[], mode="ranking", ranking_mode="weekly", ranking_position=1, count=3)
```

## 内容策略

插件默认 `ALLOW_R18=false`，即使调用参数传入 `r18="adult"` 或 `r18="mixed"`，也会强制使用全年龄检索。若管理员确需开放 R18，请在插件配置页显式开启 `ALLOW_R18`，并要求调用时明确传入对应策略。

该设计用于避免 Agent 在用户没有明确请求时误取成人内容，也便于按实例独立管理内容边界。

## 免登录与登录态边界

未填写 `PIXIV_REFRESH_TOKEN` 时，插件不携带 Pixiv 登录态，适合获取公开可见的插画搜索结果与公开排行榜结果。免登录模式的优点是部署简单、不会保存账号凭证；限制是无法保证覆盖 Pixiv 全量内容。

免登录模式通常无法稳定覆盖：

- 登录后才完整可见的作品详情、年龄门槛内容和受限内容。
- 关注限定、私密、已删除、地区或账号状态限制的作品。
- 需要官方登录态才能稳定解析的完整原图链路、收藏推荐、个性化推荐等能力。

如果目标是“尽可能接近 Pixiv App 登录后的完整能力”，请填写 Pixiv OAuth `refresh_token`，插件会在运行时换取短期 `access_token`。不建议保存账号密码。Cookie 方案可作为临时调试手段，但更容易过期，也更依赖网页登录状态。

推荐的凭证获取流程：

1. 在受信任的本机或服务器上运行一次性 Pixiv OAuth 登录辅助脚本：`python get_pixiv_refresh_token.py`。
2. 浏览器打开授权页面，由账号本人完成登录和授权。
3. 辅助脚本接收回调 code 并换取 `refresh_token`。
4. 将 `refresh_token` 填入 WebUI 插件配置 `PIXIV_REFRESH_TOKEN`，后续由插件自动刷新 access token。

登录态启用后，插件会优先使用 Pixiv App API 搜索、排行榜和作品图片 URL；如果登录态刷新失败、接口暂时不可用或 token 失效，会记录日志并退回免登录公开接口。

## 部署方式

将 `nekro_plugin_pixiv_fetcher` 目录放入 Nekro Agent 的插件包目录：

```text
plugins/packages/nekro_plugin_pixiv_fetcher/
```

然后在 `configs/nekro-agent.yaml` 的 `PLUGIN_ENABLED` 中加入：

```yaml
- Akiyo_dayo.nekro_plugin_pixiv_fetcher
```

重启 Nekro Agent 后，在插件管理或日志中确认插件加载成功。

## 故障排查

### 搜索返回空结果

- 减少标签数量，优先使用作品名、角色名、常用 Pixiv 日文标签。
- 检查 `EXCLUDE_AI_BY_DEFAULT` 是否导致 AI 作品被过滤。
- 检查 `ALLOW_R18` 与调用参数是否符合预期。

### 下载失败

- 检查容器到 `api.lolicon.app` 与 `i.pixiv.re` 的网络连通性。
- 将 `size` 从 `original` 改为 `regular`。
- 检查是否配置了可用代理；插件会优先复用 Nekro Agent 的 `DEFAULT_PROXY`，代理失败时自动直连重试。

### Agent 找到文件但无法发送

- 确认返回路径为 `/app/uploads/<filename>`。
- 确认发送时使用基础交互插件的 `send_msg_file`。
- 查看 Nekro Agent 日志中是否有文件路径转换或权限错误。

## 验收标准

一次完整上线应满足：

- `python -m py_compile` 无语法错误。
- 插件目录存在于目标实例的 `plugins/packages` 下。
- `PLUGIN_ENABLED` 已加入 `Akiyo_dayo.nekro_plugin_pixiv_fetcher`。
- 重启后目标 NA 实例 `/api/health` 返回 `{"ok": true}`。
- 工具实际调用可以返回至少一条 Pixiv 元数据与 `/app/uploads/...` 文件路径。
- `mode="ranking", ranking_mode="daily", ranking_position=1` 可以获取 Pixiv 公开日榜第一。
