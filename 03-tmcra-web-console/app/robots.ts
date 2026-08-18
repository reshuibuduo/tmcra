import type { MetadataRoute } from "next";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      allow: "/",
      disallow: [
        "/api/",
        "/console/",
        "/personal",
        "/enterprise",
        "/internal",
        "/account-setup",
        "/account-suspended",
        "/visual-atlas-preview",
      ],
    },
    sitemap: "https://tmcra.com/sitemap.xml",
  };
}
