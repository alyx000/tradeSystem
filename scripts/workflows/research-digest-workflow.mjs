#!/usr/bin/env node
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  diagnoseAntigravityFailure,
  isGlobalLlmFailure,
  sanitizeDiagnostics,
} from "./antigravity_diagnostics.mjs";

const __filename = fileURLToPath(import.meta.url);
const WORKFLOW_DIR = path.dirname(__filename);
const SCRIPTS_DIR = path.dirname(WORKFLOW_DIR);
const REPO_ROOT = path.dirname(SCRIPTS_DIR);
const HELPER = path.join(WORKFLOW_DIR, "huibo_helper.py");
const LLM_INPUT_MARKER = ".research-digest-workflow";
let fatalCleanupDir = null;
let helperEnvOverrides = {};
let currentInvocationId = "";

async function main() {
  const { command, opts } = parseArgs(process.argv.slice(2));
  if (command !== "daily") {
    throw new Error("用法: node scripts/workflows/research-digest-workflow.mjs daily --date YYYY-MM-DD");
  }

  const date = resolveWorkflowDate(opts.date);
  const runRoot = abs(opts.runRoot || path.join(REPO_ROOT, "data/runs/research-digest"));
  const runDir = path.join(runRoot, date);
  const artifact = (name) => path.join(runDir, name);
  const rawDir = abs(opts.rawDir || process.env.HUIBO_RAW_DIR || path.join(REPO_ROOT, "data/reports/huibo/raw"));
  const summaryDir = abs(opts.summaryDir || process.env.HUIBO_SUMMARY_DIR || path.join(REPO_ROOT, "data/reports/huibo/summaries"));
  helperEnvOverrides = {
    HUIBO_RAW_DIR: rawDir,
    HUIBO_SUMMARY_DIR: summaryDir,
  };
  const llmInputBaseDir = abs(opts.llmInputDir || process.env.HUIBO_LLM_INPUT_DIR || path.join(os.tmpdir(), "huibo-llm-input"));
  const llmInputDir = path.join(llmInputBaseDir, safeFileStem(date));
  fatalCleanupDir = llmInputDir;
  const statePath = artifact("state.json");
  const eventsPath = artifact("events.jsonl");
  const readerDir = artifact("reader");
  const antigravityLogDir = artifact("antigravity-logs");
  const readerCap = intOpt(opts.readerCap, 20);
  const readerConcurrency = intOpt(opts.readerConcurrency || process.env.HUIBO_READER_CONCURRENCY, 4);
  const readerMaxAttempts = intOpt(opts.readerMaxAttempts || process.env.HUIBO_READER_MAX_ATTEMPTS, 2);
  const recommendCap = intOpt(opts.recommendCap, 2);
  const windowDays = intOpt(opts.windowDays, 5);
  const resume = Boolean(opts.resume || opts.retryFailed);
  const retryFailed = Boolean(opts.retryFailed);
  const invocationStartedAt = nowIso();
  const invocationId = newInvocationId(invocationStartedAt);
  currentInvocationId = invocationId;
  helperEnvOverrides.HUIBO_WORKFLOW_INVOCATION_ID = invocationId;

  fs.mkdirSync(readerDir, { recursive: true });
  let state = readJsonIfExists(statePath) || {
    date,
    runDir,
    stages: {},
    reports: {},
    llmStatus: "ok",
    options: {},
    startedAt: nowIso(),
  };
  state.llmStatus = state.llmStatus || "ok";
  if (opts.resetLlmStatus) {
    resetLlmStatus(state, eventsPath);
  }
  state.currentInvocation = {
    id: invocationId,
    startedAt: invocationStartedAt,
    resume,
    retryFailed,
  };
  state.options = {
    readerCap,
    readerConcurrency,
    readerMaxAttempts,
    recommendCap,
    windowDays,
    rawDir,
    summaryDir,
    llmInputBaseDir,
    llmInputDir,
    antigravityLogDir,
  };
  saveState(statePath, state);

  const ctx = { date, runDir, statePath, eventsPath, state, resume, retryFailed, opts };
  if (preflightEnabled(opts) && !isLlmUnavailable(state)) {
    await stage(ctx, "preflight", artifact("preflight.json"), async () => {
      const result = await runAntigravityPreflight({
        timeoutSeconds: intOpt(opts.preflightTimeoutSeconds || process.env.HUIBO_PREFLIGHT_TIMEOUT_SECONDS, 45),
        antigravityLogDir,
      });
      writeJson(artifact("preflight.json"), result);
      if (isGlobalLlmFailure(result.reason)) {
        markLlmUnavailable(state, statePath, eventsPath, result, { stage: "preflight" });
      }
      return result;
    });
  }

  await stage(ctx, "collect", artifact("candidates.json"), async () => {
    return runHelper([
      "collect",
      "--date", date,
      "--window-days", String(windowDays),
      "--mode", opts.huiboMode || "desktop_terminal",
      "--out", artifact("candidates.json"),
      "--texts-out", artifact("texts.json"),
    ]);
  });

  await stage(ctx, "prescreen", artifact("prescreened.json"), async () => {
    return runHelper([
      "prescreen",
      "--candidates", artifact("candidates.json"),
      "--texts", artifact("texts.json"),
      "--reader-cap", String(readerCap),
      "--out", artifact("prescreened.json"),
    ]);
  });

  await stage(ctx, "download", artifact("downloaded.json"), async () => {
    const result = runHelper([
      "download",
      "--prescreened", artifact("prescreened.json"),
      "--raw-dir", rawDir,
      "--out", artifact("downloaded.json"),
    ]);
    const items = readJson(artifact("downloaded.json"));
    for (const item of items) {
      const c = item.candidate;
      state.reports[c.report_id] = {
        ...(state.reports[c.report_id] || {}),
        reportId: c.report_id,
        title: c.title,
        pdfPath: c.pdf_path || "",
        status: c.pdf_path ? "downloaded" : "missing_pdf",
      };
    }
    saveState(statePath, state);
    return result;
  });

  await stage(ctx, "read", null, async () => {
    const items = readJson(artifact("downloaded.json"));
    const jobs = [];
    for (const item of items) {
      const c = item.candidate;
      const reportState = state.reports[c.report_id] || {};
      const readerPath = path.join(readerDir, `${c.report_id}.json`);
      const canSkip = resume && fs.existsSync(readerPath) && reportState.status === "read_done" && !retryFailed;
      const retryThis = retryFailed && reportState.status === "failed";
      if (canSkip || (retryFailed && !retryThis)) {
        event(eventsPath, "report_read_skip", { report_id: c.report_id, title: c.title });
        continue;
      }
      if (retryFailed && retryThis && Number(reportState.attempts || 0) >= readerMaxAttempts) {
        event(eventsPath, "report_read_give_up", {
          report_id: c.report_id,
          title: c.title,
          attempts: Number(reportState.attempts || 0),
          max_attempts: readerMaxAttempts,
        });
        continue;
      }
      if (!c.pdf_path || !fs.existsSync(c.pdf_path)) {
        state.reports[c.report_id] = {
          ...reportState,
          reportId: c.report_id,
          title: c.title,
          status: "missing_pdf",
          lastError: "pdf_missing",
        };
        continue;
      }
      if (isLlmUnavailable(state)) {
        markReportSkippedForLlmUnavailable(item, state, eventsPath, readerPath);
        continue;
      }
      jobs.push({ item, readerPath });
    }
    saveState(statePath, state);
    let pending = jobs;
    let retryCount = 0;
    while (pending.length > 0) {
      const current = pending;
      pending = [];
      await runPool(current, Math.max(1, readerConcurrency), async ({ item, readerPath }) => {
        if (isLlmUnavailable(state)) {
          markReportSkippedForLlmUnavailable(item, state, eventsPath, readerPath);
          saveState(statePath, state);
          return;
        }
        await readOneReport({
          item,
          readerPath,
          state,
          statePath,
          eventsPath,
          llmInputDir,
          antigravityLogDir,
          timeoutSeconds: intOpt(opts.llmTimeoutSeconds || process.env.LLM_TIMEOUT_SECONDS, 240),
        });
      });
      for (const job of current) {
        const c = job.item.candidate;
        const reportState = state.reports[c.report_id] || {};
        const attempts = Number(reportState.attempts || 0);
        if (!isLlmUnavailable(state) && reportState.status === "failed" && attempts < readerMaxAttempts) {
          retryCount += 1;
          event(eventsPath, "report_read_retry", {
            report_id: c.report_id,
            title: c.title,
            attempts,
            max_attempts: readerMaxAttempts,
          });
          pending.push(job);
        }
      }
    }
    return {
      status: "ok",
      scheduled_count: jobs.length,
      retry_count: retryCount,
      reader_dir: readerDir,
      max_attempts: readerMaxAttempts,
      ...readerStats(state),
    };
  });

  await stage(ctx, "finalize", artifact("summary.json"), async () => {
    const result = runHelper([
      "finalize",
      "--date", date,
      "--prescreened", artifact("downloaded.json"),
      "--reader-dir", readerDir,
      "--summary-dir", summaryDir,
      "--markdown-out", artifact("report.md"),
      "--events-path", eventsPath,
      "--recommend-cap", String(recommendCap),
      "--lookback-days", String(windowDays),
      "--antigravity-status", state.llmStatus || "ok",
      "--antigravity-reason", state.llmFailureReason || "",
      "--antigravity-message", state.llmFailureMessage || "",
      "--antigravity-log-file", state.llmFailureLogFile || "",
      ...(opts.noAggregateLlm ? ["--no-llm"] : []),
    ]);
    const summaryPath = path.join(summaryDir, `${date}.json`);
    if (fs.existsSync(summaryPath)) {
      fs.copyFileSync(summaryPath, artifact("summary.json"));
    }
    syncLlmStatusFromFinalizeResult(state, statePath, eventsPath, result);
    return result;
  });

  if (opts.publish) {
    await stage(ctx, "publish", artifact("published.json"), async () => {
      return runHelper([
        "publish",
        "--date", date,
        "--markdown", artifact("report.md"),
        "--huibo-summary", artifact("summary.json"),
        "--out", artifact("published.json"),
        "--out-root", opts.publishOutRoot || path.join(REPO_ROOT, "data/reports/research-digest"),
        ...(opts.publishDryRun ? ["--dry-run"] : []),
        ...(opts.publishNoPush ? ["--no-push"] : []),
        ...(opts.includeBaseDigest ? ["--include-base-digest"] : []),
      ]);
    });
  }

  await stage(ctx, "cleanup", null, async () => {
    const llmCleanup = cleanupLlmInputDir(llmInputDir, Boolean(opts.cleanupDryRun));
    return runHelper([
      "cleanup",
      "--raw-dir", rawDir,
      "--summary-dir", summaryDir,
      "--raw-retention-days", String(intOpt(opts.rawRetentionDays, 30)),
      "--summary-retention-days", String(intOpt(opts.summaryRetentionDays, 180)),
      ...(opts.cleanupDryRun ? ["--dry-run"] : []),
    ], llmCleanup);
  });

  const runReportPath = artifact("run_report.md");
  const summaryPayload = workflowSummary(state, opts);
  try {
    writeRunReport(runReportPath, state, opts);
    event(eventsPath, "workflow_summary", { ...summaryPayload, run_report: runReportPath });
  } catch (err) {
    event(eventsPath, "run_report_error", { error: String(err.message || err), run_report: runReportPath });
    event(eventsPath, "workflow_summary", { ...summaryPayload, run_report: runReportPath, run_report_error: String(err.message || err) });
  }
  console.log(`[workflow] done run_dir=${runDir}`);
  process.exit(0);
}

