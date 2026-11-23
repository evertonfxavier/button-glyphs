import type { Style } from "../model";

export const STYLE_OPTIONS: { data: Style; label: string }[] = [
  { data: "original", label: "Original" },
  { data: "ps", label: "PlayStation" },
  { data: "xbox", label: "Xbox" },
];

export const styleLabel = (style?: Style): string =>
  STYLE_OPTIONS.find((o) => o.data === style)?.label ?? "Original";
