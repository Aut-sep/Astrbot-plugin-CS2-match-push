# 贡献与仓库维护

## 分支与 PR

1. 从最新 `main` 创建一个用途明确的短期分支：`fix/<主题>`、`feat/<主题>`、`docs/<主题>` 或 `refactor/<主题>`，名称使用小写英文和连字符。
2. 一个分支对应一项工作和一个 PR。已合并的分支不要继续用于下一项修复；从更新后的 `main` 创建新分支。
3. 未完成的工作使用 Draft PR；实现、文档和检查就绪后转为 Ready for review。
4. PR 标题采用 `类型: 中文说明`，例如 `fix: 修复赛前提醒漏发`。提交消息使用同样的类型前缀：`feat`、`fix`、`docs`、`refactor`、`test`、`chore`。
5. PR 说明写清问题、最终改动、验证结果及仍未验证的环境。重复 PR 在确认改动已经整合后关闭，并在说明中链接接替它的 PR。
6. 通常使用 Squash and merge，使 `main` 保留每项工作的一个清楚提交。确有保留分支提交历史的需要时才使用普通合并。
7. 合并后删除已经整合的短期远程分支。个人实验分支仅在确认没有独有提交后清理；不要为了减少分支数量丢失未合并工作。

## 开发与检查

需要 Python 3.10+ 和 Node.js（用于页面 JavaScript 测试）。在仓库目录创建虚拟环境，安装依赖：

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m compileall -q main.py client.py store.py formatters.py banners.py config.py delivery.py web_panel.py
git diff --check
```

CI 覆盖 Python 3.10、3.12、3.13 和 Node.js 22。离线测试使用模拟 AstrBot，不等于真实机器人联调；涉及推送、适配器或图片的改动，在 PR 中说明是否验证了实际 AstrBot、PandaScore 和测试目标。

保持 UTF-8 文本、LF 换行。不要提交虚拟环境、缓存、运行数据、Token、临时日志或编辑器本地配置。功能与指令变更同步更新 README；版本变化同步更新 `metadata.yaml`、`main.py` 注册版本和 CHANGELOG。

## 发布

1. 检查 `main` 的自动测试结果，确认版本号和 CHANGELOG 一致。
2. 在实际 AstrBot 环境验证后，以 `v<版本号>` 创建 tag 和 GitHub Release，例如 `v4.1.1`；发布说明包含改动、升级注意事项和已知限制。
3. 使用 [AstrBot Cloud](https://cloud.astrbot.app/publish) 提交插件或更新。仓库地址和插件标识保持与 `metadata.yaml` 一致。
4. GitHub 合并、GitHub Release 和插件市场发布是独立操作，分别核实结果；没有发布完成前不要把它写成已上线。

## 问题反馈

Issue 标题简洁描述问题，正文包含插件版本、AstrBot 版本、适配器、复现步骤、预期与实际行为、脱敏日志。不要上传完整运行数据或鉴权信息。修复关联 Issue 时在 PR 中使用 `Fixes #编号`。
