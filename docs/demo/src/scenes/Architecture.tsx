import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { COLORS } from "../tokens";

const HARNESSES = ["Claude Code", "Cursor", "Windsurf", "OpenCode", "OpenClient", "Hermes", "Python"];
const LAYERS = ["memory/", "skills/", "protocols/"];

const HarnessItem: React.FC<{ label: string; delay: number }> = ({ label, delay }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const sp = spring({ fps, frame: Math.max(0, frame - delay), config: { damping: 12 } });
  const op = interpolate(frame, [delay, delay + 18], [0, 1], { extrapolateRight: "clamp" });
  return (
    <div style={{
      background: COLORS.harness, border: "1px solid #334155", borderRadius: 6,
      padding: "7px 18px", color: COLORS.harnessText, fontSize: 15,
      opacity: op, transform: `translateX(${(1 - sp) * -50}px)`,
    }}>
      {label}
    </div>
  );
};

export const Architecture: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const brainSp = spring({ fps, frame, config: { damping: 10, stiffness: 70 } });

  return (
    <div style={{
      background: COLORS.bg, width: "100%", height: "100%",
      display: "flex", alignItems: "center", justifyContent: "center",
      fontFamily: "'Courier New', monospace", gap: 60,
    }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
        {HARNESSES.map((h, i) => <HarnessItem key={h} label={h} delay={i * 14} />)}
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 7, paddingTop: 2 }}>
        {HARNESSES.map((_, i) => {
          const op = interpolate(frame, [i * 14 + 25, i * 14 + 45], [0, 1], { extrapolateRight: "clamp" });
          return <div key={i} style={{ color: COLORS.accent, fontSize: 22, opacity: op, lineHeight: "31px" }}>→</div>;
        })}
      </div>

      <div style={{
        background: COLORS.brain, border: `2px solid ${COLORS.border}`, borderRadius: 14,
        padding: "36px 52px", textAlign: "center",
        opacity: brainSp, transform: `scale(${interpolate(brainSp, [0, 1], [0.6, 1])})`,
      }}>
        <div style={{ fontSize: 26, fontWeight: 700, color: COLORS.accentLight, marginBottom: 22 }}>
          .agent/
        </div>
        {LAYERS.map((l, i) => {
          const op = interpolate(frame, [55 + i * 22, 75 + i * 22], [0, 1], { extrapolateRight: "clamp" });
          return (
            <div key={l} style={{ color: COLORS.muted, fontSize: 17, marginTop: 10, opacity: op }}>
              {l}
            </div>
          );
        })}
      </div>
    </div>
  );
};
