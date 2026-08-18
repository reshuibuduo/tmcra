import type { NextConfig } from "next";

const internalSecurityHeaders = [
  { key: "Cache-Control", value: "private, no-store, max-age=0" },
  { key: "Pragma", value: "no-cache" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "no-referrer" },
  { key: "X-Robots-Tag", value: "noindex, nofollow, noarchive, nosnippet" },
  {
    key: "Permissions-Policy",
    value: "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
  },
  {
    key: "Content-Security-Policy",
    value:
      "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
  },
] as const;

const nextConfig: NextConfig = {
  async headers() {
    return [
      { source: "/internal", headers: [...internalSecurityHeaders] },
      { source: "/internal/:path*", headers: [...internalSecurityHeaders] },
      { source: "/api/internal", headers: [...internalSecurityHeaders] },
      { source: "/api/internal/:path*", headers: [...internalSecurityHeaders] },
    ];
  },
};

export default nextConfig;
