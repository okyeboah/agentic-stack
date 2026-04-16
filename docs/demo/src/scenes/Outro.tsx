import { interpolate, useCurrentFrame } from "remotion";
import { COLORS } from "../tokens";

export const Outro: React.FC = () => {
  const frame = useCurrentFrame();

  const fadeIn = interpolate(frame, [0, 35], [0, 1], { extrapolateRight: "clamp" });
  const fadeOut = interpolate(frame, [120, 180], [1, 0], { extrapolateLeft: "clamp" });
  const glowOpacity = interpolate(frame, [20, 60], [0, 0.4], { extrapolateRight: "clamp" });

  return (
    <div style={{
      background: COLORS.bg, width: "100%", height: "100%",
      display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
      fontFamily: "'Courier New', monospace", opacity: fadeIn * fadeOut,
    }}>
      <div style={{
        position: "absolute", width: 500, height: 500, borderRadius: "50%",
        background: `radial-gradient(circle, rgba(124,58,237,${glowOpacity}) 0%, transparent 70%)`,
        pointerEvents: "none",
      }} />

      <div style={{ fontSize: 52, fontWeight: 700, color: COLORS.text, marginBottom: 20, textAlign: "center" }}>
        One brain,{" "}
        <span style={{ color: COLORS.accentLight }}>many harnesses.</span>
      </div>

      <div style={{ fontSize: 20, color: COLORS.muted, marginBottom: 28 }}>
        github.com/codejunkie99/agentic-stack
      </div>

      <div style={{ fontSize: 14, color: "#475569", letterSpacing: 3 }}>
        v0.3.0 · MIT
      </div>
    </div>
  );
};
