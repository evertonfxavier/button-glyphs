import { PanelSection, PanelSectionRow } from "@decky/ui";
import { t } from "../i18n";
import { MUTED } from "../theme";
import type { ButtonGlyphsModel } from "../hooks";

export function DetailsSection({ model }: { model: ButtonGlyphsModel }) {
  const { pluginVersion, games, supported } = model;
  const vars = { supported: supported.length, total: games.length, version: pluginVersion };
  return (
    <PanelSection title={t("details.title")}>
      <PanelSectionRow>
        <div style={{ fontSize: "0.76em", color: MUTED }}>
          {pluginVersion ? t("details.summary", vars) : t("details.summaryNoVersion", vars)}
        </div>
      </PanelSectionRow>
    </PanelSection>
  );
}
