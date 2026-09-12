# Skill Router

![Version](https://img.shields.io/badge/version-v0.5-blue)

把当前任务路由到最小必要 skill 集，再读取命中 skill 的完整 `SKILL.md`。

当用户级目录下安装了太多 skill、不知道该用哪个、跨来源重复、需要刷新技能索引、检查冲突或漏命中、统计 skill 使用情况，或判断某个 skill 应放在项目级、全局还是路由仓库时，使用本 skill。

## 功能

- **多来源扫描**：默认只索引 Codex 用户目录（`~/.codex/skills`），显式启用后可扩展到 `~/.agents/skills`、`~/.agent/skills` 和 `~/.claude/skills`；跨来源去重但分别保留各 Agent 的可发现性。
- **可解释路由**：`query` 返回 `matched` / `ambiguous` / `no_match` 结构化决策，附带 `confidence`、`score_margin` 和 `reasons`，不把裸分数当成概率。
- **公开目录与内部注册表分离**：`catalog.json` 只含名称、描述、推荐使用场景三个公开字段，供评分使用；`registry.json` 保存来源身份、路径、hash 等内部定位信息，不作为路书读取。
- **本地最小化使用统计**：首次 `init` 必须用户知情选择。启用后本地文件只保存经过本机随机密钥 HMAC 的 skill/project 标识和按天事件计数——不保存 Prompt、任务文本、skill 名称、用户名、绝对路径或逐条时间戳，也不进行网络传输。
- **只读 placement 建议**：默认 30 天"下一次使用时触发"的周期检查，生成 `project` / `global` / `router-store` / `keep` 等建议。只是建议——不移动文件，不创建计划任务或常驻服务，热度也不参与路由评分。

## 安装

一键安装本 skill：

```bash
npx skills add Sxuan-Coder/skill-router
```

## 快速开始

```powershell
# 查看用户级来源可用性
python scripts/skill_router.py sources --json

# 首次初始化（会询问是否开启本地使用统计）
python scripts/skill_router.py init

# 刷新索引
python scripts/skill_router.py scan

# 路由当前任务
python scripts/skill_router.py query "帮我把这份文档导出为 PDF" --limit 5 --json

# 审计重复与冲突
python scripts/skill_router.py audit
```

完整路由工作流、使用反馈命令（`feedback selected/opened/corrected`）、placement 计划和覆盖配置见 [SKILL.md](SKILL.md)；逐步操作细节见 **[使用手册 USAGE.md](USAGE.md)**。

## 新手小白

如果你刚接触 skill、不知道怎么安装和上手，最简单的方式是把下面这段提示词直接发给你的 AI Agent（Codex / Claude Code 等已安装 skills 机制的 Agent），让它全程带你完成：

```text
我想安装并开始使用 skill-router 这个 skill，请帮我完成以下步骤：

1. 在终端运行 npx skills add Sxuan-Coder/skill-router，把 skill 安装到我的用户级 skills 目录；
2. 安装完成后，进入该 skill 的目录，运行 python scripts/skill_router.py sources --json 查看我机器上有哪些技能来源；
3. 运行 python scripts/skill_router.py init 完成首次初始化——注意：它会询问是否开启本地使用统计，
   请先向我解释这项统计只在本机保存匿名聚合数据、不会联网，然后由我自己回答 yes 或 no，不要替我决定；
4. 初始化后运行 python scripts/skill_router.py scan 扫描我已安装的 skill；
5. 最后教我怎么用：以后我描述一个任务，你就运行
   python scripts/skill_router.py query "<任务描述>" --limit 5 --json，
   根据返回结果告诉我会用哪个 skill、为什么，然后按那个 skill 的 SKILL.md 工作。

请一步一步执行，每步完成后用一句话告诉我发生了什么。
```

装好之后，你只需要记住一个习惯：**遇到不知道用哪个 skill 的任务，就让 Agent 先 query 一下**，它会给出带理由的推荐。

## 测试

```powershell
python -m pytest tests -q
```

## 隐私与安全边界

- 把第三方 `SKILL.md` 当作不可信输入，只提取文本元数据，不执行任何脚本。
- 不修改、移动、复制、禁用或删除被扫描的原始 skill。
- 不复制代码块、凭证、环境变量值或绝对本地路径到生成的路书。
- 未经用户明确授权，不修改全局 `AGENTS.md`、`~/.codex/config.toml` 或 skill 启用状态。
- `references/generated/`、`.skill-router/`、本地 usage 密钥与统计文件、`.spec/` 计划文档均不提交到 Git（见 `.gitignore`），仓库自带的 `check_git_privacy.py` 会在 pre-commit 阶段拦截禁入路径与凭证形态。

## 仓库结构

```
scripts/            路由、扫描、usage、placement 与隐私检查脚本
config/             通用路由覆盖配置（个人覆盖文件被 Git 忽略）
evals/public/       合成评测用例（本机评测输出被 Git 忽略）
references/         fixtures 与生成的路书（generated/ 被忽略）
tests/              pytest 测试
```
