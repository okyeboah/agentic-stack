import { interpolate, useCurrentFrame } from "remotion";
import { COLORS } from "../tokens";

const LINES: Array<{ text: string; color?: string }> = [
  { text: "$ brew tap codejunkie99/agentic-stack \\" },
  { text: "    https://github.com/codejunkie99/agentic-stack", color: COLORS.muted },
  { text: "$ brew install agentic-stack" },
  { text: "" },
  { text: "$ cd your-project" },
  { text: "$ agentic-stack claude-code" },
  { text: "" },
  { text: "✓  .agent/ installed — edit PREFERENCES.md to begin", color: COLORS.green },
];

const SPEED = 1.8; // chars per frame

export const Quickstart: React.FC = () => {
  const frame = useCurrentFrame();
  const panelOpacity = interpolate(frame, [0, 20], [0, 1], { extrapolateRight: "clamp" });

  let budget = Math.floor(frame * SPEED);

  return (
    <div style={{
      background: COLORS.bg, width: "100%", height: "100%",
      display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
      fontFamily: "'Courier New', monospace", opacity: panelOpacity,
    }}>
      <div style={{ color: COLORS.muted, fontSize: 13, marginBottom: 14, letterSpacing: 3 }}>
        QUICKSTART
      </div>
      <div style={{
        background: COLORS.terminal, border: "1px solid #1f2937",
        borderRadius: 10, padding: "28px 44px", minWidth: 660,
        boxShadow: "0 0 40px rgba(124,58,237,0.15)",
      }}>
        {LINES.map((line, i) => {
          if (!line.text) { budget -= 0; return <div key={i} style={{ height: 14 }} />; }
          const visible = Math.min(line.text.length, Math.max(0, budget));
          budget -= line.text.length;
          const cursor = visible < line.text.length && visible > 0;
          return (
            <div key={i} style={{ color: line.color ?? COLORS.text, fontSize: 17, lineHeight: 1.9 }}>
              {line.text.slice(0, visible)}
              {cursor && (
                <span style={{ opacity: Math.sin(frame * 0.35) > 0 ? 1 : 0, color: COLORS.accentLight }}>
                  ▋
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};
