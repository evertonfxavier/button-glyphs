// Pure UI logic, run with `pnpm run test:ui` (node's built-in runner strips the TypeScript types).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { interpolate, translate, translateOr } from "../../src/i18n/core.ts";
import { DOT_KINDS, dotKind, optionParts } from "../../src/lib/dots.ts";
import { orderGames, pickCurrent } from "../../src/lib/games.ts";
import { mapLanguage, pickLanguage } from "../../src/lib/lang.ts";
import { eventRef, statusRef } from "../../src/lib/messages.ts";

const load = (name) => JSON.parse(readFileSync(new URL(`../../src/i18n/${name}.json`, import.meta.url), "utf8"));
const CATALOGS = { en: load("en"), "pt-BR": load("pt-BR"), es: load("es") };

test("steam and browser language names map to the three languages", () => {
  for (const raw of ["brazilian", "pt-BR", "pt_BR", "pt", "portuguese", "PT-pt"]) assert.equal(mapLanguage(raw), "pt-BR", raw);
  for (const raw of ["spanish", "latam", "es", "es-MX", "es_419", "ES"]) assert.equal(mapLanguage(raw), "es", raw);
  for (const raw of ["english", "en-US", "french", "estonian", "schinese", "", "  ", undefined, null, 5]) {
    assert.equal(mapLanguage(raw), "en", String(raw));
  }
});

test("the first non-empty candidate decides, even when it maps to English", () => {
  assert.equal(pickLanguage([undefined, "", "brazilian", "es"]), "pt-BR");
  assert.equal(pickLanguage(["french", "es"]), "en");
  assert.equal(pickLanguage([]), "en");
});

test("dot kinds: none / ingame / error / partial / full", () => {
  assert.equal(dotKind({ supported: false }), "none");
  assert.equal(dotKind({ supported: false, support: "ingame" }), "ingame");
  assert.equal(dotKind({ supported: false, support: "ingame", state: "error" }), "ingame");
  assert.equal(dotKind({ supported: false, state: "error" }), "none");
  assert.equal(dotKind({ supported: true, state: "error", support: "full" }), "error");
  assert.equal(dotKind({ supported: true, state: "ok", support: "partial" }), "partial");
  assert.equal(dotKind({ supported: true, state: "pending" }), "partial"); // not confirmed = partial
  assert.equal(dotKind({ supported: true, state: "ok", support: "full" }), "full");
  assert.equal(dotKind({ supported: true, state: "running", support: "full" }), "full");
});

const G = (appid, name, supported) => ({ appid, name, supported });
const GAMES = [G("3", "Zelda", false), G("2", "beta", true), G("1", "Alpha", true), G("4", "Abe", false)];

test("games are ordered supported first, then by name ignoring case", () => {
  assert.deepEqual(orderGames(GAMES).map((g) => g.name), ["Alpha", "beta", "Abe", "Zelda"]);
  assert.deepEqual(GAMES.map((g) => g.appid), ["3", "2", "1", "4"], "input is not mutated");
});

test("current game: running supported > remembered > first supported > first", () => {
  assert.equal(pickCurrent(GAMES, "2", "1").appid, "2");
  assert.equal(pickCurrent(GAMES, "3", "1").appid, "1", "a running unsupported game does not win");
  assert.equal(pickCurrent(GAMES, "99", "4").appid, "4", "remembered, even unsupported");
  assert.equal(pickCurrent(GAMES, undefined, "gone").appid, "1");
  assert.equal(pickCurrent([G("5", "Only", false)], undefined, "").appid, "5");
  assert.equal(pickCurrent([], undefined, ""), undefined);
});

test("status code lookup and last-event recognition", () => {
  assert.deepEqual(statusRef({ code: "will_apply", args: { style: "ps" } }), { key: "code.will_apply", args: { style: "ps" } });
  assert.equal(statusRef({}), undefined);
  assert.deepEqual(eventRef("PlayStation applied"), { key: "event.applied", args: { style: "ps" } });
  assert.deepEqual(eventRef("Xbox re-applied after update (x)"), { key: "event.reapplied", args: { style: "xbox" } });
  assert.deepEqual(eventRef("Xbox kept (patch format updated)"), { key: "event.kept", args: { style: "xbox" } });
  assert.deepEqual(eventRef("Original restored"), { key: "event.restored", args: {} });
  assert.equal(eventRef("Game updated (build 5)").key, "event.updated");
  assert.equal(eventRef("Original: the game's own value kept"), undefined);
});

test("every event key and style the lookup can produce exists in every catalog", () => {
  for (const key of ["event.applied", "event.reapplied", "event.restored", "event.updated", "event.kept", "style.ps"]) {
    for (const [lang, catalog] of Object.entries(CATALOGS)) assert.ok(catalog[key], `${lang}: ${key}`);
  }
});

test("translate interpolates, falls back to English, then to the key", () => {
  assert.equal(interpolate("a {x} b {y}", { x: 1 }), "a 1 b {y}");
  assert.equal(translate(CATALOGS, "pt-BR", "details.summary", { supported: 3, total: 14, version: "0.3.10" }), "3 de 14 jogos · v0.3.10");
  const partial = { en: { k: "english {n}" }, "pt-BR": {}, es: {} };
  assert.equal(translate(partial, "es", "k", { n: 2 }), "english 2");
  assert.equal(translate(partial, "es", "missing"), "missing");
  assert.equal(translateOr(partial, "es", "missing", "backend text"), "backend text");
});

test("a game-select line: dot kind, name, and the configured style for supported games only", () => {
  assert.deepEqual(optionParts({ name: "Quantum Break", supported: true, state: "ok", support: "full", style: "ps" }), {
    kind: "full", name: "Quantum Break", style: "ps",
  });
  assert.equal(optionParts({ name: "A", supported: true, state: "ok", support: "partial" }).style, "original");
  assert.deepEqual(optionParts({ name: "Doom", supported: false, style: "ps" }), { kind: "none", name: "Doom", style: undefined });
  assert.equal(optionParts({ name: "B", supported: true, state: "error", support: "full", style: "xbox" }).kind, "error");
});

test("games with a built-in option sort after the supported ones and before the rest", () => {
  const list = [
    { appid: "1", name: "Aaa", supported: false },
    { appid: "2", name: "Zzz", supported: false, support: "ingame" },
    { appid: "3", name: "Mmm", supported: true, support: "full" },
    { appid: "4", name: "Bbb", supported: false, support: "ingame" },
  ];
  assert.deepEqual(orderGames(list).map((g) => g.appid), ["3", "4", "2", "1"]);
  assert.equal(pickCurrent(list, undefined, "").appid, "3", "default stays the first supported game");
});

test("an in-game game line has the blue dot and no style", () => {
  assert.deepEqual(optionParts({ name: "Digimon", supported: false, support: "ingame", style: "ps" }), {
    kind: "ingame", name: "Digimon", style: undefined,
  });
});

test("legend kinds all have a label in every language", () => {
  const kinds = ["full", "partial", "error", "ingame", "none"];
  assert.deepEqual(DOT_KINDS, kinds, "the legend shows exactly these, in this order");
  for (const [lang, catalog] of Object.entries(CATALOGS)) for (const k of kinds) assert.ok(catalog[`dot.${k}`], `${lang}: dot.${k}`);
});