function parseArgs(argv) {
  const command = argv[0];
  const opts = {};
  for (let i = 1; i < argv.length; i += 1) {
    const token = argv[i];
    if (!token.startsWith("--")) {
      continue;
    }
    const key = camel(token.slice(2));
    const next = argv[i + 1];
    if (!next || next.startsWith("--")) {
      opts[key] = true;
    } else {
      opts[key] = next;
      i += 1;
    }
  }
  return { command, opts };
}

function resolveWorkflowDate(rawDate) {
  if (rawDate === undefined) {
    return resolveDefaultTradeDate(defaultDateInShanghai());
  }
  if (typeof rawDate !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(rawDate)) {
    throw new Error("--date requires YYYY-MM-DD");
  }
  return rawDate;
}

function resolveDefaultTradeDate(today) {
  const result = spawnSync("python3", [HELPER, "resolve-date", "--date", today], {
    cwd: SCRIPTS_DIR,
    encoding: "utf-8",
    env: process.env,
  });
  if (result.status === 0) {
    try {
      const parsed = JSON.parse(result.stdout);
      if (parsed && typeof parsed.date === "string" && /^\d{4}-\d{2}-\d{2}$/.test(parsed.date)) {
        return parsed.date;
      }
    } catch (_) {
      // fall through to local weekday fallback
    }
  } else {
    console.warn(`[workflow] resolve-date helper failed, using weekday fallback: ${result.stderr || result.stdout}`);
  }
  return previousWeekday(today);
}

