import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Benchmarks | TMCRA",
  description: "TMCRA's recorded LongMemEval result, category breakdown, recall timing, and reproduction entry points.",
};

export default function BenchmarksLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return children;
}
