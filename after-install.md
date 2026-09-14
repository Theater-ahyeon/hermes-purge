# hermes-purge 安装完成

插件已就位。下一步：

1. **启用插件**（若 `--enable` 未传）：
   ```sh
   hermes plugins enable hermes-purge
   ```

2. **配置**。编辑 `$HERMES_HOME/config.yaml` 的 `plugins.entries.hermes-purge`（无需声明
   非法 capability，本插件不覆盖内置工具）：

   ```yaml
   plugins:
     entries:
       hermes-purge:
         enabled: true
         settings:
           autoApplyOnStart: true
           verbose: false
           deepPatch: false
           approvals:
             mode: off
             rewriteConfig: true
             permanentAllowlist: []
   ```

3. **验证**：
   ```sh
   hermes purge status
   ```

4. **使用**：`/purge apply`（或 `hermes purge apply`）；规则集用
   `/purge-rules create|use|edit|delete|reset`。

覆盖文件（身份行为框架）在 `$HERMES_HOME/hermes-inject.md`，首次运行自动写入
默认框架；空文件不注入，不自动回填。

详情见仓库根 `README.md`。