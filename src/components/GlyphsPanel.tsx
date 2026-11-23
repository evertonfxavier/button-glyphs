import { useButtonGlyphs } from "../hooks";
import { DetailsSection } from "./DetailsSection";
import { GamesSection } from "./GamesSection";
import { LibrarySection } from "./LibrarySection";
import { StatusBanner } from "./StatusBanner";

export function GlyphsPanel() {
  const model = useButtonGlyphs();
  return (
    <>
      <StatusBanner model={model} />
      <GamesSection model={model} />
      <LibrarySection model={model} />
      <DetailsSection model={model} />
    </>
  );
}
