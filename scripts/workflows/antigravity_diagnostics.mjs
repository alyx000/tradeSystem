import fs from "node:fs";

export const GLOBAL_FAILURE_REASONS = new Set(["quota_exhausted", "auth_required", "startup_failed"]);

export function diagnoseAntigravityFailure({
  stdout = "",
  stderr = "",
  logFile = "",
  reason = "antigravity_failed",
  returncode = null,
} = {}) {
  const logText = readTextIfExists(logFile);
  const combined = [stdout, stderr, logText].filter(Boolean).join("\n");
  const detected = classifyAntigravityFailure(combined, reason);
  const diagnostics = {
    reason: detected,
    message: diagnosticMessage(combined) || detected,
    stdout_empty: !String(stdout || "").trim(),
    stderr_empty: !String(stderr || "").trim(),
  };
  if (logFile) diagnostics.log_file = logFile;
  if (returncode !== null && returncode !== undefined) diagnostics.returncode = returncode;
  return diagnostics;
}

export function classifyAntigravityFailure(text, fallback = "antigravity_failed") {
  const lower = String(text || "").toLowerCase();
  if (lower.includes("resource_exhausted") || lower.includes("code 429") || lower.includes("quota")) {
    return "quota_exhausted";
  }
  if (
    lower.includes("not logged in")
    || lower.includes("not authenticated")
    || lower.includes("unauthenticated")
    || lower.includes("opening authentication page")
    || lower.includes("authorization code")
  ) {
    return "auth_required";
  }
  if (fallback === "startup_failed") return "startup_failed";
  if (lower.includes("timeout")) return "timeout";
  return fallback || "antigravity_failed";
}

export function diagnosticMessage(text) {
  const lines = String(text || "").split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const preferred = ["resource_exhausted", "code 429", "quota", "not logged in", "not authenticated", "authentication", "timeout"];
  for (const line of lines) {
    const lower = line.toLowerCase();
    if (preferred.some((term) => lower.includes(term))) {
      return line.slice(0, 500);
    }
  }
  return lines.length ? lines[0].slice(0, 500) : "";
}

export function readTextIfExists(file) {
  if (!file) return "";
  try {
    if (!fs.existsSync(file)) return "";
    return fs.readFileSync(file, "utf-8").slice(0, 4000);
  } catch (_) {
    return "";
  }
}

export function sanitizeDiagnostics(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  const out = {};
  for (const key of ["reason", "message", "log_file", "returncode", "stdout_empty", "stderr_empty"]) {
    if (value[key] !== undefined && value[key] !== null && value[key] !== "") out[key] = value[key];
  }
  return out;
}

export function isGlobalLlmFailure(reason) {
  return GLOBAL_FAILURE_REASONS.has(String(reason || ""));
}
