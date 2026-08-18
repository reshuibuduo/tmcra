import { chatGPTSignOutPath, requireChatGPTUser } from "../chatgpt-auth";
import InternalClient from "./InternalClient";

export const dynamic = "force-dynamic";

export default async function InternalPage() {
  const user = await requireChatGPTUser("/internal");

  return (
    <InternalClient
      initialActor={{
        displayName: user.displayName,
        email: user.email,
        role: "Pending verification",
      }}
      signOutPath={chatGPTSignOutPath("/")}
    />
  );
}
