import asyncio
import json
import os
import sys
from typing import Any, Dict, Optional

import decky

PLUGIN_DIR = getattr(decky, "DECKY_PLUGIN_DIR", os.path.dirname(os.path.abspath(__file__)))
PY_MODULES = os.path.join(PLUGIN_DIR, "py_modules")
if PY_MODULES not in sys.path:
    sys.path.insert(0, PY_MODULES)

from button_glyphs.engine import STYLES, Engine  # noqa: E402

CHECK_INTERVAL_SEC = 60
GLYPH_DIR = os.path.join(PY_MODULES, "button_glyphs", "glyphs")


def make_engine() -> Engine:
    return Engine(
        home=decky.DECKY_USER_HOME,
        settings_dir=decky.DECKY_PLUGIN_SETTINGS_DIR,
        runtime_dir=decky.DECKY_PLUGIN_RUNTIME_DIR,
        glyph_dir=GLYPH_DIR,
        version=getattr(decky, "DECKY_PLUGIN_VERSION", "dev"),
        logger=decky.logger,
    )


class Plugin:
    _engine: Optional[Engine] = None
    _task: Optional["asyncio.Task[None]"] = None

    def _get_engine(self) -> Engine:
        if self._engine is None:
            self._engine = make_engine()
        return self._engine

    async def ping(self) -> Dict[str, str]:
        return {
            "status": "ok",
            "version": getattr(decky, "DECKY_PLUGIN_VERSION", "unknown"),
            "user": getattr(decky, "DECKY_USER", ""),
        }

    async def list_games(self) -> str:
        """JSON string (reliable through the Decky bridge)."""
        try:
            games = await asyncio.to_thread(self._get_engine().list_games)
            return json.dumps({"ok": True, "error": "", "games": games})
        except Exception as exc:
            decky.logger.exception("list_games failed")
            return json.dumps({"ok": False, "error": str(exc), "games": []})

    async def set_style(self, appid: str, style: str) -> Dict[str, Any]:
        if style not in STYLES:
            return {"ok": False, "logs": [], "errors": ["unknown style %s" % style]}
        try:
            return await asyncio.to_thread(self._get_engine().set_style, str(appid), style)
        except Exception as exc:
            decky.logger.exception("set_style failed")
            return {"ok": False, "logs": [], "errors": [str(exc)]}

    async def recheck(self) -> Dict[str, Any]:
        try:
            results = await asyncio.to_thread(self._get_engine().sync_all, "user-check")
        except Exception as exc:
            decky.logger.exception("recheck failed")
            return {"ok": False, "logs": [], "errors": [str(exc)]}
        logs = ["%s: %s" % (r["appid"], ", ".join(r["logs"] or r["errors"])) for r in results]
        errors = [e for r in results for e in r["errors"]]
        return {"ok": not errors, "logs": logs or ["nothing to do"], "errors": errors}

    async def _watch(self) -> None:
        """Re-apply chosen styles after game updates / Steam file verification."""
        while True:
            try:
                for result in await asyncio.to_thread(self._get_engine().sync_all, "watch"):
                    if result.get("changed"):
                        decky.logger.info("re-applied %s: %s", result["appid"], result["logs"])
            except Exception:
                decky.logger.exception("watch iteration failed")
            await asyncio.sleep(CHECK_INTERVAL_SEC)

    async def _main(self):
        decky.logger.info("button-glyphs backend loaded")
        self._task = asyncio.get_running_loop().create_task(self._watch())

    async def _unload(self):
        if self._task:
            self._task.cancel()
        decky.logger.info("button-glyphs backend unloaded")

    async def _uninstall(self):
        decky.logger.info("button-glyphs backend uninstalled")
