import { WorkflowContractError, readWorkflowBody, requireWorkflowOrigin, workflowPath } from "./contract.mjs";

// The account binding is a prefix, not a project scope. Resolve an opaque UI
// selection against a freshly authorized catalog on every request.
export async function workflowProjects(prefix, catalog) {
  workflowPath(prefix);
  if (!Array.isArray(catalog)) throw new WorkflowContractError(502, "invalid_workflow_catalog", "Project catalog is unavailable.");
  const globalScope = `${prefix}-global`, projectPrefix = `${prefix}-project-`;
  const scopes = [...new Set(catalog.map((row) => row?.scope_name).filter((name) =>
    typeof name === "string" && name.startsWith(projectPrefix) && /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/u.test(name)))].sort();
  const projects = [{ id: "global", label: "全局记忆", scope: globalScope }];
  workflowPath(globalScope);
  for (const scope of scopes) {
    const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(scope)));
    const key = [...digest].map((byte) => byte.toString(16).padStart(2, "0")).join("").slice(0, 32);
    projects.push({ id: `p-${key}`, label: scope.slice(projectPrefix.length).replace(/-[a-f0-9]{16}$/u, "") || "项目", scope });
  }
  return projects;
}

function selectedProject(request) {
  const params = new URL(request.url).searchParams;
  const keys = [...params.keys()];
  const key = params.get("project") || "global";
  if (keys.some((item) => item !== "project") || keys.length > 1 || !/^(global|p-[a-f0-9]{32})$/u.test(key)) {
    throw new WorkflowContractError(422, "invalid_workflow_query", "Choose a project from the authenticated catalog.");
  }
  return key;
}

// Dependency-injected boundary permits executable authorization/CSRF regression tests.
// Production dependencies are supplied only by route.ts, never by a browser payload.
export function workflowHandler(deps) {
  return async (request, mutation) => {
    const requestId = crypto.randomUUID();
    try {
      if (mutation) requireWorkflowOrigin(request);
      const access = await deps.requirePersonalAccess();
      const selection = selectedProject(request);
      const body = mutation ? await readWorkflowBody(request) : undefined;
      const binding = deps.personalMemoryBinding();
      const headers = { "X-TMCRA-On-Behalf-Of-Subject": access.space.id };
      const catalog = await deps.personalMemoryRequest(binding,
        `/v1/scopes?prefix=${encodeURIComponent(`${access.space.scopeName}-`)}&limit=1000`,
        { method: "GET", headers }, requestId);
      const projects = await workflowProjects(access.space.scopeName, catalog);
      const selected = projects.find((item) => item.id === selection);
      if (!selected) throw new WorkflowContractError(403, "workflow_project_forbidden", "This project is not available in your personal space.");
      const result = await deps.personalMemoryRequest(
        binding, workflowPath(selected.scope),
        {
          method: mutation ? "POST" : "GET",
          headers: {
            ...headers,
            ...(mutation ? { "Content-Type": "application/json" } : {}),
          },
          body: mutation ? JSON.stringify(body) : undefined,
        }, requestId,
      );
      return Response.json(mutation ? result : {
        ...result,
        workflow_projects: projects.map(({ id, label }) => ({ id, label })),
        workflow_project: { id: selected.id, label: selected.label },
      }, { headers: { ...deps.noStoreHeaders, "X-Request-ID": requestId } });
    } catch (error) {
      return deps.personalErrorResponse(error instanceof WorkflowContractError
        ? deps.contractError(error.status, error.code, error.message) : error, requestId);
    }
  };
}
