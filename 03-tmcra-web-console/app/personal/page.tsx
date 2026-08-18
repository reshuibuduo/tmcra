import { redirect } from "next/navigation";
import { resolveAccountRouting, resolvePersonalMemoryAccess } from "@/db/console";
import { requireChatGPTUser } from "../chatgpt-auth";
import PersonalConsoleClient from "./PersonalConsoleClient";

export const dynamic = "force-dynamic";

export default async function PersonalPage() {
  const user = await requireChatGPTUser("/personal");
  const identity = {
    email: user.email,
    displayName: user.displayName,
    fullName: user.fullName,
  };
  const routing = await resolveAccountRouting(identity);
  if (routing.destination !== "/personal") redirect(routing.destination);
  if (!routing.hasPersonalSpace) redirect("/account-setup?reason=personal-provisioning");
  const access = await resolvePersonalMemoryAccess(identity);

  return (
    <PersonalConsoleClient
      initialActor={access.actor}
      initialSpace={access.space}
    />
  );
}
