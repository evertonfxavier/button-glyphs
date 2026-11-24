// Pure lookup of which catalog key explains a backend status or a stored "last change" line.
import type { Game } from "../model";

export type MessageRef = { key: string; args: Record<string, string> };

/** `code.<code>` plus its args, or undefined when the backend sent no code (the UI shows `message`). */
export function statusRef(game: Pick<Game, "code" | "args">): MessageRef | undefined {
  if (!game.code) return undefined;
  const args: Record<string, string> = {};
  for (const [name, value] of Object.entries(game.args ?? {})) args[name] = String(value);
  return { key: `code.${game.code}`, args };
}

const STYLE_BY_LABEL: Record<string, string> = { Original: "original", PlayStation: "ps", Xbox: "xbox" };

/**
 * The engine stores its last event as an English sentence ("PlayStation applied",
 * "Xbox re-applied after update (...)", "Original restored"). Recognise the common shapes;
 * anything else returns undefined and is shown as written.
 */
export function eventRef(raw: string): MessageRef | undefined {
  const text = raw.trim();
  if (text === "Original restored") return { key: "event.restored", args: {} };
  if (/^Game updated\b/.test(text)) return { key: "event.updated", args: {} };
  const m = /^(Original|PlayStation|Xbox) (applied|re-applied|kept)\b/.exec(text);
  if (!m) return undefined;
  const style = STYLE_BY_LABEL[m[1]];
  const key = m[2] === "applied" ? "event.applied" : m[2] === "kept" ? "event.kept" : "event.reapplied";
  return { key, args: { style } };
}
