import { toaster } from "@decky/api";
import { Router } from "@decky/ui";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { listGames, pingBackend, recheck, setStyle } from "../api";
import { t } from "../i18n";
import { errorMessage, orderGames, parseGames, pickCurrent, styleLabel } from "../lib";
import type { Game, Style } from "../model";

const POLL_MS = 10_000;

// Kept while Decky stays loaded: reopening the panel shows the last list at once and refreshes behind it.
let lastSelected = "";
let cachedGames: Game[] = [];
let cachedVersion = "";
let everLoaded = false;

/** The Steam app that is running now, or undefined (the store is only partly typed and may be missing). */
const runningAppid = (): string | undefined => {
  try {
    const id = Router?.MainRunningAppID;
    return id ? String(id) : undefined;
  } catch {
    return undefined;
  }
};

const without = <T,>(map: Record<string, T>, key: string): Record<string, T> => {
  const next = { ...map };
  delete next[key];
  return next;
};

export function useButtonGlyphs() {
  const [games, setGames] = useState<Game[]>(cachedGames);
  const [loaded, setLoaded] = useState(everLoaded);
  const [busy, setBusy] = useState(false);
  const [working, setWorking] = useState<Record<string, Style>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [listError, setListError] = useState("");
  const [backendError, setBackendError] = useState("");
  const [checkResult, setCheckResult] = useState<{ ok: boolean; text: string } | null>(null);
  const [pluginVersion, setPluginVersion] = useState(cachedVersion);
  const [selectedAppid, setSelectedAppid] = useState(lastSelected);
  const mounted = useRef(true);
  const inflight = useRef(false);
  const again = useRef(false);
  const picked = useRef(false);

  const ordered = useMemo(() => orderGames(games), [games]);
  const supported = useMemo(() => ordered.filter((g) => g.supported), [ordered]);
  const selected = useMemo(
    () => games.find((g) => g.appid === selectedAppid) ?? pickCurrent(games, undefined, ""),
    [games, selectedAppid]
  );

  const selectGame = useCallback((appid: string) => {
    lastSelected = appid;
    setSelectedAppid(appid);
  }, []);

  // First list of this mount: start on the running game if it is supported, else on the remembered one.
  useEffect(() => {
    if (picked.current || !games.length) return;
    picked.current = true;
    const first = pickCurrent(games, runningAppid(), lastSelected);
    if (first) selectGame(first.appid);
  }, [games, selectGame]);

  const notify = useCallback((title: string, body: string) => {
    try {
      toaster.toast({ title, body });
    } catch {
      // a toast is a courtesy; never let it break an apply
    }
  }, []);

  const load: () => Promise<void> = useCallback(async () => {
    if (inflight.current) {
      again.current = true; // the backend holds a lock while it applies: one more read after this one, no queue
      return;
    }
    inflight.current = true;
    try {
      const result = parseGames(await listGames());
      if (!mounted.current) return;
      if (result.ok) {
        cachedGames = result.games;
        setGames(result.games);
      }
      setListError(result.ok ? "" : result.error || "unknown error");
    } catch (error) {
      if (mounted.current) setListError(errorMessage(error));
    } finally {
      inflight.current = false;
    }
    everLoaded = true;
    if (mounted.current) setLoaded(true);
    if (again.current && mounted.current) {
      again.current = false;
      await load();
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    void (async () => {
      try {
        const pong = await pingBackend();
        if (pong?.version && mounted.current) {
          cachedVersion = pong.version;
          setPluginVersion(pong.version);
        }
        if (mounted.current) setBackendError("");
      } catch (error) {
        if (mounted.current) setBackendError(errorMessage(error));
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
      setErrors((e) => without(e, game.appid));
      setWorking((w) => ({ ...w, [game.appid]: style }));
      try {
        const result = await setStyle(game.appid, style);
        if (!result.ok) {
          const detail = result.errors?.[0] || t("apply.failedShort");
          setErrors((e) => ({ ...e, [game.appid]: detail }));
          notify(game.name, t("apply.failedShort"));
        } else if (result.changed) {
          notify(game.name, style === "original" ? t("apply.restored") : t("apply.done", { style: styleLabel(style) }));
        }
      } catch (error) {
        setErrors((e) => ({ ...e, [game.appid]: errorMessage(error) }));
        notify(game.name, t("apply.failedShort"));
      }
      setWorking((w) => without(w, game.appid));
      await load();
    },
    [load, notify]
  );

  const checkNow = useCallback(async () => {
    setBusy(true);
    setCheckResult(null);
    try {
      const result = await recheck();
      setCheckResult(
        result.ok
          ? { ok: true, text: t("updates.upToDate") }
          : { ok: false, text: t("updates.failed", { detail: result.errors?.[0] ?? "?" }) }
      );
    } catch (error) {
      setCheckResult({ ok: false, text: t("updates.failed", { detail: errorMessage(error) }) });
    }
    await load();
    setBusy(false);
  }, [load]);

  return {
    games: ordered,
    supported,
    selected,
    loaded,
    busy,
    working,
    errors,
    listError,
    backendError,
    checkResult,
    pluginVersion,
    selectGame,
    chooseStyle,
    checkNow,
    reload: load,
  };
}

export type ButtonGlyphsModel = ReturnType<typeof useButtonGlyphs>;
