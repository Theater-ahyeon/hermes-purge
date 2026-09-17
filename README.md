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

1. **注入主通道是 `system_prompt_section`，不是 AGENTS.md**。Hermes 对上下文文件（AGENTS.md/SOUL.md/.cursorrules）做 prompt-injection 扫描并阻断匹配内容；插件注册的 system_prompt_section 走插件权限通道，不受该扫描干预，且随会话冻结持久化。规则激活写入的 AGENTS.md/CLAUDE.md 目标文件**可能被该扫描 BLOCK**——那是文件侧的尽力而为；规则真正生效走 system_prompt_section（`purge-rules` 段），两者独立。
2. **身份不由插件发明**。`hermes-inject.md`（和用户自己的 SOUL.md）是身份唯一来源，插件逐字注入。SOUL.md 是身份文件，**不作为规则目标**（禁止覆盖）。
3. **审批尊重显式设置**。Hermes 的 `approvals.mode: off` 是官方 `--yolo` 等价物；插件只改写**未显式设置**的无人值守面（cron/single-query/unattended 走默认 deny 时）为 approve，并加白名单。用户显式写的 `mode`/`cron_mode`/`single_query_mode`（含显式 deny）一律保留——通过与默认值同值的 raw config 键区分。
4. **深度清洗是真语义改写**。`deepPatch=true` 时针对当前 Hermes 源码定位真实 needle 并**执行 replacement**（不是插注释），覆盖三个点：审批硬线拒绝句、否认断路器禁令、默认身份行；带备份回滚。升级被 `hermes update` 覆盖后可由 `autoApplyOnStart` 重洗；`autoRevertOnMissing=true` 时文件缺失自动整体回滚。
5. **注入段总预算受控**。Hermes 对每节 4000 字符、总 8000 字符逐一渲染并**静默丢弃超预算节**；插件用两段固定通道（`purge-core`≤4000 + `purge-rules`≤3800，恒 ≤7800）避免 rules 段被挤掉。

## 安装

实测过的完整步骤。**注意：安装时传的 `--enable` 不会真的启用插件**——它只把文件放到位，
`plugins list` 里仍然是 `not enabled`，必须再显式跑一次 `enable`。

```sh
# 1. 安装（仓库根即插件根，clone 落到 <HERMES_HOME>/plugins/hermes-purge/）
hermes plugins install https://github.com/Theater-ahyeon/hermes-purge

# 2. 启用 —— 这一步不能省
hermes plugins enable hermes-purge
#   会问 "Allow this plugin to replace built-in tools?"。本插件不覆盖内置工具，
#   选 No 即可（非交互环境下自动为 No）。确实需要时才加 --allow-tool-override。

# 3. 验证（应打印状态面板，而不是一行空白）
hermes purge status

# 4. 可选：把 settings 写进 config.yaml（不写则全部使用默认值）
```

不走 Git 时手动放置：

```powershell
Copy-Item -Recurse hermes-purge "$env:LOCALAPPDATA\hermes\plugins\hermes-purge"
hermes plugins enable hermes-purge
$env:LOCALAPPDATA\hermes\bin\hermes.exe purge status
```

`$HERMES_HOME` 在本机是 `C:\Users\<user>\AppData\Local\hermes`。

## 配置

`hermes plugins enable` 只会往 `$HERMES_HOME/config.yaml` 写入这两块：

```yaml
plugins:
  enabled:
    - hermes-purge
  entries:
    hermes-purge:
      allow_tool_override: false
```

**`settings` 不会被自动写入**，需要自己补。不补也能跑（全部取默认值），但要改
`deepPatch` / `approvals` / `verbose` 就必须写：

```yaml
plugins:
  entries:
    hermes-purge:
      settings:
        autoApplyOnStart: true   # 新会话启动时自动应用
        verbose: false
        deepPatch: false         # 深度清洗：补 Hermes 源码（可选，默认关）
        autoRevertOnMissing: false
        approvals:
          mode: off              # off = 全放行（== --yolo）；manual/smart 保留人工/智能审批
          rewriteConfig: true    # 只改写"未显式设置"的键，显式值一律保留
          permanentAllowlist: []
```

Hermes 合法的 capability 只有 `tools.override` 等少数几个；本插件**不需要**任何
capability（写入走 Python 文件操作，由用户显式配置授权），所以上一步的
`allow_tool_override` 保持 `false` 即可。

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
/purge-rules create <id> [别名] [AGENTS.md|CLAUDE.md]
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
| `$HERMES_HOME/plugin-data/hermes-purge/backups/` | deep patch 备份 + 规则激活前目标文件原样备份（`AGENTS.md.pre-purge.bak`） |

规则激活会覆盖 `$HERMES_HOME/<target>`；覆盖前自动备份原始文件，`/purge-rules reset` 优先按备份恢复。

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
- **SOUL.md 不作为规则目标**：DSH 允许规则写 CLAUDE.md/SOUL.md，Hermes 的 SOUL.md 是身份源，允许覆盖会破坏身份加载，故排除。
- **deepPatch 是源码级语义改写**：patch 目标随 Hermes 版本演进；`pattern_not_found` 表示当前版本结构变化，不会假装 applied。

## 测试

`tests/test_smoke.py` 覆盖：路径/override/规则 CRUD+覆盖保护+reset 恢复/审批显式键尊重+隐式键放行/deepPatch 真语义改写+幂等+回滚/注入预算/register 全注册。另有 `tests/verify_real_needles.py`（只读）对当前 Hermes 源码验证 3 个 patch 的实际命中。

## License

MIT。参考 dsh-purge（MIT, YuJunZhiXue）移植。