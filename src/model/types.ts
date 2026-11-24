export type Style = "original" | "ps" | "xbox";

export type GameState = "ok" | "pending" | "running" | "updating" | "error";

// How far a profile is confirmed in the game (from its docs/games page).
export type SupportLevel = "full" | "partial";

// Games without a profile may still be listed as "ingame": they have their own Xbox/PS option in the game's settings.
export type GameSupport = SupportLevel | "ingame";

export type Game = {
  appid: string;
  name: string;
  supported: boolean;
  buildid: string;
  // present only for supported games
  style?: Style;
  applied?: Style;
  state?: GameState;
  message?: string;
  // stable id of `message` (+ its values) so the UI can translate it; absent for uncommon messages
  code?: string;
  args?: Record<string, string>;
  support?: GameSupport;
  // where / how to set it, only for support === "ingame" and only when known
  how?: string;
  last_event?: string;
  has_backup?: boolean;
  roles?: string[];
  original_hint?: string;
};

export type GamesResult = {
  ok: boolean;
  error: string;
  games: Game[];
};

export type ActionResult = {
  ok: boolean;
  logs: string[];
  errors: string[];
  changed?: boolean;
};
