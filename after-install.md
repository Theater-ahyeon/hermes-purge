# hermes-purge 安装完成

插件文件已就位。**还差两步才算能用**——安装时传的 `--enable` 不会真的启用它。

## 1. 启用（必须，不能省）

```sh
hermes plugins enable hermes-purge
```

会问一句 `Allow this plugin to replace built-in tools?`。本插件不覆盖内置工具，
选 **No** 即可（非交互环境下自动为 No）。只有在你确实需要时才加
`--allow-tool-override`。

确认状态：

```sh
hermes plugins list          # hermes-purge 应为 enabled
```

## 2. 验证

```sh
hermes purge status
```

应当打印出状态面板（Hermes home / override 文件 / approvals mode / deep patches /
规则集目录）。**如果是一行空白，说明插件没被加载或命令注册失败**，按下面排查：

| 现象 | 原因 | 处理 |
|---|---|---|
| `plugins list` 显示 `not enabled` | 安装时的 `--enable` 不生效 | 跑上一步的 `hermes plugins enable` |
| `hermes purge status` 空白 | 旧版 handler 把结果 return 了出去，而 Hermes 只把返回值当退出码 | 更新到修复版（`hermes plugins update hermes-purge`） |
| `invalid choice: 'purge'` | 插件未加载 | 确认 `plugins list` 里是 enabled，然后重启会话 |

## 3. 配置（可选）

`hermes plugins enable` 只会往 `$HERMES_HOME/config.yaml` 写入 `plugins.enabled` 与
`entries.hermes-purge.allow_tool_override`，**不会写 settings**。不写也能跑（全取默认值），
要改 `deepPatch` / `approvals` / `verbose` 才需要补：

```yaml
plugins:
  entries:
    hermes-purge:
      settings:
        autoApplyOnStart: true
        verbose: false
        deepPatch: false
        approvals:
          mode: off
          rewriteConfig: true
          permanentAllowlist: []
```

## 4. 使用

- 斜杠命令：`/purge status|apply|revert|write|edit`、`/purge-rules list|use|create|edit|delete|reset`
- CLI：`hermes purge status|apply|revert`
- 模型工具：`purge_status`、`purge_apply`、`purge_revert`、`purge_rules_status`

覆盖文件（身份行为框架）在 `$HERMES_HOME/hermes-inject.md`，首次运行自动写入默认框架；
空文件不注入，也不自动回填——想用自定义身份就往里写。

详情见仓库根 `README.md`。
