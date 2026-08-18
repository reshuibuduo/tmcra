import type { Metadata } from "next";
import type { ReactNode } from "react";
import { requireChatGPTUser } from "../chatgpt-auth";
import "../console/console.css";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "TMCRA Enterprise Console",
  description: "Enterprise memory infrastructure, access, usage, and governance.",
  robots: { index: false, follow: false },
};

export default async function EnterpriseLayout({ children }: { children: ReactNode }) {
  await requireChatGPTUser("/enterprise");
  return children;
}
