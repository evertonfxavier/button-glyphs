import { DropdownItem, PanelSection, PanelSectionRow, Spinner } from "@decky/ui";
import { CurrentGame } from "./CurrentGame";
import { dotLabel, StatusDot } from "./StatusDot";
import { t } from "../i18n";
import { DOT_KINDS, optionParts, styleLabel } from "../lib";
import { GAP, MUTED } from "../theme";
import type { ButtonGlyphsModel } from "../hooks";
import type { Game } from "../model";

const ELLIPSIS = { overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", minWidth: 0 } as const;

function Legend() {
  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        gap: `${GAP.tight}px 12px`,
        fontSize: "0.7em",
        color: MUTED,
        marginBottom: GAP.block,
      }}
    >
      {DOT_KINDS.map((kind) => (
        <span key={kind} style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
          <StatusDot kind={kind} size={8} />
          {dotLabel(kind)}
        </span>
      ))}
    </div>
  );
}

/** One line, in the options and in the closed select: dot, name (takes the free width, truncated), style at the end. */
const gameLabel = (game: Game) => {
  const { kind, name, style } = optionParts(game);
  return (
    <span style={{ display: "flex", alignItems: "center", gap: GAP.block, minWidth: 0, width: "100%" }}>
      <StatusDot kind={kind} />
      <span style={{ ...ELLIPSIS, flex: 1 }}>{name}</span>
      {style ? <span style={{ flex: "none", fontSize: "0.8em", color: MUTED }}>{styleLabel(style)}</span> : null}
    </span>
  );
};

/** Top of the panel: legend, the game select (dots inside its options), then the style controls. */
export function GamesSection({ model }: { model: ButtonGlyphsModel }) {
  const { games, selected, loaded } = model;
  const reading = !loaded && !games.length;

  const description = reading ? (
    <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <Spinner width={12} height={12} />
      {t("games.reading")}
    </span>
  ) : !games.length ? (
    t("games.empty")
  ) : undefined;

  return (
    <PanelSection>
      <PanelSectionRow>
        <Legend />
      </PanelSectionRow>
      <PanelSectionRow>
        <DropdownItem
          layout="below"
          label={t("games.label")}
          description={description}
          rgOptions={games.map((game) => ({ data: game.appid, label: gameLabel(game) }))}
          selectedOption={selected?.appid ?? null}
          disabled={!games.length}
          onChange={(option) => model.selectGame(String(option.data))}
        />
      </PanelSectionRow>
      <CurrentGame model={model} />
    </PanelSection>
  );
}
