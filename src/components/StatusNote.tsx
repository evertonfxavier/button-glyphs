import type { ReactNode } from "react";
import { StatusDot } from "./StatusDot";
import { GAP, MUTED } from "../theme";
import type { DotKind } from "../lib";

/**
 * A neutral note under a control: no border, no box. The tone is only the small dot (colour + shape, with
 * its label for screen readers). Same inset as the other rows (no horizontal padding of its own) and a clear
 * gap above, so it never sticks to the select it explains.
 */
export function StatusNote({
  kind,
  title,
  children,
}: {
  kind: DotKind;
  title?: string;
  children?: ReactNode;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: GAP.block,
        width: "100%",
        boxSizing: "border-box",
        marginTop: GAP.block + 2,
        marginBottom: GAP.tight,
      }}
    >
      <span style={{ flex: "none", paddingTop: 3 }}>
        <StatusDot kind={kind} size={10} />
      </span>
      <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: GAP.tight }}>
        {title ? <span style={{ fontWeight: 700, fontSize: "0.9em" }}>{title}</span> : null}
        {children ? (
          <span style={{ fontSize: "0.78em", color: MUTED, overflowWrap: "anywhere" }}>{children}</span>
        ) : null}
      </div>
    </div>
  );
}
