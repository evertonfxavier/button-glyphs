// Pure classification of a game for the status dots.
import type { Game, Style } from "../model";

export type DotKind = "full" | "partial" | "error" | "ingame" | "none";

export const DOT_KINDS: DotKind[] = ["full", "partial", "error", "ingame", "none"];

type DotInput = Pick<Game, "supported" | "state" | "support">;

/**
 * none = no profile; ingame = no profile, but the game has its own Xbox/PS option; error = profile but unusable now;
 * partial = not fully confirmed; full = confirmed.
 */
export function dotKind(game: DotInput): DotKind {
  if (!game.supported) return game.support === "ingame" ? "ingame" : "none";
  if (game.state === "error") return "error";
  if (game.support === "full") return "full";
  return "partial";
}

/** What one line of the game select shows: the dot's kind, the name, and (supported games only) the chosen style. */
export function optionParts(game: Pick<Game, "supported" | "state" | "support" | "name" | "style">): {
  kind: DotKind;
  name: string;
  style?: Style;
} {
  return {
    kind: dotKind(game),
    name: game.name,
    style: game.supported ? game.style ?? "original" : undefined,
  };
}
