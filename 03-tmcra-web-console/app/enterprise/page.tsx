import { redirect } from "next/navigation";
import { resolveAccountRouting } from "@/db/console";
import { requireChatGPTUser } from "../chatgpt-auth";
import ConsoleClient from "../console/ConsoleClient";

export const dynamic = "force-dynamic";

export default async function EnterprisePage() {
  const user = await requireChatGPTUser("/enterprise");
  const routing = await resolveAccountRouting({
    email: user.email,
    displayName: user.displayName,
    fullName: user.fullName,
  });
  if (routing.destination !== "/enterprise") redirect(routing.destination);

  return (
    <ConsoleClient
      apiBase="/api/enterprise"
      initialActor={{
        displayName: user.displayName,
        email: user.email,
        role: "Enterprise operator",
      }}
    />
  );
}
