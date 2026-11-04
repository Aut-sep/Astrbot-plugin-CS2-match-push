# AstrBot CS2 比赛推送插件

面向 **QQ 群管理员和 CS2 赛事观众**的 AstrBot 插件：按战队关注、赛事级别与黑名单筛选比赛，由普通 QQ 机器人自动发送赛前提醒、赛后比分、改期通知和赛程日报，在 AstrBot 内嵌 Web 面板集中管理，支持每个群独立设置推送规则。比赛数据来自 **PandaScore**；QQ 官方机器人可选作用户主动查询入口。

AstrBot plugin for Counter-Strike 2 (CS2) match notifications, team subscriptions and Major brackets in QQ groups.

> **4.4.0** · 新版管理面板，支持深浅主题切换。查看[更新记录](CHANGELOG.md)。

[安装与配置](#安装与配置) · [平台区别](#普通机器人与官方机器人怎么选) · [常用指令](#常用指令) · [图片示例](#图片效果展示) · [常见问题](#常见问题)

## 核心功能

- **按主队与赛事筛选通知**：支持战队关注、赛事级别和战队／联赛黑名单，中文外号、简称与拼写容错方便添加主队。默认推送 S/A 级，关注战队可覆盖级别限制，黑名单仍优先。见[战队搜索说明](docs/team-search.md)。
- **每群独立规则**：各群分别设置关注战队、筛选、赛前提醒分钟数、通知开关、日报时间和图片样式；未设置的项目沿用默认规则。通知按群去重，发送失败时自动重试。见[群规则指南](docs/group-rules.md)。
- **推送预设**：将默认配置、大赛观众、主队粉丝、赛事资讯群与自定义配置集中管理，直接编辑或另存，方便不同群复用。
- **临近改期通知与汇总**：双方队伍确定后，仅临近两小时或已提醒过的比赛发送改期通知；同次刷新 1–2 场逐条发，3 场及以上合并。远期与 TBD 赛程静默更新，减少集中调整带来的刷屏。
- **Major 瑞士轮与淘汰赛可视化**：`/cs对阵图` 查看当前阶段、下一阶段、指定阶段或整届赛事；瑞士轮按赛前战绩分组并展示晋级／淘汰结果，淘汰赛只连接数据源已公布的晋级关系。各阶段开赛前两小时内向启用推送的群发送阶段提醒与对阵图。见[Major 说明](docs/cs-major.md)。
- **Major 专属播报卡片**：Major 赛事使用专属播报卡片，覆盖赛前、赛后、改期、赛事开幕与阶段开赛等场景。跨日改期展示调整前后的完整日期与时间。
- **AstrBot 内嵌管理与查询**：新手引导、接收群、关注、通知规则、赛程／赛果／对阵图工具、配置导入导出集中在面板。支持深浅主题切换，查询和预览不触发群推送。
- **今日选手互动**：内置 127 位选手，每天抽取一位，同日重复查询保留原结果。普通机器人与官方机器人均可使用，详见[选手规则](docs/qq-official.md)。

## 普通机器人与官方机器人怎么选

| 能力 | 普通 QQ 机器人（OneBot／NapCat） | QQ 官方机器人扩展 |
| --- | --- | --- |
| 赛程、赛果、Major、今日选手等查询 | 支持文字指令与图片回复 | 支持已开放的查询，可用 Markdown 和按钮 |
| 今日选手照片与成就图片 | 支持 | 支持；可配合 Markdown 资料排版 |
| Markdown 卡片与快捷按钮 | 使用普通图文和指令交互 | 支持，需账号具备相应权限 |
| 赛前提醒、日报、赛果等自动推送 | 支持 | 本插件官方端不提供 |
| 群组、关注和推送配置管理命令 | 支持，管理操作需管理员权限 | 不开放；由管理员在插件面板配置 |
| 是否必须配置 | 需要自动赛事推送时使用 | 可选，默认关闭 |

需要自动赛事通知时使用 OneBot／NapCat；希望通过官方机器人查询时，可启用官方扩展。两端共用赛程、配置和选手池，官方端支持 Markdown 排版与快捷按钮，富文本不可用时自动回退为文字和图片。

**开启方法**：先在 AstrBot 接入 QQ 官方机器人，再到「插件详情 → CS2 管理 → 高级维护 → 官方机器人扩展」启用，选择账号和开放功能；「使用图片卡片和富文本回复」可控制 Markdown／卡片体验。插件直接使用 AstrBot 已配置的账号，无需重复填写官方账号凭据。官方图片内嵌需要可访问的公开图片地址；未配置或不可用时发送本地图片。详见[配置说明](docs/qq-official.md)。

## 安装要求

| 项目 | 要求 |
| --- | --- |
| AstrBot | 已安装 AstrBot。内嵌管理面板需要 AstrBot 支持插件可视化视图；若无法打开，可使用独立面板。 |
| Python 与依赖 | Python 3.10+，并满足所用 AstrBot 的版本要求；依赖见 [requirements.txt](requirements.txt)。图片在本地生成，无需浏览器或 LLM 服务。 |
| 数据源 | PandaScore API Token，以及可访问 PandaScore API 和图片资源的网络。赛事覆盖和请求额度取决于账户方案。 |
| 消息平台 | 自动推送使用 OneBot／NapCat，机器人需在线并已加入目标群；QQ 官方机器人需在 AstrBot 中接入后启用扩展。 |

## 安装与配置

在 AstrBot 插件管理中使用仓库地址安装插件，确认依赖安装完成，填写 PandaScore API Token 并重载。Token 获取方式见 [PandaScore 文档](https://developers.pandascore.co/docs/authentication)。优先打开「插件详情 → CS2 管理」，按新手引导填写数据源和接收群；普通机器人必须在线并已加入目标群。引导中的连接检查不会发送测试消息。

```text
https://github.com/Aut-sep/Astrbot-plugin-CS2-match-push
```

建议首次配置后确认机器人已加入目标群，再使用以下命令：

```text
/cs设置群 123456789
/cs关注 NaVi
/cs刷新
/cs比赛
/cs状态
```

`/` 是示例唤醒前缀，实际前缀由 AstrBot 全局设置决定。管理员指令需要 AstrBot 管理员权限；`/cs帮助` 查看可用指令。连接多个机器人时，在配置中指定 `push_platform_id`。

多群可重复执行 `/cs设置群 <群号>`。在「订阅管理」选择接收群，应用默认或观赛配置，按需修改并保存，详见[群规则指南](docs/group-rules.md)。首次查询可先用 `/cs状态`、`/cs比赛` 核对内容，`/cs推送` 会实际发送到接收群。

## 管理面板

**建议优先在 AstrBot「插件详情 → CS2 管理」中配置**，内嵌页面使用 AstrBot 自身登录，新手引导会介绍普通机器人与官方扩展的分工。也可启用独立 Web 面板；独立面板在本机和远程访问时均需有效面板令牌，管理员可私聊机器人发送 `/cs面板` 获取。

面板支持深浅主题切换，并记住你的选择。修改完成后，点击「保存配置」统一提交。赛程、赛果和对阵图查询只供查看；「预览」不发送消息，「测试」向测试目标发送，「正式推送」向已配置的接收群发送。

## 主要配置

| 配置项 | 默认值 | 用途 |
| --- | --- | --- |
| `pandascore_token` | 空 | 必填，PandaScore API Token |
| `push_platform_id` | 空 | 多机器人时指定 AstrBot 平台实例 ID |
| `image_push_enabled` | `true` | 使用赛事图片；关闭后使用文字和队标条 |
| `fetch_interval_min` | `5` | 自动刷新赛程的间隔（分钟） |
| `fetch_ahead_days` | `2` | 自动获取的未来赛程范围 |
| `web_panel_enabled` | `true` | 开启独立 Web 管理面板 |
| `web_panel_host` | `127.0.0.1` | 面板监听地址 |
| `web_panel_port` | `8765` | 面板监听端口 |

其他选项包括赛事等级、赛前提醒、日报时间、开幕提醒和黑名单。日报时间以北京时间（UTC+8）解释。日报失败时会重试未成功的目标群；手动 `/cs推送 [天数]` 不属于定时日报，不会自动重试。

## 常用指令

| 指令 | 说明 |
| --- | --- |
| `/cs比赛 [天数/全部]` | 不填查看已获取的全部赛程；填 1–30 按北京时间今天起 N 天筛选 |
| `/cs今日赛程` | 查询今日赛程快捷入口 |
| `/cs未来赛程 [天数]` | 查询未来赛程快捷入口，默认今天及后两天；支持 1–30 天 |
| `/cs赛果 [天数]` | 查询最近赛果，1–30 天，默认 3 天；也可用 `/cs赛果 比赛 <ID>` 指定比赛 |
| `/cs今日选手` | 抽取今日选手，OneBot 返回文字和本地图片 |
| `/cs对阵图 [阶段]` | 查看 Major 当前阶段、指定阶段或全部对阵 |
| `/cs状态`、`/cs群列表`、`/cs关注列表` | 查看当前配置摘要、推送群或关注战队 |
| `/cs刷新` | 立即刷新赛程（管理员） |
| `/cs设置群 <群号>`、`/cs移除群 <群号>` | 管理推送群（管理员） |
| `/cs关注 <战队名>`、`/cs取消 <战队名>` | 管理默认关注战队（管理员） |
| `/cs提醒 [分钟]` | 查看或设置默认赛前提醒时间，1–120 分钟（管理员）；本群可在面板独立设置 |
| `/cs群规则` | 在普通接收群查看本群生效规则 |
| `/cs延迟通知 [开/关]` | 查看或设置改期通知开关（管理员） |
| `/cs推送 [天数]` | 立即发送赛程日报（管理员） |
| `/cs日报` | 查看默认日报设置和子命令（管理员） |
| `/cs面板` | 私聊获取独立面板地址和令牌（管理员） |

赛程指令读取已获取且符合筛选条件的缓存；查询 7 天不会自动把默认 2 天的获取范围扩大到 7 天。需要更多赛程时，先在面板调整 `fetch_ahead_days`（1–30）并刷新。最近赛果按北京时间的**计划开赛日期**回看，不按完赛日期筛选，也沿用本群的赛事级别、关注与黑名单规则；私聊和官方端使用默认规则。

## 图片效果展示

以下为赛事卡片与页面示例，可点击查看原图。图片包含演示数据和历史赛程，具体样式以当前版本为准。

### 赛事通知与查询

<table>
  <tr>
    <td width="50%"><strong>赛前提醒 · 普通机器人自动推送</strong><br>双方队标与开赛时间。<br><br><a href="docs/images/push-upcoming.png"><img src="docs/images/push-upcoming.png" width="320" alt="普通机器人赛前提醒图片，示例赛程"></a></td>
    <td width="50%"><strong>赛后比分 · FaZe 2:1 ASTRAL</strong><br>中央比分与各局胜负；两端均可查询。<br><br><a href="docs/images/result-faze-astral.png"><img src="docs/images/result-faze-astral.png" width="320" alt="插件实际生成的 FaZe 对 ASTRAL 赛果图片"></a></td>
  </tr>
  <tr>
    <td><strong>赛程变更 · 普通机器人自动推送</strong><br>展示调整前后的时间。<br><br><a href="docs/images/push-reschedule.png"><img src="docs/images/push-reschedule.png" width="320" alt="普通机器人赛程变更图片示例"></a></td>
    <td><strong>赛事开幕 · 普通机器人自动推送</strong><br>展示赛事名称、时间与参赛队伍。<br><br><a href="docs/images/push-opening.png"><img src="docs/images/push-opening.png" width="320" alt="普通机器人赛事开幕图片示例"></a></td>
  </tr>
  <tr>
    <td><strong>赛程日报 · 普通机器人定时推送</strong><br>按赛事整理比赛；官方端可主动查询赛程。<br><br><a href="docs/images/push-daily.png"><img src="docs/images/push-daily.png" width="320" alt="普通机器人赛程日报图片示例"></a></td>
    <td><strong>赛程查询 · 两端通用</strong><br>使用 <code>/cs比赛 [天数]</code> 查询；官方端可配合快捷按钮。<br><br><a href="docs/images/query-schedule.png"><img src="docs/images/query-schedule.png" width="320" alt="普通和官方机器人均可返回的赛程图片"></a></td>
  </tr>
</table>

### Major 对阵图

淘汰赛沿已公布的晋级关系连接对阵；瑞士轮按赛前战绩分组，不推测后续抽签。使用 `/cs对阵图` 或在官方菜单点击「Major 对阵」查看。下图为历史版本示例。

[![插件实际生成的 Major 淘汰赛晋级图，两端通用](docs/images/major-knockout.png)](docs/images/major-knockout.png)

<details>
  <summary>展开查看瑞士轮示例</summary>
  <p><a href="docs/images/major-swiss.png"><img src="docs/images/major-swiss.png" alt="插件实际生成的 Major 瑞士轮对阵图，两端通用"></a></p>
</details>

### 今日选手

发送 `/cs今日选手`，抽取今日专属选手，查看照片与生涯成就。普通机器人和官方机器人均支持。

<p><a href="player_cards/hltv_11893_compact.png"><img src="player_cards/hltv_11893_compact.png" width="160" alt="ZywOo 的实际选手照片与已收录成就图片，两端均可使用"></a></p>

官方端可通过 Markdown 展示选手名称、战队、国籍、年龄与抽取状态；发送 `/cs` 可打开功能菜单。选手资料以插件收录时为准。

### 每群规则管理

各群可独立设置提前 1–120 分钟提醒、关注战队与日报时间，也可复制规则或恢复默认设置。下图为历史版本的群规则面板示例。

<details>
  <summary>展开查看桌面与手机暗色面板</summary>
  <p><a href="docs/images/group-rules-desktop.png"><img src="docs/images/group-rules-desktop.png" width="700" alt="每群推送规则桌面面板，演示数据"></a></p>
  <p><a href="docs/images/group-rules-mobile-dark.png"><img src="docs/images/group-rules-mobile-dark.png" width="260" alt="每群推送规则手机暗色面板，演示数据"></a></p>
</details>

## 常见问题

**关注了战队，是不是只推它的比赛？** 默认是“赛事级别或关注战队”，黑名单优先。只看主队时，可在群规则里选择“只看关注战队”并设置本群关注列表；默认规则也可清空赛事级别、保留关注优先。

**官方 QQ 机器人能自动发赛前提醒吗？** 官方扩展支持用户主动查询；自动赛事通知请使用 OneBot／NapCat。

**没有收到赛事通知怎么办？** 先用 `/cs状态` 检查数据源和配置，确认机器人在线、已加入目标群，且群推送开关已开启；再检查赛事级别、关注战队和黑名单。数据源延迟或机器人离线也可能影响通知。

## 注意事项

- 独立面板接口要求有效面板令牌；本机访问也需要登录。容器部署还需映射端口。AstrBot 内嵌页面使用宿主登录。
- 面板里的「预览」不会发送消息；「测试」会向已配置的测试目标发送实际消息；正式推送按已保存配置发送。
- 图片推送默认开启。若图片中文字体缺失，可安装 Noto Sans CJK 或文泉驿字体后重载插件。
- 数据保存在 AstrBot 插件数据目录 `data/plugin_data/astrbot_plugin_cs_match/`。完整备份建议停用插件后复制整个目录，至少保存 `cs_data.json`（配置与通知状态）和 `qq_daily_players.sqlite3`（今日选手记录，如已生成）；面板配置导出不包含凭据、通知状态或抽签数据库。
- 赛事信息来自 PandaScore，可能存在更新延迟或缺失。
- QQ 官方查询扩展默认关闭；启用后只允许用户触发的当前会话查询，官方侧不开放管理指令，也不发送主动赛事通知。配置说明见 [QQ 官方适配器说明](docs/qq-official.md)。
- 第三方队标、选手照片与资料归各自权利人所有；代码复用授权请联系作者。

更新前建议备份插件数据。问题反馈请提供插件与 AstrBot 版本、适配器、复现步骤和脱敏日志，不要发送 Token 或面板令牌。
