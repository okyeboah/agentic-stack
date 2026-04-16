import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { COLORS } from "../tokens";

export const Title: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const titleSpring = spring({ fps, frame, config: { damping: 14, stiffness: 80 } });
  const titleOpacity = interpolate(frame, [0, 20], [0, 1], { extrapolateRight: "clamp" });
  const tagOpacity = interpolate(frame, [45, 75], [0, 1], { extrapolateRight: "clamp" });
  const subOpacity = interpolate(frame, [75, 105], [0, 1], { extrapolateRight: "clamp" });

  return (
    <div
      style={{
        background: COLORS.bg,
        width: "100%",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        fontFamily: "'Courier New', monospace",
      }}
    >
      <div
        style={{
          fontSize: 88,
          fontWeight: 700,
          color: COLORS.text,
          letterSpacing: -2,
          opacity: titleOpacity,
          transform: `translateY(${(1 - titleSpring) * 50}px)`,
        }}
      >
        agentic-stack
      </div>

      <div
        style={{
          fontSize: 30,
          color: COLORS.accentLight,
          marginTop: 24,
          opacity: tagOpacity,
          letterSpacing: 1,
        }}
      >
        One brain, many harnesses.
      </div>

      <div
        style={{
          fontSize: 18,
          color: COLORS.muted,
          marginTop: 14,
          opacity: subOpacity,
          letterSpacing: 2,
        }}
      >
        memory · skills · protocols
      </div>
    </div>
  );
};
