import { redirect } from "next/navigation";

import { resolveAccountRouting } from "@/db/console";
import { requireChatGPTUser } from "../../../chatgpt-auth";
import DeviceAuthorizationClient from "../codex/CodexDeviceAuthorizationClient";
import "../codex/connect-codex.css";

export const dynamic = "force-dynamic";

export default function DeepSeekHarnessConnectPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  return <AuthenticatedDeepSeekHarnessConnect searchParams={searchParams} />;
}

async function AuthenticatedDeepSeekHarnessConnect({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const userCode = normalizeCode(
    typeof params.user_code === "string" ? params.user_code : "",
  );
  const returnTo = `/console/connect/deepseek-harness${
    userCode ? `?user_code=${encodeURIComponent(userCode)}` : ""
  }`;
  const user = await requireChatGPTUser(returnTo);
  const routing = await resolveAccountRouting({
    email: user.email,
    displayName: user.displayName,
    fullName: user.fullName,
  });
  if (routing.destination === "/account-setup" || !routing.hasPersonalSpace) {
    redirect(`/account-setup?return_to=${encodeURIComponent(returnTo)}`);
  }
  if (routing.destination !== "/personal") redirect(routing.destination);

  return (
    <DeviceAuthorizationClient
      email={user.email}
      initialUserCode={userCode}
      provider="deepseek_harness"
      providerLabel="DeepSeek Harness"
      connectPath="/console/connect/deepseek-harness"
    />
  );
}

function normalizeCode(value: string) {
  const code = value.toUpperCase().replace(/[\s-]+/g, "");
  return /^[A-HJ-NP-Z2-9]{8}$/.test(code) ? code : "";
}