function previousWeekday(today) {
  const d = new Date(`${today}T00:00:00Z`);
  for (let delta = 1; delta <= 15; delta += 1) {
    const candidate = new Date(d);
    candidate.setUTCDate(candidate.getUTCDate() - delta);
    const day = candidate.getUTCDay();
    if (day !== 0 && day !== 6) {
      return candidate.toISOString().slice(0, 10);
    }
  }
  return today;
}

function defaultDateInShanghai() {
  const now = process.env.WORKFLOW_NOW_ISO ? new Date(process.env.WORKFLOW_NOW_ISO) : new Date();
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

async function stage(ctx, name, outputPath, fn) {
  const existing = outputPath && fs.existsSync(outputPath);
  const mustRefresh = shouldRefreshStage(ctx, name, outputPath);
  if (ctx.resume && !mustRefresh && existing && ctx.state.stages[name]?.status === "done") {
    event(ctx.eventsPath, "stage_skip", { stage: name, output: outputPath });
    return;
  }
  ctx.state.stages[name] = { status: "running", startedAt: nowIso() };
  saveState(ctx.statePath, ctx.state);
  event(ctx.eventsPath, "stage_start", { stage: name });
  const started = Date.now();
  try {
    const result = await fn();
    ctx.state.stages[name] = {
      status: "done",
      startedAt: ctx.state.stages[name].startedAt,
      endedAt: nowIso(),
      durationMs: Date.now() - started,
      result,
    };
    event(ctx.eventsPath, "stage_end", { stage: name, duration_ms: Date.now() - started, result });
  } catch (err) {
    ctx.state.stages[name] = {
      status: "failed",
      startedAt: ctx.state.stages[name].startedAt,
      endedAt: nowIso(),
      durationMs: Date.now() - started,
      error: String(err.message || err),
    };
    event(ctx.eventsPath, "stage_error", { stage: name, error: String(err.message || err) });
    saveState(ctx.statePath, ctx.state);
    throw err;
  }
  saveState(ctx.statePath, ctx.state);
}

function shouldRefreshStage(ctx, name, outputPath) {
  if (ctx.retryFailed && (name === "finalize" || name === "publish")) {
    return true;
  }
  if (
    name === "publish"
    && ctx.opts?.publish
    && !ctx.opts?.publishDryRun
    && !ctx.opts?.publishNoPush
    && outputPath
    && fs.existsSync(outputPath)
    && ctx.state.stages[name]?.status === "done"
  ) {
    const published = readJsonIfExists(outputPath) || {};
    return published.pushed !== true;
  }
  return false;
}

function runHelper(args, extra = null) {
  const result = spawnSync("python3", [HELPER, ...args], {
    cwd: SCRIPTS_DIR,
    encoding: "utf-8",
    env: { ...process.env, ...helperEnvOverrides },
  });
  if (result.status !== 0) {
    throw new Error(`helper failed: ${args[0]}\n${result.stderr || result.stdout}`);
  }
  try {
    const parsed = JSON.parse(result.stdout);
    if (extra && parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      parsed.llm_input_cleanup = extra;
    }
    return parsed;
  } catch (err) {
    throw new Error(`helper returned invalid JSON for ${args[0]}: ${result.stdout}`);
  }
}

async function readOneReport({ item, readerPath, state, statePath, eventsPath, llmInputDir, antigravityLogDir, timeoutSeconds }) {
  const c = item.candidate;
  const reportState = state.reports[c.report_id] || {};
  const attempts = Number(reportState.attempts || 0) + 1;
  state.reports[c.report_id] = {
    ...reportState,
    reportId: c.report_id,
    title: c.title,
    status: "reading",
    attempts,
    pdfPath: c.pdf_path,
  };
  saveState(statePath, state);
  event(eventsPath, "report_read_start", { report_id: c.report_id, title: c.title, attempts });
  try {
    const llmPdfPath = prepareLlmPdf(c, llmInputDir);
    state.reports[c.report_id] = {
      ...state.reports[c.report_id],
      llmPdfPath,
    };
    saveState(statePath, state);
    const reader = await runAntigravityReader({ ...c, llm_pdf_path: llmPdfPath }, { timeoutSeconds, antigravityLogDir });
    writeJson(readerPath, reader);
    state.reports[c.report_id] = {
      ...state.reports[c.report_id],
      status: "read_done",
      readerPath,
      readScore: reader.read_score ?? null,
      updatedAt: nowIso(),
    };
    event(eventsPath, "report_read_end", { report_id: c.report_id, title: c.title, reader_path: readerPath });
  } catch (err) {
    const diagnostics = sanitizeDiagnostics(err.diagnostics);
    state.reports[c.report_id] = {
      ...state.reports[c.report_id],
      status: "failed",
      lastError: String(err.message || err),
      ...(diagnostics.reason ? { lastErrorReason: diagnostics.reason } : {}),
      ...(diagnostics.log_file ? { lastErrorLogFile: diagnostics.log_file } : {}),
      updatedAt: nowIso(),
    };
    event(eventsPath, "report_read_error", {
      report_id: c.report_id,
      title: c.title,
      error: String(err.message || err),
      ...(diagnostics.reason ? { reason: diagnostics.reason } : {}),
      ...(diagnostics.message ? { message: diagnostics.message } : {}),
      ...(diagnostics.log_file ? { log_file: diagnostics.log_file } : {}),
    });
    if (isGlobalLlmFailure(diagnostics.reason)) {
      markLlmUnavailable(state, statePath, eventsPath, diagnostics);
    }
  } finally {
    saveState(statePath, state);
  }
}

function prepareLlmPdf(candidate, llmInputDir) {
  if (!candidate.pdf_path || !fs.existsSync(candidate.pdf_path)) {
    throw new Error("pdf_missing");
  }
  fs.mkdirSync(llmInputDir, { recursive: true });
  fs.writeFileSync(path.join(llmInputDir, LLM_INPUT_MARKER), JSON.stringify({ createdAt: nowIso() }), "utf-8");
  const target = path.join(llmInputDir, `${safeFileStem(candidate.report_id)}.pdf`);
  fs.copyFileSync(candidate.pdf_path, target);
  return target;
}

function nextAntigravityLogFile(root, reportId) {
  const dir = root || path.join(os.tmpdir(), "tradesystem-antigravity-logs");
  fs.mkdirSync(dir, { recursive: true });
  return path.join(dir, `${safeFileStem(reportId)}-${process.pid}-${Date.now()}.log`);
}

function isLlmUnavailable(state) {
  return state.llmStatus === "unavailable";
}

function markLlmUnavailable(state, statePath, eventsPath, diagnostics, eventExtra = {}) {
  if (isLlmUnavailable(state)) return;
  const d = sanitizeDiagnostics(diagnostics);
  state.llmStatus = "unavailable";
  state.llmFailureReason = d.reason || "antigravity_unavailable";
  state.llmFailureMessage = d.message || state.llmFailureReason;
  state.llmFailureLogFile = d.log_file || "";
  state.llmGlobalFailureAt = nowIso();
  event(eventsPath, "llm_global_failure", {
    reason: state.llmFailureReason,
    message: state.llmFailureMessage,
    log_file: state.llmFailureLogFile,
    ...eventExtra,
  });
  saveState(statePath, state);
}

function syncLlmStatusFromFinalizeResult(state, statePath, eventsPath, result) {
  if (!result || result.llm_status !== "unavailable") {
    return;
  }
  markLlmUnavailable(
    state,
    statePath,
    eventsPath,
    {
      reason: result.llm_failure_reason || "antigravity_unavailable",
      message: result.llm_failure_message || result.llm_failure_reason || "antigravity_unavailable",
      log_file: result.llm_failure_log_file || "",
    },
    { stage: "finalize" },
  );
}

function markReportSkippedForLlmUnavailable(item, state, eventsPath, readerPath = "") {
  const c = item.candidate;
  const existing = state.reports[c.report_id] || {};
  if (existing.status === "read_done" || existing.status === "skipped_llm_unavailable") return;
  state.reports[c.report_id] = {
    ...existing,
    reportId: c.report_id,
    title: c.title,
    pdfPath: c.pdf_path || existing.pdfPath || "",
    status: "skipped_llm_unavailable",
    lastError: state.llmFailureReason || "llm_unavailable",
    updatedAt: nowIso(),
  };
  if (readerPath) {
    writeJson(readerPath, {
      reader: {
        error: "skipped_llm_unavailable",
        reason: state.llmFailureReason || "llm_unavailable",
        message: state.llmFailureMessage || "",
      },
    });
  }
  event(eventsPath, "report_read_skip", {
    report_id: c.report_id,
    title: c.title,
    reason: "skipped_llm_unavailable",
  });
}

function readerStats(state) {
  const reports = Object.values(state.reports || {});
  return {
    llm_status: state.llmStatus || "ok",
    reader_success_count: reports.filter((report) => report.status === "read_done").length,
    reader_failed_count: reports.filter((report) => report.status === "failed").length,
    reader_skipped_count: reports.filter((report) => report.status === "skipped_llm_unavailable").length,
  };
}

function resetLlmStatus(state, eventsPath) {
  const oldStatus = state.llmStatus || "ok";
  const oldReason = state.llmFailureReason || "";
  state.llmStatus = "ok";
  delete state.llmFailureReason;
  delete state.llmFailureMessage;
  delete state.llmFailureLogFile;
  delete state.llmGlobalFailureAt;
  for (const report of Object.values(state.reports || {})) {
    if (report.status === "skipped_llm_unavailable") {
      report.status = "failed";
      report.lastError = "reset_llm_status";
      delete report.lastErrorReason;
      delete report.lastErrorLogFile;
      report.updatedAt = nowIso();
    }
  }
  event(eventsPath, "llm_status_reset", {
    old_status: oldStatus,
    old_reason: oldReason,
    new_status: "ok",
  });
}

function workflowSummary(state, opts) {
  const stats = readerStats(state);
  const finalizeResult = state.stages?.finalize?.result || {};
  const publishResult = state.stages?.publish?.result || {};
  return {
    invocation_id: state.currentInvocation?.id || currentInvocationId,
    llm_status: stats.llm_status,
    reader_success_count: stats.reader_success_count,
    reader_failed_count: stats.reader_failed_count,
    reader_skipped_count: stats.reader_skipped_count,
    ranker_status: finalizeResult.ranker_status || "",
    include_base_digest: Boolean(opts.includeBaseDigest),
    base_digest_included: Boolean(publishResult.base_digest_included),
    published: Boolean(opts.publish && state.stages?.publish?.status === "done"),
    pushed: Boolean(publishResult.pushed),
    summary: state.runDir ? path.join(state.runDir, "summary.json") : finalizeResult.summary || "",
    markdown: state.runDir ? path.join(state.runDir, "report.md") : finalizeResult.markdown || "",
  };
}

function preflightEnabled(opts) {
  const raw = process.env.HUIBO_ANTIGRAVITY_PREFLIGHT;
  return Boolean(opts.preflight || raw === "1" || String(raw || "").toLowerCase() === "true");
}

async function runAntigravityPreflight({ timeoutSeconds, antigravityLogDir }) {
  const logFile = nextAntigravityLogFile(antigravityLogDir, "preflight");
  const cmd = buildAntigravityCommand("Antigravity health check. Output exactly JSON: {\"ok\":true}", {
    timeoutSeconds,
    logFile,
  });
  let stdout = "";
  let stderr = "";
  try {
    const result = await runProcess(cmd.command, cmd.args, (timeoutSeconds + 10) * 1000, { logFile });
    stdout = result.stdout;
    stderr = result.stderr;
  } catch (err) {
    const diagnostics = sanitizeDiagnostics(err.diagnostics || diagnoseAntigravityFailure({
      stdout: err.stdout || "",
      stderr: err.stderr || String(err.message || err),
      logFile,
      reason: err.reason || "antigravity_failed",
    }));
    return {
      status: isGlobalLlmFailure(diagnostics.reason) ? "unavailable" : "warning",
      ...diagnostics,
    };
  }
  if (!String(stdout || "").trim()) {
    const diagnostics = diagnoseAntigravityFailure({ stdout, stderr, logFile, reason: "empty_stdout" });
    return {
      status: isGlobalLlmFailure(diagnostics.reason) ? "unavailable" : "warning",
      ...diagnostics,
    };
  }
  const parsed = parseJsonObject(stdout);
  if (!parsed) {
    const diagnostics = diagnoseAntigravityFailure({ stdout, stderr, logFile, reason: "parse_failed" });
    return { status: "warning", ...diagnostics };
  }
  return { status: "ok", reason: "ok", message: "ok", log_file: logFile };
}

function writeRunReport(file, state, opts) {
  const stats = readerStats(state);
  const lines = [
    `# Research Digest Workflow Run · ${state.date || ""}`,
    "",
    "## Summary",
    "",
    `| key | value |`,
    `| --- | --- |`,
    `| invocation_id | ${state.currentInvocation?.id || currentInvocationId} |`,
    `| llm_status | ${stats.llm_status} |`,
    `| reader_success_count | ${stats.reader_success_count} |`,
    `| reader_failed_count | ${stats.reader_failed_count} |`,
    `| reader_skipped_count | ${stats.reader_skipped_count} |`,
    `| ranker_status | ${state.stages?.finalize?.result?.ranker_status || ""} |`,
    `| include_base_digest | ${Boolean(opts.includeBaseDigest)} |`,
    `| base_digest_included | ${Boolean(state.stages?.publish?.result?.base_digest_included)} |`,
    "",
    "## Stages",
    "",
    `| stage | status | duration_ms |`,
    `| --- | --- | ---: |`,
  ];
  for (const [name, row] of Object.entries(state.stages || {})) {
    lines.push(`| ${name} | ${row.status || ""} | ${row.durationMs ?? ""} |`);
  }
  lines.push("", "## Reports", "", `| title | status | attempts | last_error |`, `| --- | --- | ---: | --- |`);
  for (const report of Object.values(state.reports || {})) {
    lines.push(`| ${escapeMd(report.title || report.reportId || "")} | ${report.status || ""} | ${report.attempts || 0} | ${escapeMd(report.lastErrorReason || report.lastError || "")} |`);
  }
  fs.writeFileSync(file, `${lines.join("\n")}\n`, "utf-8");
}

async function runAntigravityReader(candidate, { timeoutSeconds, antigravityLogDir }) {
  const pdfPath = candidate.llm_pdf_path || candidate.pdf_path;
  const prompt = [
    "请读取这个PDF研报，只输出JSON，不要markdown。",
    "字段：title, industry, viewpoint, key_points(数组最多3条), recommend_reason, read_score(0-100), ",
    "mentioned_stocks(数组，每项 name, viewpoint, source, source_page, source_section)。",
    "mentioned_stocks 的 viewpoint 只能写该个股在研报中的独立观点；如果只是可比公司、客户、供应商、数据引用来源，viewpoint 必须留空，把关系写到 source。",
    "source_page/source_section 尽量给页码或章节；不要输出目标价、买入卖出、仓位或价格预测。",
    `候选标题：${candidate.title}。PDF：@${path.resolve(pdfPath)}`,
  ].join("");
  const logFile = nextAntigravityLogFile(antigravityLogDir, candidate.report_id);
  const cmd = buildAntigravityCommand(prompt, {
    addDirs: [path.dirname(path.resolve(pdfPath))],
    timeoutSeconds,
    logFile,
  });
  let stdout = "";
  let stderr = "";
  try {
    const result = await runProcess(cmd.command, cmd.args, (timeoutSeconds + 30) * 1000, { logFile });
    stdout = result.stdout;
    stderr = result.stderr;
  } catch (err) {
    const diagnostics = sanitizeDiagnostics(err.diagnostics || diagnoseAntigravityFailure({
      stdout: err.stdout || "",
      stderr: err.stderr || String(err.message || err),
      logFile,
      reason: err.reason || "antigravity_failed",
    }));
    err.diagnostics = diagnostics;
    throw err;
  }
  if (!String(stdout || "").trim()) {
    const diagnostics = diagnoseAntigravityFailure({
      stdout,
      stderr,
      logFile,
      reason: "empty_stdout",
    });
    const err = new Error(diagnostics.message || "antigravity stdout is empty");
    err.diagnostics = diagnostics;
    throw err;
  }
  const parsed = parseJsonObject(stdout);
  if (!parsed) {
    const diagnostics = diagnoseAntigravityFailure({
      stdout,
      stderr,
      logFile,
      reason: "parse_failed",
    });
    const err = new Error("antigravity output has no JSON object");
    err.diagnostics = diagnostics;
    throw err;
  }
  return parsed;
}

function cleanupLlmInputDir(llmInputDir, dryRun = false) {
  if (!fs.existsSync(llmInputDir)) {
    return { dir: llmInputDir, removed: false };
  }
  const marker = path.join(llmInputDir, LLM_INPUT_MARKER);
  if (!fs.existsSync(marker)) {
    return { dir: llmInputDir, removed: false, skipped: "missing_marker" };
  }
  if (dryRun) {
    return { dir: llmInputDir, removed: false, dry_run: true, would_remove: true };
  }
  fs.rmSync(llmInputDir, { recursive: true, force: true });
  return { dir: llmInputDir, removed: true };
}

function buildAntigravityCommand(prompt, options = {}) {
  const agy = process.env.ANTIGRAVITY_BIN || process.env.AGY_BIN || "agy";
  const timeoutSeconds = intOpt(options.timeoutSeconds || process.env.LLM_TIMEOUT_SECONDS, 180);
  const args = ["--print-timeout", `${timeoutSeconds}s`];
  for (const addDir of options.addDirs || []) {
    args.push("--add-dir", addDir);
  }
  args.push("--dangerously-skip-permissions");
  const model = process.env.LLM_MODEL || process.env.ANTIGRAVITY_MODEL || "";
  if (model) args.push("--model", model);
  if (options.logFile) args.push("--log-file", options.logFile);
  args.push("--prompt", prompt);
  return { command: agy, args };
}

function runProcess(command, args, timeoutMs, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      env: process.env,
      detached: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    let settled = false;
    let timeoutError = null;
    let earlyError = null;
    let forceTimer = null;
    const finish = (fn, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (forceTimer) clearTimeout(forceTimer);
      fn(value);
    };
    const timer = setTimeout(() => {
      timeoutError = new Error(`process timed out after ${timeoutMs}ms`);
      signalProcessGroup(child, "SIGTERM");
      forceTimer = setTimeout(() => {
        signalProcessGroup(child, "SIGKILL");
      }, 2000);
    }, timeoutMs);
    const inspectOutput = () => {
      if (earlyError) return;
      const fatal = detectFatalAntigravityPrompt(`${stdout}\n${stderr}`);
      if (!fatal) return;
      earlyError = new Error(fatal);
      earlyError.reason = "auth_required";
      earlyError.diagnostics = diagnoseAntigravityFailure({
        stdout,
        stderr,
        logFile: options.logFile,
        reason: "auth_required",
      });
      clearTimeout(timer);
      signalProcessGroup(child, "SIGTERM");
      forceTimer = setTimeout(() => {
        signalProcessGroup(child, "SIGKILL");
      }, 2000);
    };
    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString();
      inspectOutput();
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
      inspectOutput();
    });
    child.on("error", (err) => {
      err.reason = "startup_failed";
      err.diagnostics = diagnoseAntigravityFailure({
        stdout,
        stderr: String(err.message || err),
        logFile: options.logFile,
        reason: "startup_failed",
      });
      finish(reject, err);
    });
    child.on("close", (code) => {
      if (earlyError) {
        finish(reject, earlyError);
      } else if (timeoutError) {
        timeoutError.diagnostics = diagnoseAntigravityFailure({
          stdout,
          stderr,
          logFile: options.logFile,
          reason: "timeout",
        });
        finish(reject, timeoutError);
      } else if (code === 0) {
        finish(resolve, { stdout, stderr });
      } else {
        const err = new Error(`process exited ${code}: ${stderr || stdout}`);
        err.stdout = stdout;
        err.stderr = stderr;
        err.diagnostics = diagnoseAntigravityFailure({
          stdout,
          stderr,
          logFile: options.logFile,
          reason: "antigravity_failed",
          returncode: code,
        });
        finish(reject, err);
      }
    });
  });
}

