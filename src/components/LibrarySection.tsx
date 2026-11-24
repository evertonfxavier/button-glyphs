import { ButtonItem, PanelSection, PanelSectionRow } from "@decky/ui";
import { t } from "../i18n";
import { GAP, MUTED } from "../theme";
import { StatusNote } from "./StatusNote";
import type { ButtonGlyphsModel } from "../hooks";

export function LibrarySection({ model }: { model: ButtonGlyphsModel }) {
  const { busy, checkNow, checkResult } = model;
  return (
    <PanelSection title={t("updates.title")}>
      <PanelSectionRow>
        <div style={{ fontSize: "0.72em", color: MUTED }}>{t("updates.note")}</div>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={() => void checkNow()} disabled={busy}>
          {busy ? t("updates.checking") : t("updates.check")}
        </ButtonItem>
      </PanelSectionRow>
      {checkResult ? (
        <PanelSectionRow>
          {checkResult.ok ? (
            <div style={{ fontSize: "0.72em", marginTop: GAP.tight, color: MUTED }}>{checkResult.text}</div>
          ) : (
            <StatusNote kind="error">{checkResult.text}</StatusNote>
          )}
        </PanelSectionRow>
      ) : null}
    </PanelSection>
  );
}
