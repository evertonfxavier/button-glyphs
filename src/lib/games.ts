// Pure helpers about the game list: order and which game is "current".
import type { Game } from "../model";

type Named = Pick<Game, "appid" | "name" | "supported"> & Partial<Pick<Game, "support">>;

const rank = (g: Named): number => (g.supported ? 0 : g.support === "ingame" ? 1 : 2);

/** Supported games first, then the ones with a built-in option, then the rest; each group by name. */
export function orderGames<T extends Named>(games: T[]): T[] {
  return [...games].sort(
    (a, b) =>
      rank(a) - rank(b) ||
      a.name.localeCompare(b.name, undefined, { sensitivity: "base" })
  );
}

/**
 * Current game: the running one if it is supported, else the remembered one if it is still installed,
 * else the first supported game, else the first game.
 */
export function pickCurrent<T extends Named>(
  games: T[],
  runningAppid: string | undefined,
  remembered: string
): T | undefined {
  const ordered = orderGames(games);
  const running = runningAppid ? ordered.find((g) => g.appid === runningAppid && g.supported) : undefined;
  if (running) return running;
  const kept = remembered ? ordered.find((g) => g.appid === remembered) : undefined;
  return kept ?? ordered.find((g) => g.supported) ?? ordered[0];
}
