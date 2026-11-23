import { callable } from "@decky/api";
import type { ActionResult, Style } from "../model";

export const pingBackend = callable<[], Record<string, string>>("ping");

export const listGames = callable<[], string>("list_games");

export const setStyle = callable<[appid: string, style: Style], ActionResult>("set_style");

export const recheck = callable<[], ActionResult>("recheck");
