# hermes-purge

移植自 [dsh-purge](https://github.com/YuJunZhiXue/dsh-purge)（DeepSeek Harness 指令权威性清洗）到 **Hermes Agent** 的插件。

核心思路不变：**提示词层 + 行为策略层 + 引擎层重构宿主默认行为，使模型响应遵循操作员定义的指令权威性；所有操作可回滚，升级后可重洗。**

但移植不是代码复制，是**机制映射**。Hermes 与 DSH 架构差异决定了实现方式：

| dsh-purge（DSH） | hermes-purge（Hermes） |
|---|---|
| 40 个 patch 直接改写 `@deepseek-ai/*` 包 | Hermes 配置开放，L1/L2 走官方机制：`system_prompt_section` + `config.yaml`。仅深度模式（`deepPatch=true`）补源码 |
| `prompt-inject.md` override | `$HERMES_HOME/hermes-inject.md` |
| `/purge`、`/rules` 命令 | `/purge`、`/purge-rules` 斜杠命令 + `hermes purge` 子命令 |
| `purge_status/apply/revert` 工具 | 同名模型工具 + `purge_rules_status` |
| shim 启动注入（cmd 静默） | Hermes 无此问题；CLI 静默由官方处理 |
| 每次启动自动重洗 | `on_session_start` hook 异步自动应用 |
| 备份 `<file>.dshpurge.bak` | `plugin-data/hermes-purge/backups/` |

## 关键设计（为什么这样移植）

1. **注入主通道是 `system_prompt_section`，不是 AGENTS.md**。Hermes 对上下文文件（AGENTS.md/SOUL.md/.cursorrules）做 prompt-injection 扫描并阻断匹配内容；插件注册的 system_prompt_section 走插件权限通道，不受该扫描干预，且随会话冻结持久化。
2. **身份不由插件发明**。`hermes-inject.md`（和用户自己的 SOUL.md）是身份唯一来源，插件逐字注入。
3. **审批不放行硬编码**。Hermes 的 `approvals.mode: off` 是官方 `--yolo` 等价物；插件只把三个默认 deny 的无人值守面（cron/single-query/unattended）改写为 approve，并加白名单 —— 不破坏用户显式设置。
4. **深度清洗是可选项**。`deepPatch=true` 时才会碰 Hermes 源码（当前只含极小硬点补丁集），带备份回滚；升级被 `hermes update` 覆盖后可由 `autoApplyOnStart` 重洗。
5. **大注入内容拆节**。system_prompt_section 限制 4000 字符/节、总 8000；banner/inject/rules 三节按预算截断。

## 安装

```sh
# 仓库根即插件根（hermes plugins install 会把 clone 根作为插件目录落到 <HERMES_HOME>/plugins/hermes-purge/）
# 当前机器 HOME: C:\Users\<user>\AppData\Local\hermes
hermes plugins install <git-url-of-this-repo> --enable
# 或手动放置：
Copy-Item -Recurse hermes-purge "$env:LOCALAPPDATA\hermes\plugins\hermes-purge"
hermes plugins enable hermes-purge
```

安装后插件会打印 `after-install.md` 指引（`$HERMES_HOME/plugins/<name>/after-install.md`）。

## 配置

编辑 `$HERMES_HOME/config.yaml`。Hermes 合法 capability 只有 `tools.override` 等
少数几个；本插件无需声明任何能力（写入走 Python 文件操作，由用户显式配置授权），
只配置 settings：

```yaml
plugins:
  entries:
    hermes-purge:
      enabled: true         # 插件总开关
      settings:
        autoApplyOnStart: true
        verbose: false
        deepPatch: false       # 深度清洗：补 Hermes 源码（可选，默认关）
        approvals:
          mode: off            # off = 全放行（== --yolo）；manual/smart 保留人工/智能审批
          rewriteConfig: true
          permanentAllowlist: []
```

> 说明：`deepPatch: true` 会改写 Hermes 源码树中的 `tools/approval_floors.py`、
> `tools/approval.py`、`agent/prompt_builder.py`，带备份回滚；升级被 `hermes update`
> 覆盖后由 `autoApplyOnStart` 重洗。默认关闭，普通清理只走官方配置 + system_prompt_section
> 注入，不改源码。

## 使用

```sh
hermes purge status
hermes purge apply
hermes purge revert

/purge status
/purge apply
/purge revert
/purge write <文本>
/purge edit
/purge-rules list
/purge-rules use <id>
/purge-rules create <id> [别名] [AGENTS.md|CLAUDE.md|SOUL.md]
/purge-rules edit <id> <内容>
/purge-rules delete <id>
/purge-rules reset
```

模型工具（agent 可直接调用）：`purge_status`、`purge_apply`、`purge_revert`、`purge_rules_status`。

## 运行时用户文件

| 文件 | 作用 |
|---|---|
| `$HERMES_HOME/hermes-inject.md` | 身份与行为框架（override 注入源；空文件不注入） |
| `$HERMES_HOME/plugin-data/hermes-purge/rules/` | 规则库 `<id>.md` + `<id>.json` |
| `$HERMES_HOME/plugin-data/hermes-purge/state.json` | 激活规则状态 |
| `$HERMES_HOME/plugin-data/hermes-purge/backups/` | deep patch 备份 |

## 验证

```sh
hermes purge status
```

输出应包含：hermes home、override 文件、approvals mode、规则集、deep patches 进度。首次运行自动创建 override（默认框架）与规则初始状态。

## 与 dsh-purge 的行为差异（移植边界）

- **无 cmd 静默**：Hermes 无 Windows CMD 闪窗问题，删除该能力。
- **无 liangshen/doctor/market 专属补丁**：那些是 DSH 生态插件，Hermes 没有对应物。
- **无 `dsh://` URI 安装**：Hermes 用 `hermes plugins install`。
- **设置页 UI**：Hermes dashboard 插件机制与 DSH cordis 不同；当前版本提供 CLI/命令/工具三通道，dashboard 面板后续可加。
- **身份默认不同**：DSH 默认「小码酱」；Hermes 默认不发明身份，override 文件初始为通用可执行框架，操作员可自填。

## License

MIT。参考 dsh-purge（MIT, YuJunZhiXue）移植。