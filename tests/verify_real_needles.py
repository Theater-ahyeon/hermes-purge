"""验证 DEEP_PATCHES 的 needle 是否命中真实 Hermes 源码（只读，不写）。"""
import sys
from pathlib import Path

sys.path.insert(0, r"C:\Users\theater\AppData\Local\hermes\hermes-agent")
PLUGIN_DIR = Path(r"E:\codex\dshpursh\hermes-purge")
ROOT = Path(r"C:\Users\theater\AppData\Local\hermes\hermes-agent")

# 构造临时模块上下文，读取 lib/core.py 的 DEEP_PATCHES
import importlib.util
spec = importlib.util.spec_from_file_location(
    "hp_core_probe", PLUGIN_DIR / "lib" / "core.py")
core = importlib.util.module_from_spec(spec)
sys.modules["hp_core_probe"] = core
# 阻止核心模块尝试 import hermes_cli（只读取常量，不执行函数）
import types
fake = types.ModuleType("hermes_constants")
fake.get_hermes_home = lambda: Path("unused")
sys.modules.setdefault("hermes_constants", fake)
spec.loader.exec_module(core)

ok = True
for p in core.DEEP_PATCHES:
    fp = ROOT / Path(*p.rel)
    if not fp.is_file():
        print(f"[{p.id}] MISSING_FILE {p.rel}")
        ok = False
        continue
    text = fp.read_text(encoding="utf-8")
    hit = p.needle in text
    # 模拟 apply 后是否真的改变且含 marker
    changed = text.replace(p.needle, p.replacement, 1) != text
    has_marker_after = p.marker in text.replace(p.needle, p.replacement, 1)
    print(f"[{p.id}] {p.name}: file={fp.name} needle_hit={hit} will_change={changed} marker_after={has_marker_after}")
    if not (hit and changed and has_marker_after):
        ok = False
print("ALL_REAL_SOURCE_PATCHES_VERIFIED" if ok else "REAL_SOURCE_VERIFICATION_FAILED")