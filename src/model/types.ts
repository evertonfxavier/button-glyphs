export type Style = "original" | "ps" | "xbox";

export type GameState = "ok" | "pending" | "running" | "updating" | "error";

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
  last_event?: string;
  has_backup?: boolean;
  roles?: string[];
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
