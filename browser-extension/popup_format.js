export function formatCaptureResult(result) {
  if (!result || typeof result !== "object") {
    return "❌ Capture failed: empty response";
  }
  if (result.status !== "ok") {
    return formatErrorResult(result);
  }

  const lines = [];
  const action = actionLabel(result.action || "captured");
  const citekey = result.citekey || "entry";
  const bibName = result.bib_name || result.bib;
  const bib = bibName ? ` in ${bibName}` : "";
  const isUnchanged = result.action === "update"
    && Array.isArray(result.changed_fields)
    && result.changed_fields.length === 0;
  if (isUnchanged) {
    lines.push(`✅ Already captured ${citekey}${bib}`);
  } else {
    lines.push(`✅ ${action} ${citekey}${bib}`);
  }

  const pdf = pdfStatusLines(result);
  for (const line of pdf) lines.push(line);

  for (const warning of displayWarnings(result)) {
    if (warning) lines.push(`⚠️ ${warning}`);
  }
  for (const warning of metadataWarnings(result)) {
    lines.push(`⚠️ ${warning}`);
  }
  const diagnostics = metadataDiagnostics(result);
  if (diagnostics.length > 0) {
    lines.push("metadata diagnostics:");
    for (const line of diagnostics) lines.push(`  ${line}`);
  }
  return lines.join("\n");
}

function metadataWarnings(result) {
  if (!Array.isArray(result.metadata_warnings)) return [];
  return result.metadata_warnings.filter((warning) => typeof warning === "string" && warning);
}

function metadataDiagnostics(result) {
  if (!Array.isArray(result.metadata_diagnostics)) return [];
  return result.metadata_diagnostics.filter((line) => typeof line === "string" && line);
}

function displayWarnings(result) {
  const warnings = Array.isArray(result.warnings) ? result.warnings : [];
  if (result.pdf_attach && result.pdf_attach.status === "ok") {
    return warnings.filter(
      (warning) =>
        !isSupersededPdfWarning(warning, {
          attachedUrl: result.pdf_attach.source_url,
          staleError: result.pdf_error,
        })
    );
  }
  return warnings;
}

function isSupersededPdfWarning(warning, { attachedUrl = null, staleError = null } = {}) {
  if (typeof warning !== "string") return false;
  if (!isPdfDownloadWarning(warning)) return false;
  if (staleError && warning === staleError) return true;
  if (attachedUrl && warning.includes(attachedUrl)) return true;
  return false;
}

function isPdfDownloadWarning(warning) {
  return (
    warning.includes("all download methods failed") ||
    warning.includes("PDF download blocked") ||
    warning.includes("downloaded content from")
  );
}

function formatErrorResult(result) {
  const message = result.message || "capture failed";
  const lines = [`❌ Capture failed: ${message}`];
  for (const error of result.errors || []) {
    if (error) lines.push(`- ${error}`);
  }
  for (const warning of (result.warnings || [])) {
    if (warning) lines.push(`⚠️ ${warning}`);
  }
  return lines.join("\n");
}

function pdfStatusLines(result) {
  const attach = result.pdf_attach;
  const permission = result.pdf_attach_permission || attach?.pdf_attach_permission;
  if (permission && permission.status === "denied") {
    const suffix = permission.origin ? ` for ${permission.origin}` : "";
    return [`⚠️ PDF permission denied${suffix}`];
  }
  if (attach && attach.status === "ok") {
    const lines = ["📄 PDF saved"];
    if (attach.local_pdf_path || attach.pdf_path) {
      lines.push(attach.local_pdf_path || attach.pdf_path);
    }
    const savedAttempt = savedPdfAttempt(result);
    if (savedAttempt) {
      lines.push(`via ${savedAttempt.mode} (${savedAttempt.content_type}, ${fmtBytes(savedAttempt.byte_count)})`);
    }
    return lines;
  }
  if (result.pdf_path) {
    return ["📄 PDF saved", result.pdf_path];
  }
  if (result.pdf_status === "direct_saved" || result.pdf_status === "browser_saved") {
    return ["📄 PDF saved"];
  }

  // If PDF not saved and we have attempt details, show them
  const formatted = formattedAttempts(result);
  const rawAttempts = Array.isArray(result.pdf_attach_attempts) ? result.pdf_attach_attempts : [];
  const authAttempt = rawAttempts.find(a => a.status === "html_login" || a.status === "html_access_denied");
  if (authAttempt) {
    const guidance = authAttempt.status === "html_login"
      ? "PDF requires login — open the page, sign in, then retry capture"
      : "PDF access denied — you may need institutional access or subscription";
    return [`⚠️ ${guidance}`].concat(formatted.map((a) => `  • ${a}`));
  }
  if (formatted.length > 0) {
    return ["⚠️ PDF save attempts:"].concat(formatted.map((a) => `  • ${a}`));
  }

  if (attach && attach.status === "error") {
    return ["⚠️ PDF not saved: " + (attach.message || "browser attach failed")];
  }
  if (result.pdf_error) {
    const lines = [`⚠️ PDF not saved: ${result.pdf_error}`];
    if (result.pdf_suggestion) lines.push(result.pdf_suggestion);
    return lines;
  }
  if (result.pdf_status === "direct_blocked") {
    return ["⚠️ PDF not saved: publisher blocked direct download", "Try browser extension PDF attach"];
  }
  if (result.pdf_status === "failed") {
    return ["⚠️ PDF not saved"];
  }
  return [];
}

function savedPdfAttempt(result) {
  const attempts = result.pdf_attach_attempts;
  if (!Array.isArray(attempts) || attempts.length === 0) return null;
  return attempts.find((a) => a.status === "saved") || null;
}

function formattedAttempts(result) {
  const attempts = result.pdf_attach_attempts;
  if (!Array.isArray(attempts) || attempts.length === 0) return [];
  return attempts.map((a) => {
    const mode = a.mode || "unknown";
    const status = a.status || "unknown";
    const contentType = a.content_type || "?";
    return `${mode}: ${status} (${contentType})`;
  });
}

function fmtBytes(n) {
  if (typeof n !== "number" || n < 0) return "?";
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1048576).toFixed(1)} MB`;
}

function titleCase(value) {
  const text = String(value || "");
  return text ? text[0].toUpperCase() + text.slice(1) : text;
}

function actionLabel(value) {
  const text = String(value || "");
  if (text === "insert") return "Added";
  if (text === "update") return "Updated";
  return titleCase(text);
}


// ── Multi-item capture result formatting (Tier 4) ───────────────────────

export function formatMultiCaptureResult(outcome) {
  if (!outcome || !Array.isArray(outcome.results)) {
    return "❌ Capture failed: no results";
  }

  let success = 0;
  let fail = 0;
  const lines = [];

  for (const r of outcome.results) {
    if (r && r.status === "ok") {
      success++;
      lines.push("✅ " + (r.citekey || "entry"));
    } else {
      fail++;
      const msg = (r && r.message) ? r.message : "failed";
      lines.push("❌ " + msg);
    }
  }

  const summary = "Captured " + success + "/" + outcome.total + " items";
  if (fail > 0) return summary + " (" + fail + " failed)\n" + lines.join("\n");
  return summary;
}
