import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "TMCRA",
    short_name: "TMCRA",
    description: "Long-term memory that lets AI agents continue work across conversations.",
    start_url: "/",
    display: "standalone",
    background_color: "#ECEAE5",
    theme_color: "#ECEAE5",
    icons: [
      {
        src: "/brand/tmcra-app-icon.png",
        sizes: "512x512",
        type: "image/png",
      },
    ],
  };
}
