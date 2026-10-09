# 反馈部 —— 用户反馈与日志收集

反馈是生产部更新的输入源头。部门职责与流程见
[../组织部/PIPELINE.md](../组织部/PIPELINE.md)。

## 收集渠道

- **GitHub Issues**（主渠道）：已配模板（`.github/ISSUE_TEMPLATE/`），
  bug 模板会引导用户写清版本、系统与复现步骤
- 邮件、社群等渠道来的反馈由维护者代录入 Issue，统一在 Issues 里跟踪

## 用户侧指引（回复用户时可直接引用）

1. 先看 README 的「已知限制」章节，确认不是预期行为
2. 提 bug 请带：软件版本（Release 页可查）、Windows 版本、复现步骤、
   界面截图；PPT 转换问题请说明本机是否装有 PowerPoint
3. 转换日志在主界面左栏「转换日志」卡片里，可选中复制；
   「一键导出日志文件」功能在规划中（见 PIPELINE.md 阶段规划）

## 处理流程

Issue 进来 → 信息齐全吗（缺了按模板追问）→ 打标签
（bug / enhancement / question）→ bug 转生产部修复 →
修复随下个 Release 发布 → 在 Issue 里回复修复版本号并关闭。
