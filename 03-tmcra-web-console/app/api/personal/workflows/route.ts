import { ConsoleError } from "@/db/console";
import {
  PERSONAL_NO_STORE_HEADERS, personalErrorResponse, personalMemoryBinding,
  personalMemoryRequest, requirePersonalAccess,
} from "../export/server";
import { workflowHandler } from "./handler.mjs";

export const dynamic = "force-dynamic";

const proxy = workflowHandler({
  requirePersonalAccess, personalMemoryBinding, personalMemoryRequest, personalErrorResponse,
  noStoreHeaders: PERSONAL_NO_STORE_HEADERS,
  contractError: (status: number, code: string, message: string) => new ConsoleError(status, code, message),
});

export async function GET(request: Request) { return proxy(request, false); }
export async function POST(request: Request) { return proxy(request, true); }
