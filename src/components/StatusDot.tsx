import { t } from "../i18n";
import type { TKey } from "../i18n";
import { DOT_COLOR } from "../theme";
import type { DotKind } from "../lib";

const LABEL_KEY: Record<DotKind, TKey> = {
  full: "dot.full",
  partial: "dot.partial",
  error: "dot.error",
  ingame: "dot.ingame",
  none: "dot.none",
};

export const dotLabel = (kind: DotKind): string => t(LABEL_KEY[kind]);

/** Colour is never the only cue: "none" is a hollow ring, and the label is also the accessible name. */
export function StatusDot({ kind, size = 10 }: { kind: DotKind; size?: number }) {
  const label = dotLabel(kind);
  const hollow = kind === "none";
  return (
    <span
      role="img"
      aria-label={t("dot.aria", { label })}
      title={label}
      style={{
        display: "inline-block",
        flex: "none",
        width: size,
        height: size,
        borderRadius: "50%",
        boxSizing: "border-box",
        background: hollow ? "transparent" : DOT_COLOR[kind],
        border: hollow ? `2px solid ${DOT_COLOR.none}` : "none",
      }}
    />
  );
}
