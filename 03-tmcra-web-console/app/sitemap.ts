import type { MetadataRoute } from "next";

const publicRoutes = [
  "",
  "/product",
  "/architecture",
  "/benchmarks",
  "/developers",
  "/developers/automatic-memory",
  "/developers/codex",
  "/docs",
  "/download",
  "/pricing",
  "/security",
  "/access",
  "/privacy",
  "/terms",
];

export default function sitemap(): MetadataRoute.Sitemap {
  const origin = "https://tmcra.com";
  return publicRoutes.map((route) => ({
    url: `${origin}${route}`,
    lastModified: new Date("2026-08-12T00:00:00+08:00"),
    changeFrequency: route === "" ? "weekly" : "monthly",
    priority: route === "" ? 1 : route === "/product" || route === "/developers" ? 0.8 : 0.6,
  }));
}
