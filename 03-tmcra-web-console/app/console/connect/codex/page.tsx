import { redirect } from "next/navigation";

import { resolveAccountRouting } from "@/db/console";
import { requireChatGPTUser } from "../../../chatgpt-auth";
import CodexDeviceAuthorizationClient from "./CodexDeviceAuthorizationClient";
import "./connect-codex.css";

export const dynamic = "force-dynamic";

export default function CodexConnectPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  return <AuthenticatedCodexConnect searchParams={searchParams} />;
}

async function AuthenticatedCodexConnect({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const userCode = normalizeCode(
    typeof params.user_code === "string" ? params.user_code : "",
  );
  const returnTo = `/console/connect/codex${
    userCode ? `?user_code=${encodeURIComponent(userCode)}` : ""
  }`;
  const user = await requireChatGPTUser(returnTo);
  const identity = {
    email: user.email,
    displayName: user.displayName,
    fullName: user.fullName,
  };
  const routing = await resolveAccountRouting(identity);
  if (routing.destination === "/account-setup" || !routing.hasPersonalSpace) {
    redirect(`/account-setup?return_to=${encodeURIComponent(returnTo)}`);
  }
  if (routing.destination !== "/personal") redirect(routing.destination);

  return (
    <CodexDeviceAuthorizationClient
      email={user.email}
      initialUserCode={userCode}
    />
  );
}

function normalizeCode(value: string) {
  const code = value.toUpperCase().replace(/[\s-]+/g, "");
  return /^[A-HJ-NP-Z2-9]{8}$/.test(code) ? code : "";
}
