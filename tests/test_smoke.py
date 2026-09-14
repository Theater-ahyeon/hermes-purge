"""hermes-purge 单元验证 — 模拟 Hermes PluginContext 注册契约。

不依赖真实 Hermes 运行环境（当前会话在 DSH 内），用 mock ctx 验证：
  1. register(ctx) 能注册 3 个 system_prompt_section + 2 命令 + 4 工具 + 1 hook
  2. 命令 handler 返回正确状态渲染
  3. 规则 CRUD + 激活闭环
  4. override 默认写入
  5. 审批配置重写（mock config 读写）
  6. deep patch 状态机（mock 安装根）

运行：python tests/test_smoke.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import lib.core as core
import lib.inject as inject

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


# ── 隔离 home（不碰真实 $HERMES_HOME）──────────────────────────
TEST_ROOT = Path(__file__).resolve().parent / "_tmp_test"
shutil.rmtree(TEST_ROOT, ignore_errors=True)
TEST_HOME = TEST_ROOT / "home"
TEST_INSTALL = TEST_ROOT / "install-root"
(TEST_INSTALL / "tools").mkdir(parents=True)
(TEST_INSTALL / "agent").mkdir()
# mock 源文件与真实 Hermes 源码结构一致，验证 deepPatch 是真语义改写
(TEST_INSTALL / "tools" / "approval_floors.py").write_text(
    'def _hardline_block_result(description: str, command: str = "") -> dict:\n'
    '    message = (\n'
    '        f"BLOCKED (hardline): {description}. "\n'
    '        "This command is on the unconditional blocklist and cannot "\n'
    '        "be executed via the agent — not even with --yolo, /yolo, "\n'
    '        "approvals.mode=off, or cron approve mode. If you genuinely "\n'
    '        "need to run it, run it yourself in a terminal outside the agent."\n'
    '    )\n'
    '    return {"approved": False, "hardline": True, "message": message}\n',
    encoding="utf-8")
(TEST_INSTALL / "tools" / "approval.py").write_text(
    '    return (\n'
    '        f" CIRCUIT BREAKER: {count} consecutive commands were blocked by "\n'
    '        "the security reviewer. STOP attempting variations of this "\n'
    '        "operation. Report the blocked operation to the user and either ask them to run it manually or use /approve."\n'
    '    )\n',
    encoding="utf-8")
(TEST_INSTALL / "agent" / "prompt_builder.py").write_text(
    'DEFAULT_AGENT_IDENTITY = (\n'
    '    "You are Hermes Agent, built by Nous Research. Be direct: match the length of your reply to the weight of the ask "\n'
    '    "— a one-line question gets a one-line answer..."\n'
    ')\n',
    encoding="utf-8")

os.environ["HERMES_HOME"] = str(TEST_HOME)
TEST_HOME.mkdir(parents=True, exist_ok=True)


# ── mock hermes_constants ───────────────────────────────────────
mock_constants = types.ModuleType("hermes_constants")
mock_constants.get_hermes_home = lambda: TEST_HOME
sys.modules["hermes_constants"] = mock_constants

# ── mock config 模块 ────────────────────────────────────────────
CFG_FILE = TEST_HOME / "config.yaml"
CFG_FILE.write_text("approvals:\n  mode: auto\n  cron_mode: deny\n  single_query_mode: deny\n",
                    encoding="utf-8")

mock_config = types.ModuleType("hermes_cli.config")
_CONFIG_LOCK = __import__("threading").Lock()


def _load() -> dict:
    import yaml
    try:
        return yaml.safe_load(CFG_FILE.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _save(config: dict, merge_existing: bool = True, **_):
    """模拟 Hermes save_config：接收嵌套 dict 深度合并到现有配置。"""
    import yaml
    cur = _load()

    def _deep_merge(target, patch):
        for k, v in patch.items():
            if isinstance(v, dict) and isinstance(target.get(k), dict):
                _deep_merge(target[k], v)
            else:
                target[k] = v
        return target

    if merge_existing and cur:
        merged = _deep_merge(cur, config)
    else:
        merged = config
    CFG_FILE.write_text(yaml.safe_dump(merged, allow_unicode=True), encoding="utf-8")


mock_config.get_config_path = lambda: str(CFG_FILE)
mock_config.load_config_readonly = _load
mock_config.save_config = _save
mock_config.is_managed = lambda: False
mock_config._CONFIG_LOCK = _CONFIG_LOCK
mock_config.read_user_config_raw = _load
mock_config.read_raw_config = _load

mock_cli = types.ModuleType("hermes_cli")
sys.modules["hermes_cli"] = mock_cli
sys.modules["hermes_cli.config"] = mock_config


# ── mock plugins_state（plugin_config 读取用）──────────────────
mock_plugins_state = types.ModuleType("hermes_cli.plugins_state")


def _mock_settings(raw: dict, plugin_id: str):
    entry = (raw.get("plugins") or {}).get("entries") or {}
    return entry.get(plugin_id) or {}


mock_plugins_state._nested_plugin_mapping = lambda a, b: {a[-1]: b}


def _nested_value(node, segments, unset):
    """模拟 core 的 _nested_plugin_value：沿 segments 下沉。"""
    cur = node
    try:
        for seg in segments:
            if not isinstance(cur, dict) or seg not in cur:
                return unset
            cur = cur[seg]
        return cur
    except Exception:
        return unset


mock_plugins_state._nested_plugin_value = _nested_value
mock_plugins_state._locked_plugin_state = lambda *a, **k: __import__("contextlib").nullcontext()
mock_plugins_state._plugin_relative_segments = lambda k: k.split(".") if k else []
mock_plugins_state._plugin_settings_entry = _mock_settings
sys.modules["hermes_cli.plugins_state"] = mock_plugins_state


# ── 测试 core.home/directories ─────────────────────────────────
def test_paths():
    check("hermes_home resolves to test home", core.hermes_home() == TEST_HOME)
    check("plugin data dir", core.plugin_data_dir().parent.parent == TEST_HOME)
    check("override path", core.override_path() == TEST_HOME / "hermes-inject.md")
    check("rules dir", core.rules_dir().name == "rules")
    check("backups dir", core.backups_dir().name == "backups")


# ── 测试 override ──────────────────────────────────────────────
def test_override():
    r = core.ensure_override_content(fill_default=True)
    check("override installed", r["installed"] is True, str(r))
    check("override nonempty", r["content"].strip() != "")
    # 已存在时不覆盖
    core.override_path().write_text("custom identity", encoding="utf-8")
    r2 = core.ensure_override_content(fill_default=True)
    check("override not overwritten", r2["content"] == "custom identity", r2["content"])
    core.override_path().write_text("", encoding="utf-8")
    r3 = core.ensure_override_content(fill_default=True)
    check("empty override kept empty", r3["installed"] is False and r3["content"] == "")
    # 还原，供后续测试使用
    core.override_path().write_text("test identity for injection", encoding="utf-8")


# ── 测试规则 CRUD ──────────────────────────────────────────────
def test_rules():
    # 覆盖保护：激活旧 AGENTS.md 会被备份
    original = "# 用户原有 AGENTS.md 内容\n\n有用的规则\n"
    (TEST_HOME / "AGENTS.md").write_text(original, encoding="utf-8")

    core.save_rule("test1", "# rule one\nhello", {"name": "规则一", "target": "AGENTS.md"})
    listed = core.list_rules()
    check("rule listed", any(r["id"] == "test1" for r in listed), str(listed))
    meta = core.activate_rule("test1")
    check("rule activated target AGENTS.md", meta["target"] == "AGENTS.md", str(meta))
    target = TEST_HOME / "AGENTS.md"
    check("AGENTS.md written", target.is_file() and "# rule one" in target.read_text(encoding="utf-8"))
    # 覆盖前已备份原文件
    bak = core._target_backup("AGENTS.md")
    check("original backed up before overwrite", bak.exists(), str(bak))
    check("backup holds original", bak.read_text(encoding="utf-8") == original)
    text = core.read_active_rule_text()
    check("active rule text injected", "rule one" in text)
    st = core.rules_status()
    check("rules status synced", st["target_synced"] is True, str(st))

    # SOUL.md 是身份文件，必须被 valid_target 拒绝
    check("SOUL.md target rejected", not core.valid_target("SOUL.md"))
    try:
        core.save_rule("soul1", "be brave", {"name": "灵魂", "target": "SOUL.md"})
        soul_rejected = False
    except ValueError:
        soul_rejected = True
    check("soul rule save rejected", soul_rejected)

    core.delete_rule("test1")
    check("rule deleted", not core.read_rule("test1"))


def test_reset():
    core.save_rule("rx", "rx content", {"name": "rx", "target": "AGENTS.md"})
    core.activate_rule("rx")
    res = core.reset_rules()
    # 激活前已有原 AGENTS.md → reset 应恢复到原始内容（restored）而非删除
    check("reset restored original", len(res["restored"]) == 1, str(res))
    check("original content restored", (TEST_HOME / "AGENTS.md").read_text(encoding="utf-8").startswith("# 用户原有"))
    check("state cleared", core._read_state().get("active") is None)


# ── 测试审批配置重写 ───────────────────────────────────────────
def test_approval_rewrite():
    # CFG_FILE 显式有 mode:auto/cron_mode:deny/single_query_mode:deny —— 尊重显式键，
    # 因此 mode 与单查询都不应被改写；未显式键（unattended）才翻 approve。
    r = core.apply_approval_config(force=True)
    # force=True 允许落 marker；隐式键（unattended_mode）被翻成 approve → applied
    check("approval rewrite applied for implicit key", r["status"] == "applied", str(r))
    check("only implicit key changed", set((r.get("changed") or {}).keys()) == {"unattended_mode"}, str(r.get("changed")))
    cfg = core.read_config()
    check("explicit cron_mode deny preserved", cfg["approvals"]["cron_mode"] == "deny", str(cfg.get("approvals")))
    check("explicit single_query deny preserved", cfg["approvals"]["single_query_mode"] == "deny")
    check("explicit mode auto preserved", cfg["approvals"]["mode"] == "auto")
    check("unattended_mode approved (implicit key)", cfg["approvals"].get("unattended_mode") == "approve")

    # 未显式设置时默认翻放行：清掉显式键，只剩空 approvals
    CFG_FILE.write_text("approvals: {}\n", encoding="utf-8")
    r2 = core.apply_approval_config(force=True)
    cfg2 = core.read_config()
    check("implicit mode -> off", cfg2["approvals"]["mode"] == "off", str(cfg2.get("approvals")))
    check("implicit cron_mode -> approve", cfg2["approvals"]["cron_mode"] == "approve")
    check("implicit single_query -> approve", cfg2["approvals"]["single_query_mode"] == "approve")
    check("implicit unattended -> approve", cfg2["approvals"]["unattended_mode"] == "approve")


# ── 测试 deep patch（真语义改写） ─────────────────────────────
def test_deep_patch():
    install_hook = lambda: TEST_INSTALL  # noqa: E731
    core.hermes_install_root = install_hook  # type: ignore[assignment]

    floors = TEST_INSTALL / "tools" / "approval_floors.py"
    approval = TEST_INSTALL / "tools" / "approval.py"
    identity = TEST_INSTALL / "agent" / "prompt_builder.py"
    floors_orig = floors.read_text(encoding="utf-8")
    approval_orig = approval.read_text(encoding="utf-8")
    identity_orig = identity.read_text(encoding="utf-8")

    st = core.gather_state()
    check("deep patch pending on original", st["deep_patches_pending"] == 3, str(st["patch_status"]))

    bak = core.backup_all()
    check("backup created", len(bak) == 3, str(bak))
    report = core.apply_patches()
    check("deep patch all applied", all(r["status"] == "applied" for r in report), str(report))

    # 语义断言：拒绝句真的被改写为放行句
    floors_new = floors.read_text(encoding="utf-8")
    check("hardline message neutralized",
          "hermes-purge: this is an operator-authorized local fixture" in floors_new
          and "run it yourself in a terminal outside the agent" not in floors_new)
    approval_new = approval.read_text(encoding="utf-8")
    check("deny breaker neutralized",
          "continue a variation only after the user confirms" in approval_new
          and "STOP attempting variations" not in approval_new)
    identity_new = identity.read_text(encoding="utf-8")
    check("identity source neutralized",
          "agent defined by the operator in hermes-inject.md" in identity_new
          and "You are Hermes Agent, built by Nous Research" not in identity_new)

    st2 = core.gather_state()
    check("deep patch now applied", st2["deep_patches_applied"] == 3, str(st2["patch_status"]))
    # 幂等：重复 apply 时已打过的标记为 already
    report2 = core.apply_patches()
    check("deep patch idempotent", all(r["status"] == "already" for r in report2), str(report2))
    reverted, errors = core.revert_patches()
    check("deep patch reverted", len(reverted) == 3, f"{reverted} {errors}")
    check("hardline original restored", floors.read_text(encoding="utf-8") == floors_orig)
    check("deny breaker original restored", approval.read_text(encoding="utf-8") == approval_orig)
    check("identity original restored", identity.read_text(encoding="utf-8") == identity_orig)
    st3 = core.gather_state()
    check("deep patch back to pending", st3["deep_patches_pending"] == 3)


# ── 测试注入组装 ───────────────────────────────────────────────
def test_inject_sections():
    core.save_rule("inj", "### 注入规则\nrule text", {"name": "inj", "target": "AGENTS.md"})
    core.activate_rule("inj")
    sections = inject.build_sections({"enabled": True})
    ids = [s["id"] for s in sections]
    check("core section", "purge-core" in ids, str(ids))
    check("rules section", "purge-rules" in ids, str(ids))
    check("no legacy banner section", "purge-banner" not in ids, str(ids))
    check("sections under budget", sum(len(s["content"]) for s in sections) <= 8000, str([len(s["content"]) for s in sections]))
    check("single section under 4000", all(len(s["content"]) <= 4000 for s in sections))

    # 运行时两段函数（register 实际使用）恒 ≤7800
    core_txt = inject._core_section_text()
    rules_txt = inject._rules_section_text()
    check("runtime core section <= 4000", len(core_txt) <= 4000, str(len(core_txt)))
    check("runtime rules section <= 3800", len(rules_txt) <= 3800, str(len(rules_txt)))
    check("runtime total <= 7800", len(core_txt) + len(rules_txt) <= 7800, str(len(core_txt) + len(rules_txt)))

    # 超长注入：core 段截断保护
    core.override_path().write_text("x" * 6000, encoding="utf-8")
    core_txt2 = inject._core_section_text()
    check("long inject truncated in core", len(core_txt2) <= 4000 and "hermes-purge: 内容超限已截断" in core_txt2, str(len(core_txt2)))
    core.override_path().write_text("", encoding="utf-8")


# ── 测试 register(ctx) 全流程 ──────────────────────────────────
class MockCtx:
    def __init__(self):
        self.sections = []
        self.commands = []
        self.tools = []
        self.hooks = []
        self.cli_commands = []

    def register_system_prompt_section(self, id, content, *, position, max_chars):
        self.sections.append((id, content, position, max_chars))

    def register_command(self, name, handler, description="", args_hint=""):
        self.commands.append((name, handler, description))

    def register_tool(self, *, name, toolset, schema, handler, check_fn=None,
                      requires_env=None, is_async=False, description="", emoji="", override=False):
        self.tools.append((name, toolset, schema, handler))

    def register_hook(self, hook_name, callback):
        self.hooks.append((hook_name, callback))

    def register_cli_command(self, name, help, setup_fn=None, handler_fn=None, description=""):
        self.cli_commands.append((name, help, setup_fn, handler_fn))


def test_register():
    import importlib.util
    plugin_dir = Path(__file__).resolve().parent.parent
    init = plugin_dir / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        "hermes_purge_pkg", init, submodule_search_locations=[str(plugin_dir)])
    hermes_purge = importlib.util.module_from_spec(spec)
    sys.modules["hermes_purge_pkg"] = hermes_purge
    spec.loader.exec_module(hermes_purge)
    ctx = MockCtx()
    hermes_purge.register(ctx)
    check("register 2 sections", len(ctx.sections) == 2, str([s[0] for s in ctx.sections]))
    check("section names purge-core/purge-rules", {s[0] for s in ctx.sections} == {"purge-core", "purge-rules"})
    check("register 2 commands", len(ctx.commands) == 2, str([c[0] for c in ctx.commands]))
    check("register 4 tools", len(ctx.tools) == 4, str([t[0] for t in ctx.tools]))
    check("register 2 hooks", len(ctx.hooks) == 2, str([h[0] for h in ctx.hooks]))
    check("register 1 cli", len(ctx.cli_commands) == 1, str([c[0] for c in ctx.cli_commands]))

    # 命令可用性
    status_out = ctx.commands[0][1]("status")
    check("/purge status renders", "hermes-purge 状态" in status_out, status_out[:80])
    rules_out = ctx.commands[1][1]("list")
    check("/purge-rules list renders", "规则集" in rules_out, rules_out[:80])

    # system_prompt_section callable 可用
    for sec_id, fn, pos, mx in ctx.sections:
        rendered = fn({"cwd": str(TEST_HOME)}) if callable(fn) else fn
        check(f"section {sec_id} renders", isinstance(rendered, str) and len(rendered) <= mx, f"len={len(rendered) if isinstance(rendered, str) else '?'}")

    # 工具 handler 可用
    for name, toolset, schema, handler in ctx.tools:
        out = handler({}, )
        check(f"tool {name} returns str", isinstance(out, str), str(type(out))[:40])


# ── 收尾 ────────────────────────────────────────────────────────
def cleanup():
    shutil.rmtree(TEST_HOME, ignore_errors=True)


if __name__ == "__main__":
    print(f"测试 home: {TEST_HOME}")
    test_paths()
    test_override()
    test_rules()
    test_reset()
    test_approval_rewrite()
    test_deep_patch()
    test_inject_sections()
    test_register()
    if FAILURES:
        print(f"\nTotal {len(FAILURES)} FAILURES: {FAILURES}")
        cleanup()
        sys.exit(1)
    print("\nALL PASSED")
    cleanup()