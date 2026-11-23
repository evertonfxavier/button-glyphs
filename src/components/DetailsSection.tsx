import { Focusable, PanelSection, PanelSectionRow } from "@decky/ui";
import { MUTED } from "../theme";
import type { ButtonGlyphsModel } from "../hooks";

export function DetailsSection({ model }: { model: ButtonGlyphsModel }) {
  const { pluginVersion, output, games, supported } = model;
  const summary = [
    `${supported.length} supported of ${games.length} installed game(s)`,
    pluginVersion ? `Plugin v${pluginVersion}` : "",
  ]
    .filter(Boolean)
    .join("\n");
  return (
    <PanelSection title="Details">
      <PanelSectionRow>
        <Focusable style={{ width: "100%" }}>
          <div style={{ fontSize: "0.76em", color: MUTED, whiteSpace: "pre-wrap" }}>{summary}</div>
        </Focusable>
      </PanelSectionRow>
      {output ? (
        <PanelSectionRow>
          <Focusable style={{ width: "100%" }}>
            <div style={{ fontSize: "0.74em", color: MUTED, whiteSpace: "pre-wrap" }}>{output}</div>
          </Focusable>
        </PanelSectionRow>
      ) : null}
    </PanelSection>
  );
}
