import { chatGPTSignOutPath, requireChatGPTUser } from "../chatgpt-auth";
import AccountSetupClient from "./AccountSetupClient";
import "../account-shell.css";

export const dynamic = "force-dynamic";

export default function AccountSetupPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  return <AuthenticatedAccountSetup searchParams={searchParams} />;
}

async function AuthenticatedAccountSetup({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const returnTo = safeReturnTo(
    typeof params.return_to === "string" ? params.return_to : "/personal",
  );
  const setupPath = `/account-setup?return_to=${encodeURIComponent(returnTo)}`;
  const user = await requireChatGPTUser(setupPath);
  return (
    <AccountSetupClient
      email={user.email}
      returnTo={returnTo}
      signOutPath={chatGPTSignOutPath("/")}
    />
  );
}

function safeReturnTo(value: string) {
  if (!value.startsWith("/") || value.startsWith("//")) return "/personal";
  try {
    const parsed = new URL(value, "https://app.local");
    if (parsed.origin !== "https://app.local") return "/personal";
    if (
      parsed.pathname !== "/personal" &&
      parsed.pathname !== "/console/connect/codex"
    ) {
      return "/personal";
    }
    return `${parsed.pathname}${parsed.search}${parsed.hash}`;
  } catch {
    return "/personal";
  }
}
