import { chatGPTSignOutPath, requireChatGPTUser } from "../chatgpt-auth";
import AccountSuspendedClient from "./AccountSuspendedClient";
import "../account-shell.css";

export const dynamic = "force-dynamic";

export default async function AccountSuspendedPage() {
  await requireChatGPTUser("/account-suspended");
  return <AccountSuspendedClient signOutPath={chatGPTSignOutPath("/")} />;
}
