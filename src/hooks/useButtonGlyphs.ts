import { toaster } from "@decky/api";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { listGames, pingBackend, recheck, setStyle } from "../api";
import { actionText, errorMessage, parseGames, styleLabel } from "../lib";
import type { Game, Style } from "../model";

const POLL_MS = 10_000;

// Remembered while Decky stays loaded, so reopening the panel keeps the same game.
let lastSelected = "";

export function useButtonGlyphs() {
  const [games, setGames] = useState<Game[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [working, setWorking] = useState<Record<string, Style>>({});
  const [listError, setListError] = useState("");
  const [output, setOutput] = useState("");
  const [pluginVersion, setPluginVersion] = useState("");
  const [selectedAppid, setSelectedAppid] = useState(lastSelected);
  const mounted = useRef(true);

  const supported = useMemo(() => games.filter((g) => g.supported), [games]);
  const selected = useMemo(
    () => games.find((g) => g.appid === selectedAppid) ?? supported[0] ?? games[0],
    [games, supported, selectedAppid]
  );

  const selectGame = useCallback((appid: string) => {
    lastSelected = appid;
    setSelectedAppid(appid);
  }, []);

  const notify = useCallback((title: string, body: string) => {
    toaster.toast({ title, body });
  }, []);

  const load = useCallback(async () => {
    try {
      const result = parseGames(await listGames());
      if (!mounted.current) return;
      setGames(result.games);
      setListError(result.ok ? "" : result.error || "Could not list games");
    } catch (error) {
      if (mounted.current) setListError(errorMessage(error));
    }
    if (mounted.current) setLoaded(true);
  }, []);

  useEffect(() => {
    mounted.current = true;
    void (async () => {
      try {
        const pong = await pingBackend();
        if (pong?.version) setPluginVersion(pong.version);
      } catch (error) {
        setListError(`Backend not reachable: ${errorMessage(error)}`);
      }
      await load();
    })();
    const timer = setInterval(() => void load(), POLL_MS);
    return () => {
      mounted.current = false;
      clearInterval(timer);
    };
  }, [load]);

  const chooseStyle = useCallback(
    async (game: Game, style: Style) => {
      if (style === game.style) return;
      setWorking((w) => ({ ...w, [game.appid]: style }));
      setOutput(`${game.name}: applying ${styleLabel(style)}…`);
      try {
        const result = await setStyle(game.appid, style);
        setOutput(`${game.name}\n${actionText(result)}`);
        if (!result.ok) {
          notify("Button Glyphs", result.errors?.[0] || "Could not apply");
        } else if (result.changed) {
          notify(game.name, `${styleLabel(style)} button glyphs applied`);
        } else if (result.logs?.length) {
          notify(game.name, result.logs[result.logs.length - 1]);
        }
      } catch (error) {
        setOutput(`${game.name}: ${errorMessage(error)}`);
        notify("Button Glyphs", errorMessage(error));
      }
      setWorking((w) => {
        const next = { ...w };
        delete next[game.appid];
        return next;
      });
      await load();
    },
    [load, notify]
  );

  const checkNow = useCallback(async () => {
    setBusy(true);
    setOutput("Checking games…");
    try {
      const result = await recheck();
      setOutput(actionText(result));
      notify("Button Glyphs", result.ok ? "Everything is up to date" : result.errors[0]);
    } catch (error) {
      setOutput(errorMessage(error));
    }
    await load();
    setBusy(false);
  }, [load, notify]);

  return {
    games,
    supported,
    selected,
    loaded,
    busy,
    working,
    listError,
    output,
    pluginVersion,
    selectGame,
    chooseStyle,
    checkNow,
    reload: load,
  };
}

export type ButtonGlyphsModel = ReturnType<typeof useButtonGlyphs>;
