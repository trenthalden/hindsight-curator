"""Backend routes for the hindsight-curator Desktop Memory curation page.

Mounted at /api/plugins/hindsight-curator/ when the agent half is enabled.
Exports a FastAPI ``router`` (required by the dashboard plugin mount).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter

router = APIRouter()

_ROOT = Path(__file__).resolve().parents[1]
_PKG = "_hindsight_curator_dashboard"


def _load_plugin():
    """Import the hyphenated plugin package so dashboard routes share curator/journal."""
    if _PKG in sys.modules and hasattr(sys.modules[_PKG], "get_curator"):
        return sys.modules[_PKG]

    for key in list(sys.modules):
        if key == _PKG or key.startswith(_PKG + "."):
            del sys.modules[key]

    init_file = _ROOT / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        _PKG, init_file, submodule_search_locations=[str(_ROOT)]
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load hindsight-curator from {init_file}")
    module = importlib.util.module_from_spec(spec)
    module.__package__ = _PKG
    module.__path__ = [str(_ROOT)]  # type: ignore[attr-defined]
    sys.modules[_PKG] = module
    spec.loader.exec_module(module)
    return module


def _curator():
    plugin = _load_plugin()
    return plugin.get_curator(None)


@router.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "plugin": "hindsight-curator"}


@router.get("/plans")
def plans(limit: int = 50) -> dict[str, Any]:
    try:
        return _curator().list_plans(limit=limit)
    except Exception as exc:  # noqa: BLE001 — plugin contract: never raise out
        return {"ok": False, "error": str(exc)}


@router.get("/plans/{plan_id}")
def plan_detail(plan_id: str) -> dict[str, Any]:
    try:
        return _curator().get_plan(plan_id)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@router.post("/plans/{plan_id}/apply")
def plan_apply(plan_id: str, body: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    body = body or {}
    ids = body.get("ids")
    if ids is not None and not isinstance(ids, list):
        return {"ok": False, "error": "ids must be a list of memory ids", "kind": "validation"}
    try:
        return _curator().apply(plan_id, ids=ids)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@router.post("/plans/{plan_id}/restore")
def plan_restore(plan_id: str) -> dict[str, Any]:
    try:
        return _curator().restore(plan_id=plan_id)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
