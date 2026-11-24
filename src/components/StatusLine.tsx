import { PanelSection, PanelSectionRow } from "@decky/ui";
import { StatusNote } from "./StatusNote";
import { t } from "../i18n";
import type { ButtonGlyphsModel } from "../hooks";

/** Neutral note(s) with a red dot, only when something needs attention. It never replaces the panel. */
export function StatusLine({ model }: { model: ButtonGlyphsModel }) {
  const { backendError, listError, supported, selected } = model;

  const lines: string[] = [];
  if (backendError) lines.push(t("status.backendError", { detail: backendError }));
  else if (listError) lines.push(t("status.listError", { detail: listError }));
  // the current game shows its own error under the select; name the others here
  const others = supported.filter((g) => g.state === "error" && g.appid !== selected?.appid);
  if (others.length) lines.push(t("status.attention", { names: others.map((g) => g.name).join(", ") }));
  if (!lines.length) return null;

  return (
    <PanelSection>
      {lines.map((text, index) => (
        <PanelSectionRow key={index}>
          <StatusNote kind="error">{text}</StatusNote>
        </PanelSectionRow>
      ))}
    </PanelSection>
  );
}
