import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Architecture | TMCRA",
  description: "The scope, retrieval, actor-provenance, and evidence composition architecture behind TMCRA continuity.",
};

export default function ArchitectureLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return children;
}
