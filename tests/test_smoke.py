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
(TEST_INSTALL / "tools" / "approval_floors.py").write_text(
    '_HARDLINE_BLOCKED_HINT = "hermes-purge: original hardline"\n', encoding="utf-8")
(TEST_INSTALL / "tools" / "approval.py").write_text(
    '_BLAH = "CIRCUIT BREAKER: blocked"\n', encoding="utf-8")
(TEST_INSTALL / "agent" / "prompt_builder.py").write_text(
    "DEFAULT_AGENT_IDENTITY = (\n  'original identity'\n)\n", encoding="utf-8")

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
    core.save_rule("test1", "# rule one\nhello", {"name": "规则一", "target": "AGENTS.md"})
    listed = core.list_rules()
    check("rule listed", any(r["id"] == "test1" for r in listed), str(listed))
    meta = core.activate_rule("test1")
    check("rule activated target AGENTS.md", meta["target"] == "AGENTS.md", str(meta))
    target = TEST_HOME / "AGENTS.md"
    check("AGENTS.md written", target.is_file() and "# rule one" in target.read_text(encoding="utf-8"))
    text = core.read_active_rule_text()
    check("active rule text injected", "rule one" in text)
    st = core.rules_status()
    check("rules status synced", st["target_synced"] is True, str(st))

    # SOUL.md 目标特殊处理
    core.save_rule("soul1", "be brave", {"name": "灵魂", "target": "SOUL.md"})
    core.activate_rule("soul1")
    soul = core.hermes_home() / "SOUL.md"
    check("SOUL.md header guard", soul.read_text(encoding="utf-8").startswith("# hermes-purge rule override"))

    core.delete_rule("test1")
    core.delete_rule("soul1")
    check("rule deleted", not core.read_rule("test1"))


def test_reset():
    core.save_rule("rx", "rx content", {"name": "rx", "target": "AGENTS.md"})
    core.activate_rule("rx")
    res = core.reset_rules()
    check("reset removed target", len(res["removed"]) == 1, str(res))
    check("state cleared", core._read_state().get("active") is None)


# ── 测试审批配置重写 ───────────────────────────────────────────
def test_approval_rewrite():
    r = core.apply_approval_config(force=True)
    check("approval rewrite applied", r["status"] == "applied", str(r))
    cfg = core.read_config()
    check("cron_mode approved", cfg["approvals"]["cron_mode"] == "approve", str(cfg.get("approvals")))
    check("single_query approved", cfg["approvals"]["single_query_mode"] == "approve")
    check("mode off set", cfg["approvals"]["mode"] == "off")
    check("marker written", "off" in json.dumps(cfg))


# ── 测试 deep patch ────────────────────────────────────────────
def test_deep_patch():
    # 指向 mock 安装根
    core.hermes_install_root.__wrapped__ if False else None
    # 手动 monkeypatch 安装根解析：hermes_install_root 现在找不到 <repo>，直接注入
    install_hook = lambda: TEST_INSTALL  # noqa: E731
    core.hermes_install_root = install_hook  # type: ignore[assignment]

    st = core.gather_state()
    check("deep patch pending on original", st["deep_patches_pending"] >= 1, str(st["patch_status"]))

    bak = core.backup_all()
    check("backup created", len(bak) >= 1, str(bak))
    report = core.apply_patches()
    check("deep patch applied", any(r["status"] == "applied" for r in report), str(report))
    st2 = core.gather_state()
    check("deep patch now applied", st2["deep_patches_applied"] >= 1, str(st2["patch_status"]))
    # 幂等：重复 apply 时已打过的标记为 already
    report2 = core.apply_patches()
    non_missing = [r for r in report2 if r["status"] != "missing_file"]
    check("deep patch idempotent", all(r["status"] == "already" for r in non_missing), str(report2))
    reverted, errors = core.revert_patches()
    check("deep patch reverted", len(reverted) >= 1, f"{reverted} {errors}")
    st3 = core.gather_state()
    check("deep patch back to pending", st3["deep_patches_pending"] >= 1)
    st3 = core.gather_state()
    check("deep patch back to pending", st3["deep_patches_pending"] >= 1)


# ── 测试注入组装 ───────────────────────────────────────────────
def test_inject_sections():
    core.save_rule("inj", "### 注入规则\nrule text", {"name": "inj", "target": "AGENTS.md"})
    core.activate_rule("inj")
    sections = inject.build_sections({"enabled": True})
    ids = [s["id"] for s in sections]
    check("banner section", "purge-banner" in ids, str(ids))
    check("inject section", "purge-inject" in ids, str(ids))
    check("rules section", "purge-rules" in ids, str(ids))
    check("sections under budget", sum(len(s["content"]) for s in sections) <= 8000, str([len(s["content"]) for s in sections]))

    # 超长截断保护
    long_inject = "x" * 6000
    core.override_path().write_text(long_inject, encoding="utf-8")
    sections2 = inject.build_sections({"enabled": True})
    check("long inject truncated", all(len(s["content"]) <= 4000 for s in sections2), str([len(s["content"]) for s in sections2]))
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
    check("register 3 sections", len(ctx.sections) == 3, str([s[0] for s in ctx.sections]))
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