// Pure language mapping. Steam names its languages ("brazilian", "latam"); browsers use BCP-47 tags.
import type { Lang } from "../i18n/core";

export function mapLanguage(raw: unknown): Lang {
  if (typeof raw !== "string") return "en";
  const value = raw.trim().toLowerCase().replace(/_/g, "-");
  if (!value) return "en";
  if (/^pt(-|$)/.test(value) || value === "brazilian" || value === "portuguese") return "pt-BR";
  if (/^es(-|$)/.test(value) || value === "spanish" || value === "latam") return "es";
  return "en";
}

/** The first candidate that says anything decides (Steam's own setting before the browser's). */
export function pickLanguage(candidates: unknown[]): Lang {
  for (const candidate of candidates) {
    if (typeof candidate === "string" && candidate.trim()) return mapLanguage(candidate);
  }
  return "en";
}
