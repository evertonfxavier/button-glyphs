import { staticClasses } from "@decky/ui";
import { definePlugin } from "@decky/api";
import { FaGamepad } from "react-icons/fa";
import { GlyphsPanel } from "./components";

export default definePlugin(() => ({
  name: "Button Glyphs",
  titleView: <div className={staticClasses.Title}>Button Glyphs</div>,
  content: <GlyphsPanel />,
  icon: <FaGamepad />,
  onDismount() {
    console.log("Button Glyphs unloading");
  },
}));
