import type { Metadata } from "next";
import type { ReactNode } from "react";
import { requireChatGPTUser } from "../chatgpt-auth";
import "./internal.css";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "TMCRA Internal",
  description: "Internal platform operations for the TMCRA team.",
  robots: { index: false, follow: false, nocache: true },
};

export default async function InternalLayout({ children }: { children: ReactNode }) {
  await requireChatGPTUser("/internal");
  return children;
}
