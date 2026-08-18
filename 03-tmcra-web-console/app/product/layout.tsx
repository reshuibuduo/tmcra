import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Product | TMCRA",
  description: "TMCRA keeps project state, user requirements, Agent progress, and source evidence available across conversations.",
};

export default function ProductLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return children;
}
