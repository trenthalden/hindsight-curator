"""hindsight-curator — bulk curation of stale Hindsight memories via the public HTTP API."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from . import tools as tools_mod
from .command import make_handler
from .curator import Curator

logger = logging.getLogger(__name__)

PLUGIN_NAME = "hindsight-curator"
DEFAULT_API_URL = "http://localhost:8888"
DEFAULT_BANK_ID = "hermes"

_curator: Optional[Curator] = None
_config_override: Optional[dict[str, Any]] = None


def _resolve_api_key() -> str:
    """Read the API key only through Hermes secret scope (never from other plugins' files)."""
    try:
        from agent.secret_scope import get_secret

        return (get_secret("HINDSIGHT_API_KEY", "") or "").strip()
    except Exception:
        return ""


def _data_dir() -> Path:
    try:
        from plugins.plugin_storage import plugin_data_dir

        return plugin_data_dir(PLUGIN_NAME)
    except Exception:
        # Fallback for unit tests / doctor without a full Hermes home wiring.
        from hermes_constants import get_hermes_home

        root = get_hermes_home() / "plugin-data" / PLUGIN_NAME
        root.mkdir(parents=True, exist_ok=True)
        return root


def _settings_from_config_file() -> dict[str, str]:
    """Read api_url / bank_id from plugins.entries when no PluginContext (dashboard API)."""
    api_url = DEFAULT_API_URL
    bank_id = DEFAULT_BANK_ID
    try:
        from hermes_cli.config import load_config_readonly

        raw = load_config_readonly() or {}
        plugins = raw.get("plugins") if isinstance(raw, dict) else None
        entries = plugins.get("entries") if isinstance(plugins, dict) else None
        entry = entries.get(PLUGIN_NAME) if isinstance(entries, dict) else None
        if isinstance(entry, dict):
            settings = entry.get("settings") or entry.get("config") or {}
            if isinstance(settings, dict):
                if settings.get("api_url"):
                    api_url = str(settings["api_url"])
                if settings.get("bank_id"):
                    bank_id = str(settings["bank_id"])
    except Exception:
        pass
    return {"api_url": api_url, "bank_id": bank_id}


def _settings_from_ctx(ctx) -> dict[str, Any]:
    if _config_override is not None:
        return dict(_config_override)
    api_url = DEFAULT_API_URL
    bank_id = DEFAULT_BANK_ID
    if ctx is not None:
        try:
            api_url = str(ctx.get_config("api_url", default=DEFAULT_API_URL) or DEFAULT_API_URL)
        except Exception:
            pass
        try:
            bank_id = str(ctx.get_config("bank_id", default=DEFAULT_BANK_ID) or DEFAULT_BANK_ID)
        except Exception:
            pass
    else:
        file_settings = _settings_from_config_file()
        api_url = file_settings["api_url"]
        bank_id = file_settings["bank_id"]
    return {"api_url": api_url.rstrip("/"), "bank_id": bank_id, "api_key": _resolve_api_key()}


def get_curator(ctx=None) -> Curator:
    """Return (and cache) a Curator bound to the active plugin data dir + settings."""
    global _curator
    settings = _settings_from_ctx(ctx)
    if _curator is not None:
        # Refresh URL/bank/key if settings changed between calls.
        client = _curator.client
        if (
            client.base_url == settings["api_url"]
            and client.bank_id == settings["bank_id"]
            and client.api_key == settings["api_key"]
        ):
            return _curator
    _curator = Curator.create(
        data_dir=_data_dir(),
        api_url=settings["api_url"],
        bank_id=settings["bank_id"],
        api_key=settings["api_key"],
    )
    return _curator


def reset_curator() -> None:
    """Drop the cached curator (tests)."""
    global _curator, _config_override
    if _curator is not None:
        try:
            _curator.journal.close()
        except Exception:
            pass
    _curator = None
    _config_override = None


def configure_for_tests(*, data_dir: Path, api_url: str, bank_id: str, api_key: str = "") -> Curator:
    """Wire a curator for tests without Hermes PluginContext."""
    global _curator, _config_override
    reset_curator()
    _config_override = {"api_url": api_url.rstrip("/"), "bank_id": bank_id, "api_key": api_key}
    _curator = Curator.create(
        data_dir=Path(data_dir),
        api_url=api_url.rstrip("/"),
        bank_id=bank_id,
        api_key=api_key,
    )
    return _curator


def register(ctx) -> None:
    """Hermes plugin entrypoint."""

    def _factory() -> Curator:
        return get_curator(ctx)

    handlers = tools_mod.make_handlers(_factory)
    for name, schema in tools_mod.TOOL_SCHEMAS.items():
        ctx.register_tool(
            name=name,
            toolset=PLUGIN_NAME,
            schema=schema,
            handler=handlers[name],
        )

    ctx.register_command(
        "memory-curate",
        handler=make_handler(_factory, handlers),
        description="Scan / plan / apply / restore Hindsight memory curation",
        args_hint="scan|plan|apply|restore|history …",
    )
    logger.info(
        "hindsight-curator registered (api_url=%s bank_id=%s)",
        _settings_from_ctx(ctx).get("api_url"),
        _settings_from_ctx(ctx).get("bank_id"),
    )
