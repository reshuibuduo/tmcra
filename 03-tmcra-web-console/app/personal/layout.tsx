import type { Metadata } from "next";
import type { ReactNode } from "react";
import { requireChatGPTUser } from "../chatgpt-auth";
import "../console/console.css";
import "./personal.css";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "TMCRA Personal Memory",
  description: "Inspect and govern your personal AI memory.",
  robots: { index: false, follow: false },
};

export default async function PersonalLayout({ children }: { children: ReactNode }) {
  await requireChatGPTUser("/personal");
  return children;
}
