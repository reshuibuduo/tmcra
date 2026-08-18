import type { Metadata } from "next";
import "./docs.css";

export const metadata: Metadata = {
  title: "TMCRA API Documentation | TMCRA API 文档",
  description: "Bilingual production documentation for the TMCRA Memory API.",
};

export default function DocsLayout({ children }: { children: React.ReactNode }) {
  return children;
}
