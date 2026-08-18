import { redirect } from "next/navigation";
import { resolveAccountRouting } from "@/db/console";
import { chatGPTSignOutPath, requireChatGPTUser } from "../chatgpt-auth";
import ConsoleHomeClient from "./ConsoleHomeClient";

export const dynamic = "force-dynamic";

export default async function ConsolePage() {
  const user = await requireChatGPTUser("/console");
  const routing = await resolveAccountRouting({
    email: user.email,
    displayName: user.displayName,
    fullName: user.fullName,
  });
  if (routing.status === "suspended") redirect("/account-suspended");

  return (
    <ConsoleHomeClient
      actor={{
        displayName: user.displayName,
        email: user.email,
      }}
      account={{
        type: routing.accountType,
        hasPersonalSpace: routing.hasPersonalSpace,
        hasEnterpriseMembership: routing.hasEnterpriseMembership,
      }}
      signOutPath={chatGPTSignOutPath("/")}
    />
  );
}
