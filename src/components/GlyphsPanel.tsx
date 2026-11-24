import { useButtonGlyphs, useLanguage } from "../hooks";
import { DetailsSection } from "./DetailsSection";
import { GamesSection } from "./GamesSection";
import { LibrarySection } from "./LibrarySection";
import { StatusLine } from "./StatusLine";

export function GlyphsPanel() {
  useLanguage(); // re-renders the panel once Steam's language is known
  const model = useButtonGlyphs();
  return (
    <>
      <StatusLine model={model} />
      <GamesSection model={model} />
      <LibrarySection model={model} />
      <DetailsSection model={model} />
    </>
  );
}
