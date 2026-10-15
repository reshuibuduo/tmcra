import { redirect } from "next/navigation";
import { resolveAccountRouting, resolvePersonalMemoryAccess } from "@/db/console";
import { requireChatGPTUser } from "../../chatgpt-auth";

export const dynamic = "force-dynamic";

export default async function WorkflowsPage() {
  const user = await requireChatGPTUser("/personal/workflows");
  const identity = { email: user.email, displayName: user.displayName, fullName: user.fullName };
  const routing = await resolveAccountRouting(identity);
  if (routing.destination !== "/personal") redirect(routing.destination);
  if (!routing.hasPersonalSpace) redirect("/account-setup?reason=personal-provisioning");
  await resolvePersonalMemoryAccess(identity);
  return <main style={{ minHeight: "100dvh", background: "#f7f8fb" }}>
    <iframe title="TMCRA 项目接续与记忆工作台" src="/workflows/workflow-center.html?mode=personal"
      style={{ border: 0, width: "100%", minHeight: "100dvh" }} />
  </main>;
}
