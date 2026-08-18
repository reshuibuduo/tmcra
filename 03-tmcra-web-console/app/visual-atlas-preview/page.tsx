import { notFound } from "next/navigation";
import VisualAtlasPreviewClient from "./VisualAtlasPreviewClient";

export default function VisualAtlasPreviewPage() {
  if (
    process.env.NODE_ENV === "production" &&
    process.env.TMCRA_ENABLE_VISUAL_ATLAS_PREVIEW !== "1"
  ) {
    notFound();
  }
  return <VisualAtlasPreviewClient />;
}
