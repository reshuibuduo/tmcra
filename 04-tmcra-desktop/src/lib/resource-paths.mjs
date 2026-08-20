import { join } from "node:path";

function safeResourceName(name) {
  if (
    typeof name !== "string"
    || !/^[A-Za-z0-9_.-]+$/u.test(name)
    || name === "."
    || name === ".."
  ) {
    throw new TypeError("TMCRA resource name must be a plain filename.");
  }
  return name;
}

export function resolveProductResourcePath({ isPackaged, resourcesPath, sourceRoot }, name) {
  const filename = safeResourceName(name);
  return isPackaged
    ? join(resourcesPath, filename)
    : join(sourceRoot, "resources", filename);
}

export function resolveProductScriptPath({ isPackaged, resourcesPath, sourceRoot }, name) {
  const filename = safeResourceName(name);
  return isPackaged
    ? join(resourcesPath, "desktop-scripts", filename)
    : join(sourceRoot, "scripts", filename);
}
