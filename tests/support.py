"""Load the hyphenated plugin directory as an importable package for tests."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = "hindsight_curator_under_test"


def load_plugin_package():
    """Import ``hindsight-curator/`` as ``hindsight_curator_under_test`` (relative imports work)."""
    if PKG in sys.modules and hasattr(sys.modules[PKG], "register"):
        return sys.modules[PKG]

    # Evict stale submodules from a previous test process reuse.
    for key in list(sys.modules):
        if key == PKG or key.startswith(PKG + "."):
            del sys.modules[key]

    init_file = ROOT / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        PKG, init_file, submodule_search_locations=[str(ROOT)]
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load plugin from {init_file}")
    module = importlib.util.module_from_spec(spec)
    module.__package__ = PKG
    module.__path__ = [str(ROOT)]  # type: ignore[attr-defined]
    sys.modules[PKG] = module
    spec.loader.exec_module(module)
    return module


def get_submodule(name: str):
    """Return ``hindsight_curator_under_test.<name>`` after loading the package."""
    load_plugin_package()
    full = f"{PKG}.{name}"
    if full in sys.modules:
        return sys.modules[full]
    path = ROOT / f"{name}.py"
    spec = importlib.util.spec_from_file_location(full, path)
    if spec is None or spec.loader is None:
        raise ImportError(full)
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = PKG
    sys.modules[full] = mod
    spec.loader.exec_module(mod)
    return mod
