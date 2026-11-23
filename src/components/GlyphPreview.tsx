import { GLYPH_PREVIEW } from "../generated/glyphs";
import { MUTED } from "../theme";
import type { Style } from "../model";

export function GlyphPreview({
  style,
  roles,
  originalHint,
}: {
  style: Style;
  roles: string[];
  originalHint?: string;
}) {
  const icons = GLYPH_PREVIEW[style];
  if (!icons) {
    return (
      <div style={{ fontSize: "0.78em", color: MUTED }}>
        {originalHint || "The game's own icons."}
      </div>
    );
  }
  return (
    <div style={{ display: "flex", justifyContent: "space-between", width: "100%", gap: 6 }}>
      {icons.map((src, i) => (
        <div
          key={i}
          style={{ display: "flex", flexDirection: "column", alignItems: "center", flex: 1, gap: 2 }}
        >
          <img src={src} width={28} height={28} alt="" />
          <span style={{ fontSize: "0.62em", color: MUTED, textAlign: "center" }}>
            {roles[i] ?? ""}
          </span>
        </div>
      ))}
    </div>
  );
}
