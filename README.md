# CS2 比赛推送插件

为 AstrBot 的 QQ / OneBot（aiocqhttp）适配器提供 CS2 赛程查询、战队关注、赛前提醒、赛后结果、每日赛程和赛事开幕通知，并提供 Web 管理面板。比赛数据来自 PandaScore。

## 安装与配置

1. 在 AstrBot 插件管理中通过本仓库地址安装，填写 PandaScore API Token。
2. 设置自动刷新间隔、提前获取天数和面板监听地址。默认地址为 `127.0.0.1:8765`。
3. 使用管理员指令 `~cs设置群 <群号>` 添加推送目标，或在 Web 面板中配置。
4. 管理员私聊机器人发送 `~cs面板`，获取管理地址和令牌。

`~` 是示例唤醒前缀，实际前缀取决于 AstrBot 全局配置。`~cs帮助` 可查看全部指令。非本机访问管理面板需要令牌；容器中默认回环监听会转换为 `0.0.0.0`，仍需映射对应端口。

连接多个 QQ 机器人时，在插件配置或面板高级设置中填写 `push_platform_id`（AstrBot 平台实例 ID）。留空时选择唯一的 aiocqhttp 实例；多个实例不明确选择时会记录发送失败，避免选择错误账号。

首次安装会把 AstrBot 插件配置写入插件数据文件；此后以已持久化的设置为准，请通过管理面板或管理指令修改运行中的设置。Token 在面板中留空表示保持原值，配置导出不会包含 API Token、面板令牌或运行状态。

## 项目结构

```text
main.py                 插件注册、生命周期、调度、消息组装和机器人指令
client.py               PandaScore 请求、重试、分页与图片下载
store.py                配置、比赛快照和发送回执的 JSON 持久化
config.py               可编辑配置与请求参数的集中校验
delivery.py             AstrBot 主动发送接口兼容与平台路由
formatters.py           消息文本、比赛字段和时间处理
banners.py              横幅图片渲染、字体与长名称截断
web_panel.py            管理面板 HTTP 接口、认证和监听配置
templates/panel.html    管理页面的 HTML、CSS 和 JavaScript
tests/                  调度、请求、持久化、面板和发送兼容测试
.github/workflows/      自动回归测试
```

插件入口仍然是 `main.py`，指令装饰器留在插件类上；拆分的是可独立维护的渲染、验证和发送逻辑。页面资源相对于插件目录加载，不依赖 AstrBot 启动时的工作目录。

## 调度与持久化

- 自动刷新分页读取截止时间内的比赛，再应用赛事等级、关注战队和黑名单规则。失败或不完整响应保留旧任务；成功返回空赛程则清理旧提醒。
- 每场比赛独立等待提醒时间。进入提醒窗口后才获取到的比赛，在开赛前仍会补发；已经开赛则跳过赛前提醒，进入结果查询。
- 比赛开始后独立查询结果，最长等待 6 小时。前 6 次每 5 分钟查询，之后每 15 分钟查询；取消状态停止查询。
- 通知发送成功后才标记完成，每个成功目标单独保存回执。失败目标会重试；已保存回执的目标在同一通知的重试中跳过。赛前和开幕通知重试至开赛时间，结果通知在查询截止时间内重试。
- 时间变更通知发送失败时保留待发记录，下次刷新继续尝试。手动修改单场提醒或发生比赛时间变更时重置赛前通知回执，允许发送新的提醒。
- 管理面板修改提醒、过滤和开幕设置会重建相应任务。端口修改通过切换监听生效，绑定失败时保留旧监听并回滚本次配置。
- 数据保存在 AstrBot 的 `data/plugin_data/astrbot_plugin_cs_match/cs_data.json`，沿用旧数据目录，自动兼容旧版按名称关注的战队和小时制获取范围。JSON 使用同目录临时文件与原子替换，无法解析的旧文件会先备份为 `.invalid-*`。

去重回执记录的是 AstrBot 发送接口正常返回，不代表消息已经被用户阅读。程序在发送成功后、写入回执前中断，仍可能造成重发。完成的通知历史保留最近 500 场比赛、300 个赛事；未完成事件的回执和自定义提醒目前不会按时间自动删除。

## 开发与验证

需要 Python 3.10+；在 AstrBot 中运行时使用其插件运行环境。离线回归测试仅需本仓库依赖：

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

安装 Node.js 后还会运行页面 JavaScript 检查，涵盖文本转义、标签删除及设置保存。未安装 Node 时仅跳过该组检查。GitHub Actions 在 Python 3.10、3.12、3.13 下运行，并安装 Node 执行页面检查。

测试通过轻量模拟替代 AstrBot 接口，不访问 PandaScore、不向 QQ 发送消息；HTTP 端口切换和文件持久化使用真实实现。上线前仍需用实际 AstrBot、PandaScore Token 和测试目标验证一次赛前、赛后推送与图片显示。

## 4.1.1 维护改动

- 修复第一页限制、提醒窗口内漏推、长战队名截断死循环和旧任务清理覆盖新任务。
- 修复发送失败却标记已通知，以及部分目标失败时重复推送的问题。
- 兼容当前 AstrBot 的 `Context.send_message`，保留已有发送工具接口的兼容路径。
- 复用 HTTP 会话，处理限流重试，减少未变化的快照重复写盘。
- 分离页面、渲染、配置校验和发送适配，补充回归测试与自动检查。
- 校验管理接口输入，修复端口热切换、配置回滚、动态 HTML 转义和跨域写入保护。

接口依据：[AstrBot StarTools](https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/core/star/star_tools.py)、[AstrBot Context](https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/core/star/context.py)、[PandaScore 单场比赛](https://developers.pandascore.co/reference/get_matches_matchidorslug)、[PandaScore 筛选与排序](https://developers.pandascore.co/docs/filtering-and-sorting)。