function detectFatalAntigravityPrompt(output) {
  const text = String(output || "");
  if (text.includes("Opening authentication page") && text.includes("Do you want to continue?")) {
    return "antigravity requires interactive authentication";
  }
  if (text.includes("authorization code") && text.toLowerCase().includes("sign in")) {
    return "antigravity requires interactive authentication";
  }
  return "";
}

function signalProcessGroup(child, signal) {
  if (!child.pid) return;
  try {
    process.kill(-child.pid, signal);
  } catch (_) {
    try {
      child.kill(signal);
    } catch (__) {
      // best-effort cleanup
    }
  }
}

async function runPool(items, concurrency, worker) {
  let index = 0;
  const workers = Array.from({ length: Math.min(concurrency, items.length || 1) }, async () => {
    while (index < items.length) {
      const current = items[index];
      index += 1;
      await worker(current);
    }
  });
  await Promise.all(workers);
}

function parseJsonObject(text) {
  const trimmed = String(text || "").trim();
  try {
    const parsed = JSON.parse(trimmed);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : null;
  } catch (_) {
    // continue
  }
  const start = trimmed.indexOf("{");
  if (start < 0) return null;
  let depth = 0;
  for (let i = start; i < trimmed.length; i += 1) {
    if (trimmed[i] === "{") depth += 1;
    if (trimmed[i] === "}") {
      depth -= 1;
      if (depth === 0) {
        try {
          const parsed = JSON.parse(trimmed.slice(start, i + 1));
          return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : null;
        } catch (_) {
          return null;
        }
      }
    }
  }
  return null;
}

