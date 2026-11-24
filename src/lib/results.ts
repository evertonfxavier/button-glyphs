import type { Game, GamesResult } from "../model";

export const errorMessage = (error: unknown): string =>
  error instanceof Error ? error.message : String(error);

// The error text stays English (it is a technical cause); the UI wraps it in a translated sentence.
export const parseGames = (raw: unknown): GamesResult => {
  let data: unknown = raw;
  if (typeof raw === "string") {
    try {
      data = JSON.parse(raw);
    } catch {
      return { ok: false, error: `invalid JSON: ${raw.slice(0, 80)}`, games: [] };
    }
  }
  if (!data || typeof data !== "object") {
    return { ok: false, error: "empty reply", games: [] };
  }
  const obj = data as Partial<GamesResult>;
  return {
    ok: Boolean(obj.ok),
    error: String(obj.error ?? ""),
    games: Array.isArray(obj.games) ? (obj.games as Game[]) : [],
  };
};
