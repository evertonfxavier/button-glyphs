import { tOr } from "../i18n";
import type { Game } from "../model";
import { eventRef, statusRef } from "./messages";
import type { MessageRef } from "./messages";
import { styleLabel } from "./styles";

const render = (ref: MessageRef, fallback: string): string => {
  const vars: Record<string, string> = { ...ref.args };
  if (vars.style) vars.style = styleLabel(vars.style);
  return tOr(ref.key, fallback, vars);
};

/** The status of a supported game in the active language (the backend's English `message` if unknown). */
export const statusText = (game: Game): string => {
  const fallback = game.message ?? "";
  const ref = statusRef(game);
  return ref ? render(ref, fallback) : fallback;
};

/** The stored last-change sentence in the active language (as written if not recognised). */
export const eventText = (raw: string): string => {
  const ref = eventRef(raw);
  return ref ? render(ref, raw) : raw;
};