function event(eventsPath, eventName, payload) {
  fs.mkdirSync(path.dirname(eventsPath), { recursive: true });
  fs.appendFileSync(eventsPath, `${JSON.stringify({ ts: nowIso(), event: eventName, invocation_id: currentInvocationId, ...payload })}\n`, "utf-8");
}

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, "utf-8"));
}

function readJsonIfExists(file) {
  if (!fs.existsSync(file)) return null;
  return readJson(file);
}

function writeJson(file, payload) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(payload, null, 2), "utf-8");
}

function saveState(file, state) {
  state.updatedAt = nowIso();
  writeJsonAtomic(file, state);
}

function writeJsonAtomic(file, payload) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const tmp = `${file}.${process.pid}.${Date.now()}.${Math.random().toString(36).slice(2)}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(payload, null, 2), "utf-8");
  fs.renameSync(tmp, file);
}

function nowIso() {
  return new Date().toISOString();
}

function newInvocationId(startedAt) {
  return `${startedAt.replace(/[^0-9A-Za-z]/g, "").slice(0, 17)}-${process.pid}-${Math.random().toString(36).slice(2, 10)}`;
}

function intOpt(value, fallback) {
  const n = Number.parseInt(String(value ?? ""), 10);
  return Number.isFinite(n) && n > 0 ? n : fallback;
}

function abs(value) {
  return path.isAbsolute(value) ? value : path.resolve(REPO_ROOT, value);
}

function safeFileStem(value) {
  return String(value || "report").replace(/[^A-Za-z0-9._-]/g, "_").slice(0, 120) || "report";
}

function escapeMd(value) {
  return String(value || "").replace(/\|/g, "\\|").replace(/\r?\n/g, " ");
}

function camel(key) {
  return key.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
}

function shellJoin(parts) {
  return parts.map((part) => `'${String(part).replace(/'/g, "'\\''")}'`).join(" ");
}

main().catch((err) => {
  if (fatalCleanupDir) {
    try {
      cleanupLlmInputDir(fatalCleanupDir, false);
    } catch (_) {
      // best-effort fatal cleanup
    }
  }
  console.error(`[workflow] failed: ${err.message || err}`);
  process.exit(1);
});
