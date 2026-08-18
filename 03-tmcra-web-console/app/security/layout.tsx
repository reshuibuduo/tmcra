import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Security | TMCRA",
  description: "Implemented identity, credential, tenant, scope, browser, and operations controls in the current TMCRA product.",
};

export default function SecurityLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return children;
}
