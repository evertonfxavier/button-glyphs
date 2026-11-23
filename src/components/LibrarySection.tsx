import { ButtonItem, PanelSection, PanelSectionRow } from "@decky/ui";
import { MUTED } from "../theme";
import type { ButtonGlyphsModel } from "../hooks";

export function LibrarySection({ model }: { model: ButtonGlyphsModel }) {
  const { busy, checkNow } = model;
  return (
    <PanelSection title="Updates">
      <PanelSectionRow>
        <div style={{ fontSize: "0.72em", color: MUTED }}>
          Chosen styles are re-applied automatically after game updates (checked every minute).
        </div>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={() => void checkNow()} disabled={busy}>
          Check for game updates now
        </ButtonItem>
      </PanelSectionRow>
    </PanelSection>
  );
}
