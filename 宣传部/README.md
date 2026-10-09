# 宣传部 —— 宣传素材与对外发布

对外形象与发布材料统一收纳在这里。部门职责与流程见
[../组织部/PIPELINE.md](../组织部/PIPELINE.md)。

## 目录索引

| 目录/文件 | 内容 |
|---|---|
| `screenshot/` | README 首屏界面截图（浅色 / 深色各一） |
| `donate/` | 打赏收款码（更换方法见 [donate/README.md](donate/README.md)） |
| `logo-kit/` | 标志规范套件：SVG 三版、安全区、缩放测试、品牌方案文档 |
| `Logo设计方案-v2.md` | Logo 设计沿革与定稿记录（B6「规格框」） |
| `imgspec-logo-v2.design/` | Logo 设计源文件（设计工具产物，留档） |

## 发布（Release）流程

1. 生产部交来自检通过的产物后打 zip：`图片转换器.exe` + `_internal\` +
   `README.md` → 命名 `imgspec-vX.Y.Z-win64.zip`
2. 写 Release notes：**新增 / 修复 / 已知问题** 三段，中文为主
3. GitHub Releases 上传 zip、贴 notes、设为 latest
4. 若界面截图已过时：先更新 `screenshot/` 再发布（截图要与实际版本一致）

## 素材更新规则

- README 引用的图片（截图、收款码）**路径与文件名保持不变**，覆盖式更新
- 新宣传素材先入本目录再对外使用，不散落在仓库其他位置
