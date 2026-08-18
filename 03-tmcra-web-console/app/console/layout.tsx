import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./console.css";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "TMCRA Console",
  description: "Operate and govern TMCRA agent memory infrastructure.",
  robots: { index: false, follow: false },
};

export default function ConsoleLayout({ children }: { children: ReactNode }) {
  return children;
}
