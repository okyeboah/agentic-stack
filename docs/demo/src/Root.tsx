import { Composition, Series } from "remotion";
import { Title } from "./scenes/Title";
import { Architecture } from "./scenes/Architecture";
import { Quickstart } from "./scenes/Quickstart";
import { Outro } from "./scenes/Outro";
import { FPS, W, H } from "./tokens";

// Scene durations in frames (30fps)
const T_TITLE = 150;       // 5s
const T_ARCH = 300;        // 10s
const T_QUICK = 270;       // 9s
const T_OUTRO = 180;       // 6s
const TOTAL = T_TITLE + T_ARCH + T_QUICK + T_OUTRO; // 900 = 30s

const Demo: React.FC = () => (
  <Series>
    <Series.Sequence durationInFrames={T_TITLE}>
      <Title />
    </Series.Sequence>
    <Series.Sequence durationInFrames={T_ARCH}>
      <Architecture />
    </Series.Sequence>
    <Series.Sequence durationInFrames={T_QUICK}>
      <Quickstart />
    </Series.Sequence>
    <Series.Sequence durationInFrames={T_OUTRO}>
      <Outro />
    </Series.Sequence>
  </Series>
);

export const RemotionRoot: React.FC = () => (
  <Composition
    id="AgenticStackDemo"
    component={Demo}
    durationInFrames={TOTAL}
    fps={FPS}
    width={W}
    height={H}
  />
);
