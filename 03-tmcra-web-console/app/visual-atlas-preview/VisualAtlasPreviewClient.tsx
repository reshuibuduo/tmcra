"use client";

import VisualMemoryAtlas from "../personal/VisualMemoryAtlas";
import {
  normalizeVisualAtlas,
  type RawVisualAtlas,
} from "../personal/VisualMemoryAtlasExplorer";
import fixture from "./atlas.fixture.json";

export default function VisualAtlasPreviewClient() {
  return (
    <main style={{ minHeight: "100vh", background: "#071013", padding: 16 }}>
      <VisualMemoryAtlas data={normalizeVisualAtlas(fixture as RawVisualAtlas)} />
    </main>
  );
}
