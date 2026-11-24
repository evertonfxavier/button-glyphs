import { DropdownItem, PanelSectionRow, Spinner } from "@decky/ui";
import { StatusDot } from "./StatusDot";
import { StatusNote } from "./StatusNote";
import { t } from "../i18n";
import { eventText, statusText, styleLabel, styleOptions } from "../lib";
import { GAP, MUTED } from "../theme";
import type { ButtonGlyphsModel } from "../hooks";
import type { Game, Style } from "../model";

/** Red dot + the short cause (the dot carries the tone; the text stays the panel's own colour). */
function ErrorText({ text }: { text: string }) {
  return (
    <span style={{ display: "flex", alignItems: "flex-start", gap: 6 }}>
      <span style={{ flex: "none", paddingTop: 3 }}>
        <StatusDot kind="error" size={8} />
      </span>
      <span style={{ minWidth: 0, overflowWrap: "anywhere" }}>{text}</span>
    </span>
  );
}

function Supported({ game, model }: { game: Game; model: ButtonGlyphsModel }) {
  const pending: Style | undefined = model.working[game.appid];
  const error = model.errors[game.appid];
  const style: Style = pending ?? game.style ?? "original";

  const description = pending ? (
    <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <Spinner width={12} height={12} />
      {pending === "original" ? t("apply.restoring") : t("apply.progress", { style: styleLabel(pending) })}
    </span>
  ) : error ? (
    <ErrorText text={t("apply.failed", { detail: error })} />
  ) : game.state === "error" ? (
    <ErrorText text={statusText(game)} />
  ) : (
    <span>{statusText(game)}</span>
  );

  const event = game.last_event ? eventText(game.last_event) : "";
  const lastChange = event && game.buildid ? t("current.lastChange", { event, build: game.buildid }) : event;

  return (
    <>
      <PanelSectionRow>
        <DropdownItem
          label={t("style.label")}
          description={description}
          rgOptions={styleOptions()}
          selectedOption={style}
          disabled={Boolean(pending)}
          onChange={(option) => void model.chooseStyle(game, option.data as Style)}
        />
      </PanelSectionRow>
      {game.support !== "full" && game.state !== "error" ? (
        <PanelSectionRow>
          <StatusNote kind="partial">{t("current.partialNote")}</StatusNote>
        </PanelSectionRow>
      ) : null}
      <PanelSectionRow>
        <div style={{ fontSize: "0.72em", color: MUTED, display: "flex", flexDirection: "column", gap: GAP.tight }}>
          {lastChange ? <span>{lastChange}</span> : null}
          <span>{t("current.keepNote")}</span>
        </div>
      </PanelSectionRow>
    </>
  );
}

/** Rows under the game select: the style select with its status, the last change, or the not-supported card. */
export function CurrentGame({ model }: { model: ButtonGlyphsModel }) {
  const { selected } = model;
  if (!selected) return null;
  if (selected.supported) return <Supported game={selected} model={model} />;
  if (selected.support === "ingame") {
    return (
      <PanelSectionRow>
        <StatusNote kind="ingame" title={t("current.ingameTitle")}>
          {[t("current.ingameLine"), selected.how].filter(Boolean).join(" ")}
        </StatusNote>
      </PanelSectionRow>
    );
  }
  return (
    <PanelSectionRow>
      <StatusNote kind="none" title={t("current.unsupportedTitle")}>
        {t("current.unsupportedLine1")}
      </StatusNote>
    </PanelSectionRow>
  );
}
