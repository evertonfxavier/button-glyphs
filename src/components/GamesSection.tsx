import { DropdownItem, PanelSection, PanelSectionRow, Spinner } from "@decky/ui";
import { FaCheckCircle } from "react-icons/fa";
import { GlyphPreview } from "./GlyphPreview";
import { StatusCard } from "./StatusCard";
import { STYLE_OPTIONS, styleLabel } from "../lib";
import { MUTED, STATE_TONE, TONE_COLOR } from "../theme";
import type { ButtonGlyphsModel } from "../hooks";
import type { Game, Style } from "../model";

function SupportedGame({ game, model }: { game: Game; model: ButtonGlyphsModel }) {
  const pending = model.working[game.appid];
  const style: Style = pending ?? game.style ?? "original";
  const tone = TONE_COLOR[STATE_TONE[game.state ?? "ok"]];
  const status = pending ? (
    <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <Spinner width={12} height={12} /> Applying {styleLabel(pending)}…
    </span>
  ) : (
    <span style={{ color: tone }}>{game.message}</span>
  );

  return (
    <>
      <PanelSectionRow>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            color: TONE_COLOR.ok,
            fontWeight: 700,
            fontSize: "0.9em",
          }}
        >
          <FaCheckCircle /> Supported
        </div>
      </PanelSectionRow>
      <PanelSectionRow>
        <DropdownItem
          label="Button style"
          description={status}
          rgOptions={STYLE_OPTIONS}
          selectedOption={style}
          disabled={Boolean(pending)}
          onChange={(option) => void model.chooseStyle(game, option.data as Style)}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <GlyphPreview style={style} roles={game.roles ?? []} />
      </PanelSectionRow>
      {game.last_event ? (
        <PanelSectionRow>
          <div style={{ fontSize: "0.72em", color: MUTED }}>
            Last change: {game.last_event}
            {game.buildid ? ` · build ${game.buildid}` : ""}
          </div>
        </PanelSectionRow>
      ) : null}
      <PanelSectionRow>
        <div style={{ fontSize: "0.72em", color: MUTED }}>
          The original files are backed up before the first change. Pick “Original” to restore them.
        </div>
      </PanelSectionRow>
    </>
  );
}

export function GamesSection({ model }: { model: ButtonGlyphsModel }) {
  const { games, selected, selectGame } = model;
  if (!games.length) return null;

  return (
    <PanelSection title="Game">
      <PanelSectionRow>
        <DropdownItem
          label="Installed game"
          rgOptions={games.map((g) => ({
            data: g.appid,
            label: g.supported ? `✓ ${g.name}` : g.name,
          }))}
          selectedOption={selected?.appid ?? null}
          strDefaultLabel="Choose a game"
          onChange={(option) => selectGame(String(option.data))}
        />
      </PanelSectionRow>
      {selected ? (
        selected.supported ? (
          <SupportedGame game={selected} model={model} />
        ) : (
          <PanelSectionRow>
            <StatusCard
              tone="warn"
              title="Not supported yet"
              lines={[
                `Button Glyphs can't change the button prompts of ${selected.name} yet.`,
                "Supported games are marked with ✓ in the list.",
              ]}
            />
          </PanelSectionRow>
        )
      ) : null}
    </PanelSection>
  );
}
