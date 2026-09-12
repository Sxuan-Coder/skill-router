# Skill Router 使用手册

本手册面向已安装 skill-router 的用户，介绍从初始化到日常路由的完整流程。命令均在 skill 仓库根目录下运行。

## 1. 查看技能来源

```powershell
python scripts/skill_router.py sources --json
```

输出各用户级来源（`codex-user`、`agents-user` 等）的来源 ID、可用性和 skill 数量，不输出绝对路径。默认只扫描 `~/.codex/skills`。

## 2. 首次初始化

```powershell
python scripts/skill_router.py init
```

首次运行会询问是否开启本地使用统计：

- **yes / enable**：创建本地随机密钥，开始按天聚合记录。
- **no / decline**：只保存拒绝状态，不创建密钥，后续不再重复询问。

在 Agent（非交互）环境中，Agent 必须先在对话中征得你的同意，再显式运行 `init --improvement enable` 或 `init --improvement decline`。已初始化后想改变选择，可运行 `usage enable`、`usage disable` 或 `usage decline`。

初始化完成后会生成：

| 文件 | 用途 |
|---|---|
| `references/generated/route-index.md` | 一级领域导航 |
| `references/generated/routes-*.md` | 各领域路书（仅名称、描述、推荐场景） |
| `references/generated/catalog.json` | 路由评分用的公开目录 |
| `references/generated/registry.json` | 内部定位表（不要当路书读） |

## 3. 刷新索引

安装、删除或修改了 skill 之后运行：

```powershell
python scripts/skill_router.py scan            # 增量，复用未变化记录
python scripts/skill_router.py scan --full     # 全量重扫（排除缓存因素时用）
python scripts/skill_router.py scan --all-user-sources   # 统一扫描全部生态来源
python scripts/skill_router.py scan --source agents-user  # 精确指定来源
```

## 4. 路由一个任务

```powershell
python scripts/skill_router.py query "帮我把这份文档导出为 PDF" --limit 5 --json
```

按返回的 `status` 行动：

- **matched**：读取 `primary.skill_md` 指向的完整 SKILL.md 并遵循其工作流。只有任务确实跨输入读取、平台访问或最终产物时，才增加 helper skill。
- **ambiguous**：对比两个 alternatives 的场景；只有关键差异无法判断时才问用户一个问题。
- **no_match**：不要强行选最接近的 skill，继续正常推理。

结合 `confidence`、`score_margin` 和 `reasons` 解释选择；裸分数不是跨任务可比较的概率。

## 5. 使用反馈（默认关闭，需先启用统计）

启用统计后，`query` 自动记录 `recommended`。以下反馈只在对应行为**实际发生**后手动记录：

```powershell
python scripts/skill_router.py feedback selected <skill-id>    # 确实采用了推荐
python scripts/skill_router.py feedback opened <skill-id>      # 读完了 SKILL.md 之后
python scripts/skill_router.py feedback corrected <旧id> --to <新id>  # 选了别的 skill
```

常见误用：不要把"准备读取"记为 opened，不要把 router 推荐记为 selected。

## 6. 查看统计与 placement 建议

```powershell
python scripts/skill_router.py usage status                        # 开关状态
python scripts/skill_router.py usage show --registry <registry>   # 聚合摘要
python scripts/skill_router.py placement plan --registry <registry> --json
```

placement 默认每 30 天在"下一次 query 时"触发一次检查，输出 `project` / `global` / `router-store` / `keep` / `insufficient_data` 建议。这些只是建议，skill-router 不会移动任何文件，也不创建后台任务。清除本地统计需明确运行 `usage clear --yes`。

## 7. 审计与自定义

```powershell
python scripts/skill_router.py audit                # 重复、同名冲突、缺场景、过长描述
```

- 通用别名/阈值写在 `config/router-overrides.json`。
- 个人 skill 名称、目录或纠正规则只写入被 Git 忽略的 `config/router-overrides.local.json`，查询时用 `--overrides` 指定。

## 8. 故障排查

| 现象 | 处理 |
|---|---|
| `sources` 显示来源 unavailable | 该目录不存在属正常，不阻塞其他来源 |
| 查询结果总是 no_match | 先 `scan` 刷新索引；再用 `audit` 检查 skill 是否缺少明确场景描述 |
| 想排除缓存影响 | `scan --full` 全量重扫 |
| registry/catalog 不存在 | 重新运行 `init`（改善计划询问不会重复出现） |

## 隐私要点

- 本地统计只存 HMAC 化的标识和按天计数，无 Prompt、任务文本、skill 名、用户名、路径、时间戳，无网络传输。
- 不执行、不修改、不移动任何被扫描的 skill。
- 生成的目录/注册表、usage 密钥与统计文件均不提交到 Git。
