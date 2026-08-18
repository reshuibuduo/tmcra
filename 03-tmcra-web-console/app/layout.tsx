import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { headers } from "next/headers";
import { LanguageProvider } from "./i18n";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

const title = "TMCRA — Continue work across conversations";
const description = "Persistent memory infrastructure that lets AI agents recover project state, user requirements, and traceable evidence across conversations.";

export async function generateMetadata(): Promise<Metadata> {
  const requestHeaders = await headers();
  const forwardedHost = requestHeaders.get("x-forwarded-host")?.split(",")[0]?.trim();
  const host = forwardedHost ?? requestHeaders.get("host")?.split(",")[0]?.trim();
  const forwardedProtocol = requestHeaders.get("x-forwarded-proto")?.split(",")[0]?.trim();
  const protocol = forwardedProtocol ?? (host?.startsWith("localhost") || host?.startsWith("127.0.0.1") ? "http" : "https");
  const fallbackOrigin = "https://tmcra.com";

  let origin = fallbackOrigin;
  if (host) {
    try {
      origin = new URL(`${protocol}://${host}`).origin;
    } catch {
      origin = fallbackOrigin;
    }
  }

  const socialImage = `${origin}/og-continuity-v2.png`;

  return {
    title,
    description,
    metadataBase: new URL(origin),
    applicationName: "TMCRA",
    category: "technology",
    keywords: ["agent memory", "persistent AI agents", "temporal memory graph", "TMCRA"],
    icons: {
      icon: "/brand/tmcra-app-icon.png",
      shortcut: "/brand/tmcra-app-icon.png",
      apple: "/brand/tmcra-app-icon.png",
    },
    manifest: "/manifest.webmanifest",
    openGraph: {
      title,
      description,
      type: "website",
      siteName: "TMCRA",
      images: [{
        url: socialImage,
        width: 1728,
        height: 910,
        alt: "Illustrative TMCRA continuity flow with distinct USER and AGENT evidence",
      }],
    },
    twitter: {
      card: "summary_large_image",
      title,
      description,
      images: [socialImage],
    },
  };
}

export default async function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const requestHeaders = await headers();
  const initialLanguage = requestHeaders.get("accept-language")?.toLowerCase().trim().startsWith("zh") ? "zh" : "en";

  return (
    <html lang={initialLanguage === "zh" ? "zh-CN" : "en"} suppressHydrationWarning>
      <body className={`${geistSans.variable} ${geistMono.variable}`}><LanguageProvider initialLanguage={initialLanguage}>{children}</LanguageProvider></body>
    </html>
  );
}
