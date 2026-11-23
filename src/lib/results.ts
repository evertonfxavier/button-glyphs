import type { ActionResult, Game, GamesResult } from "../model";

export const errorMessage = (error: unknown): string =>
  error instanceof Error ? error.message : String(error);

export const parseGames = (raw: unknown): GamesResult => {
  let data: unknown = raw;
  if (typeof raw === "string") {
    try {
      data = JSON.parse(raw);
    } catch {
      return { ok: false, error: `Invalid JSON from backend: ${raw.slice(0, 120)}`, games: [] };
    }
  }
  if (!data || typeof data !== "object") {
    return { ok: false, error: "Empty response from backend", games: [] };
  }
  const obj = data as Partial<GamesResult>;
  return {
    ok: Boolean(obj.ok),
    error: String(obj.error ?? ""),
    games: Array.isArray(obj.games) ? (obj.games as Game[]) : [],
  };
};

export const actionText = (result: ActionResult): string => {
  const lines = [...(result.logs ?? [])];
  if (result.errors?.length) lines.push("", "errors:", ...result.errors);
  return lines.join("\n");
};
