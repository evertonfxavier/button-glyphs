import { t } from "../i18n";
import type { Style } from "../model";

export const STYLES: Style[] = ["original", "ps", "xbox"];

export const styleLabel = (style?: Style | string): string =>
  style === "ps" || style === "xbox" ? t(style === "ps" ? "style.ps" : "style.xbox") : t("style.original");

export const styleOptions = (): { data: Style; label: string }[] =>
  STYLES.map((style) => ({ data: style, label: styleLabel(style) }));
