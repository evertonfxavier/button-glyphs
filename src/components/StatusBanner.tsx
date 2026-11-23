import { PanelSection, PanelSectionRow } from "@decky/ui";
import { StatusCard } from "./StatusCard";
import type { ButtonGlyphsModel } from "../hooks";

export function StatusBanner({ model }: { model: ButtonGlyphsModel }) {
  const { loaded, listError, supported, games } = model;

  const card = (() => {
    if (!loaded) return <StatusCard tone="info" title="Scanning your library…" lines={[]} busy />;
    if (listError) return <StatusCard tone="danger" title="Something went wrong" lines={[listError]} />;
    if (!supported.length) {
      return (
        <StatusCard
          tone="info"
          title="No supported game installed"
          lines={[`${games.length} game(s) found. Supported so far: Assassin's Creed.`]}
        />
      );
    }
    const problems = supported.filter((g) => g.state === "error");
    if (problems.length) {
      return (
        <StatusCard
          tone="danger"
          title="Needs attention"
          lines={problems.map((g) => `${g.name}: ${g.message}`)}
        />
      );
    }
    const waiting = supported.filter((g) => g.state && g.state !== "ok");
    if (waiting.length) {
      return (
        <StatusCard
          tone="warn"
          title="Waiting"
          lines={waiting.map((g) => `${g.name}: ${g.message}`)}
        />
      );
    }
    return (
      <StatusCard
        tone="ok"
        title="All set"
        lines={[`${supported.length} supported game(s) installed`]}
      />
    );
  })();

  return (
    <PanelSection>
      <PanelSectionRow>{card}</PanelSectionRow>
    </PanelSection>
  );
}
