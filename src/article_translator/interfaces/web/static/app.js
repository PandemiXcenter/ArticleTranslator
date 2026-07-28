const state = {
  config: null,
  selectedFile: null,
  jobId: null,
  job: null,
  review: null,
  pollTimer: null,
  pollFailures: 0,
  blockIndex: new Map(),
  uncertaintyIndex: new Map(),
  sourceElements: new Map(),
  activeUncertaintyId: null,
  scrollFrame: null,
  sessionApiKey: "",
  apiKeyConfigured: false,
  apiKeySaved: false,
};

const tabButtons = [...document.querySelectorAll("[role='tab']")];
const tabPanels = [...document.querySelectorAll("[role='tabpanel']")];
const translatePanel = document.querySelector("#translate-panel");
const reviewPanel = document.querySelector("#review-panel");
const reviewTab = document.querySelector("#review-tab");
const translationSetup = document.querySelector("#translation-setup");
const jobProgress = document.querySelector("#job-progress");
const form = document.querySelector("#translation-form");
const fileInput = document.querySelector("#pdf-input");
const fileDetail = document.querySelector("#file-detail");
const fileError = document.querySelector("#file-error");
const dropZone = document.querySelector("#drop-zone");
const mappingBody = document.querySelector("#mapping-body");
const mappingHelp = document.querySelector("#mapping-help");
const mappingError = document.querySelector("#mapping-error");
const addMappingButton = document.querySelector("#add-mapping");
const startButton = document.querySelector("#start-translation");
const startButtonLabel = startButton.querySelector(".button-label");
const formError = document.querySelector("#form-error");
const globalAlert = document.querySelector("#global-alert");
const globalAlertMessage = document.querySelector("#global-alert-message");
const dismissAlert = document.querySelector("#dismiss-alert");
const progressFilename = document.querySelector("#progress-filename");
const progressAnnouncement = document.querySelector("#progress-announcement");
const progressStatus = document.querySelector("#progress-status");
const progressPages = document.querySelector("#progress-pages");
const pageProgress = document.querySelector("#page-progress");
const progressErrorActions = document.querySelector("#progress-error-actions");
const backToSetupButton = document.querySelector("#back-to-setup");
const reviewDocument = document.querySelector("#review-document");
const reviewProgress = document.querySelector("#review-progress");
const exportLink = document.querySelector("#export-link");
const activePageLabel = document.querySelector("#active-page-label");
const sourceScroll = document.querySelector("#source-scroll");
const translationScroll = document.querySelector("#translation-scroll");
const sourceContent = document.querySelector("#source-content");
const translationContent = document.querySelector("#translation-content");
const uncertaintyDialog = document.querySelector("#uncertainty-dialog");
const uncertaintyForm = document.querySelector("#uncertainty-form");
const closeUncertaintyButton = document.querySelector("#close-uncertainty");
const cancelUncertaintyButton = document.querySelector("#cancel-uncertainty");
const translateOneButton = document.querySelector("#translate-one");
const translateAllButton = document.querySelector("#translate-all");
const replacementInput = document.querySelector("#replacement-input");
const replacementError = document.querySelector("#replacement-error");
const replacementField = document.querySelector("#replacement-field");
const fallbackInstruction = document.querySelector("#fallback-instruction");
const sourceLanguageInput = document.querySelector("#source-language");
const targetLanguageInput = document.querySelector("#target-language");
const languageError = document.querySelector("#language-error");
const modelSelect = document.querySelector("#model-select");
const translationStyle = document.querySelector("#translation-style");
const geminiApiKey = document.querySelector("#gemini-api-key");
const saveApiKey = document.querySelector("#save-api-key");
const apiKeyStatus = document.querySelector("#api-key-status");
const apiKeyError = document.querySelector("#api-key-error");
const settingsMessage = document.querySelector("#settings-message");
const saveSettingsButton = document.querySelector("#save-settings");
const clearSavedKeyButton = document.querySelector("#clear-saved-key");
const newTranslationButton = document.querySelector("#new-translation");

const READY_STATUSES = new Set([
  "ready",
  "translated",
  "compiled",
  "completed",
  "review_ready",
]);
const FAILED_STATUSES = new Set(["failed", "cancelled"]);

function createElement(tagName, className, text) {
  const element = document.createElement(tagName);
  if (className) {
    element.className = className;
  }
  if (text !== undefined && text !== null) {
    element.textContent = String(text);
  }
  return element;
}

function asText(value, fallback = "") {
  if (value === undefined || value === null) {
    return fallback;
  }
  return String(value);
}

function showGlobalError(message) {
  globalAlertMessage.textContent = message;
  globalAlert.hidden = false;
  globalAlert.focus();
}

function clearGlobalError() {
  globalAlert.hidden = true;
  globalAlertMessage.textContent = "";
}

function setInlineError(element, message) {
  element.textContent = message;
  element.hidden = !message;
}

function getCookie(name) {
  const prefix = `${name}=`;
  const cookie = document.cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(prefix));
  if (!cookie) {
    return "";
  }
  try {
    return decodeURIComponent(cookie.slice(prefix.length));
  } catch {
    return cookie.slice(prefix.length);
  }
}

function errorMessageFromPayload(payload, status) {
  const detail = payload?.detail ?? payload?.error ?? payload?.message;
  if (typeof detail === "string" && detail.trim()) {
    return detail.trim();
  }
  if (Array.isArray(detail)) {
    const messages = detail
      .map((entry) => (typeof entry?.msg === "string" ? entry.msg : null))
      .filter(Boolean);
    if (messages.length) {
      return messages.join(" ");
    }
  }
  if (status === 409) {
    return "This document changed since it was loaded. Refresh the review and try again.";
  }
  if (status === 413) {
    return "This PDF is larger than the configured upload limit.";
  }
  return `The request could not be completed (HTTP ${status}).`;
}

async function apiRequest(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const headers = new Headers(options.headers || {});
  headers.set("Accept", "application/json");
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    headers.set("X-CSRF-Token", getCookie("at_csrf"));
  }

  let response;
  try {
    response = await fetch(path, {
      ...options,
      method,
      headers,
      credentials: "same-origin",
    });
  } catch {
    throw new Error("ArticleTranslator could not reach the local server.");
  }

  const contentType = response.headers.get("content-type") || "";
  let payload = null;
  if (contentType.includes("application/json")) {
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
  }
  if (!response.ok) {
    throw new Error(errorMessageFromPayload(payload, response.status));
  }
  return payload;
}

function humanize(value) {
  const text = asText(value);
  if (!text) {
    return "Unknown";
  }
  return text
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatBytes(bytes) {
  const value = Number(bytes);
  if (!Number.isFinite(value) || value <= 0) {
    return "";
  }
  if (value >= 1024 * 1024) {
    return `${(value / 1024 / 1024).toFixed(1)} MB`;
  }
  return `${Math.ceil(value / 1024)} KB`;
}

function activateTab(name, focusPanel = true) {
  const selectedTab = tabButtons.find((button) => button.dataset.tab === name);
  if (!selectedTab || selectedTab.disabled) {
    return;
  }
  for (const button of tabButtons) {
    const selected = button === selectedTab;
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
  }
  for (const panel of tabPanels) {
    panel.hidden = panel.id !== selectedTab.getAttribute("aria-controls");
  }
  if (focusPanel) {
    const panel = document.querySelector(`#${selectedTab.getAttribute("aria-controls")}`);
    panel?.setAttribute("tabindex", "-1");
    requestAnimationFrame(() => panel?.focus({ preventScroll: true }));
  }
}

function updateMappingEmptyState() {
  mappingHelp.hidden = mappingBody.querySelectorAll("tr[data-testid='mapping-row']").length > 0;
}

function addMapping(source = "", target = "", focus = true) {
  const row = createElement("tr");
  row.dataset.testid = "mapping-row";

  const sourceCell = createElement("td");
  const sourceInput = createElement("input");
  sourceInput.type = "text";
  sourceInput.value = source;
  sourceInput.placeholder = "e.g. Vattersot";
  sourceInput.dataset.testid = "mapping-source";
  sourceInput.setAttribute("aria-label", "Archaic source term");
  sourceInput.autocomplete = "off";
  sourceCell.append(sourceInput);

  const targetCell = createElement("td");
  const targetInput = createElement("input");
  targetInput.type = "text";
  targetInput.value = target;
  targetInput.placeholder = "e.g. Dropsy";
  targetInput.dataset.testid = "mapping-target";
  targetInput.setAttribute("aria-label", "Required translation");
  targetInput.autocomplete = "off";
  targetCell.append(targetInput);

  const actionCell = createElement("td");
  const remove = createElement("button", "remove-row", "×");
  remove.type = "button";
  remove.setAttribute("aria-label", source ? `Remove mapping for ${source}` : "Remove mapping");
  remove.addEventListener("click", () => {
    row.remove();
    setInlineError(mappingError, "");
    updateMappingEmptyState();
  });
  actionCell.append(remove);

  for (const input of [sourceInput, targetInput]) {
    input.addEventListener("input", () => {
      input.removeAttribute("aria-invalid");
      setInlineError(mappingError, "");
    });
  }

  row.append(sourceCell, targetCell, actionCell);
  mappingBody.append(row);
  updateMappingEmptyState();
  if (focus) {
    sourceInput.focus();
  }
}

function normalizedGlossaryKey(value) {
  return value.normalize("NFKC").toLocaleLowerCase();
}

function collectGlossary() {
  const rows = [...mappingBody.querySelectorAll("tr[data-testid='mapping-row']")];
  const glossary = {};
  const seen = new Map();
  const incompleteRows = [];
  const duplicateRows = new Set();

  for (const row of rows) {
    const sourceInput = row.querySelector("[data-testid='mapping-source']");
    const targetInput = row.querySelector("[data-testid='mapping-target']");
    sourceInput.removeAttribute("aria-invalid");
    targetInput.removeAttribute("aria-invalid");
    const source = sourceInput.value.trim();
    const target = targetInput.value.trim();

    if (!source || !target) {
      sourceInput.setAttribute("aria-invalid", String(!source));
      targetInput.setAttribute("aria-invalid", String(!target));
      incompleteRows.push(row);
      continue;
    }

    const key = normalizedGlossaryKey(source);
    if (seen.has(key)) {
      duplicateRows.add(seen.get(key));
      duplicateRows.add(row);
      continue;
    }
    seen.set(key, row);
    glossary[source] = target;
  }

  for (const row of duplicateRows) {
    row.querySelector("[data-testid='mapping-source']").setAttribute("aria-invalid", "true");
  }

  const maxEntries = Number(state.config?.limits?.max_glossary_entries);
  if (Number.isFinite(maxEntries) && rows.length > maxEntries) {
    setInlineError(mappingError, `Use no more than ${maxEntries} asserted terms.`);
    return null;
  }
  const maxCharacters = Number(state.config?.limits?.max_term_characters);
  if (Number.isFinite(maxCharacters)) {
    const overlong = rows.find((row) => {
      const source = row.querySelector("[data-testid='mapping-source']");
      const target = row.querySelector("[data-testid='mapping-target']");
      const invalid = source.value.trim().length > maxCharacters ||
        target.value.trim().length > maxCharacters;
      if (invalid) {
        source.setAttribute(
          "aria-invalid",
          String(source.value.trim().length > maxCharacters),
        );
        target.setAttribute(
          "aria-invalid",
          String(target.value.trim().length > maxCharacters),
        );
      }
      return invalid;
    });
    if (overlong) {
      setInlineError(
        mappingError,
        `Keep each source term and translation within ${maxCharacters} characters.`,
      );
      overlong.querySelector("[aria-invalid='true']")?.focus();
      return null;
    }
  }
  if (incompleteRows.length) {
    setInlineError(mappingError, "Complete both cells in every mapping, or remove the blank row.");
    incompleteRows[0].querySelector("[aria-invalid='true']")?.focus();
    return null;
  }
  if (duplicateRows.size) {
    setInlineError(mappingError, "Each source term can appear only once.");
    [...duplicateRows][0].querySelector("[data-testid='mapping-source']")?.focus();
    return null;
  }
  setInlineError(mappingError, "");
  return glossary;
}

function selectedPdf() {
  return state.selectedFile || fileInput.files?.[0] || null;
}

function displaySelectedFile(file) {
  if (!file) {
    fileDetail.textContent = "No file selected";
    return;
  }
  fileDetail.textContent = `${file.name} · ${formatBytes(file.size)}`;
}

function validatePdf(file) {
  fileInput.removeAttribute("aria-invalid");
  dropZone.classList.remove("has-error");
  if (!file) {
    fileInput.setAttribute("aria-invalid", "true");
    dropZone.classList.add("has-error");
    setInlineError(fileError, "Select one PDF to translate.");
    return false;
  }

  const looksLikePdf =
    file.type === "application/pdf" || file.name.toLocaleLowerCase().endsWith(".pdf");
  if (!looksLikePdf) {
    fileInput.setAttribute("aria-invalid", "true");
    dropZone.classList.add("has-error");
    setInlineError(fileError, "Choose a file in PDF format.");
    return false;
  }

  const maxBytes = Number(state.config?.limits?.max_upload_bytes);
  if (Number.isFinite(maxBytes) && file.size > maxBytes) {
    fileInput.setAttribute("aria-invalid", "true");
    dropZone.classList.add("has-error");
    setInlineError(fileError, `Choose a PDF smaller than ${formatBytes(maxBytes)}.`);
    return false;
  }
  setInlineError(fileError, "");
  return true;
}

function applyFile(file) {
  state.selectedFile = file || null;
  displaySelectedFile(state.selectedFile);
  if (state.selectedFile) {
    validatePdf(state.selectedFile);
  }
}

function populateConfig(config) {
  state.config = config;
  const translation = config.translation || {};
  const provider = config.provider || {};
  sourceLanguageInput.value = asText(translation.source_language);
  targetLanguageInput.value = asText(translation.target_language);
  if (["faithful", "balanced", "readable"].includes(translation.style)) {
    translationStyle.value = translation.style;
  }

  modelSelect.replaceChildren();
  const selectableModels = Array.isArray(provider.selectable_models)
    ? provider.selectable_models
    : [];
  const models = selectableModels
    .map((model) => {
      if (typeof model === "string") {
        return { value: model, label: model };
      }
      return {
        value: asText(model?.id || model?.name || model?.value),
        label: asText(model?.label || model?.name || model?.id || model?.value),
      };
    })
    .filter((model) => model.value);
  const configuredModel = asText(provider.model);
  if (configuredModel && !models.some((model) => model.value === configuredModel)) {
    models.unshift({ value: configuredModel, label: configuredModel });
  }
  for (const model of models) {
    const option = createElement("option", "", model.label);
    option.value = model.value;
    option.selected = model.value === configuredModel;
    modelSelect.append(option);
  }

  updateApiKeyStatus(
    Boolean(config.api_key_configured),
    Boolean(config.api_key_saved_on_computer),
  );
  document.querySelector("#privacy-note").textContent =
    `${humanize(provider.name || "The configured provider")} receives one page image ` +
    "and its extracted text per request. Files and review data are stored locally.";

  const glossary = translation.glossary || {};
  for (const [source, target] of Object.entries(glossary)) {
    addMapping(asText(source), asText(target), false);
  }
  const maxBytes = Number(config.limits?.max_upload_bytes);
  if (Number.isFinite(maxBytes)) {
    fileDetail.textContent = `No file selected · ${formatBytes(maxBytes)} maximum`;
  }
}

function updateApiKeyStatus(
  configured = state.apiKeyConfigured,
  saved = state.apiKeySaved,
) {
  state.apiKeyConfigured = Boolean(configured);
  state.apiKeySaved = Boolean(saved);
  apiKeyStatus.textContent = state.apiKeySaved
    ? "A saved API key is available. Leave this field blank to use it."
    : state.sessionApiKey || geminiApiKey.value.trim()
      ? "A session-only API key is ready for the next translation."
    : state.apiKeyConfigured
      ? "An API key is available for this session. Leave this field blank to use it."
      : "No API key is currently available.";
  clearSavedKeyButton.hidden = !state.apiKeySaved;
}

function validateJobSettings() {
  const sourceLanguage = sourceLanguageInput.value.trim();
  const targetLanguage = targetLanguageInput.value.trim();
  sourceLanguageInput.removeAttribute("aria-invalid");
  targetLanguageInput.removeAttribute("aria-invalid");
  if (!sourceLanguage || !targetLanguage) {
    sourceLanguageInput.setAttribute("aria-invalid", String(!sourceLanguage));
    targetLanguageInput.setAttribute("aria-invalid", String(!targetLanguage));
    setInlineError(languageError, "Enter both the input and output language.");
    return null;
  }
  setInlineError(languageError, "");

  const enteredKey = geminiApiKey.value.trim() || state.sessionApiKey;
  const useConfiguredApiKey = !enteredKey && state.apiKeyConfigured;
  if (!enteredKey && !useConfiguredApiKey) {
    setInlineError(apiKeyError, "Enter a Gemini API key in Settings.");
    activateTab("settings");
    geminiApiKey.focus();
    return null;
  }
  setInlineError(apiKeyError, "");
  return {
    settings: {
      model: modelSelect.value,
      source_language: sourceLanguage,
      target_language: targetLanguage,
      style: translationStyle.value,
    },
    sessionKey: useConfiguredApiKey ? "" : enteredKey,
  };
}

function setStartBusy(isBusy) {
  startButton.disabled = isBusy;
  addMappingButton.disabled = isBusy;
  fileInput.disabled = isBusy;
  startButtonLabel.textContent = isBusy ? "Uploading PDF…" : "Start translation";
}

async function saveSettings() {
  clearGlobalError();
  settingsMessage.textContent = "";
  const apiKey = geminiApiKey.value.trim();
  if (!apiKey) {
    if (state.sessionApiKey || state.apiKeyConfigured) {
      settingsMessage.textContent = state.sessionApiKey
        ? "The session-only API key remains available."
        : "The configured API key remains available.";
      return;
    }
    setInlineError(apiKeyError, "Enter an API key before saving settings.");
    geminiApiKey.focus();
    return;
  }

  saveSettingsButton.disabled = true;
  setInlineError(apiKeyError, "");
  try {
    const result = await apiRequest("/api/settings/api-key", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        api_key: apiKey,
        save_on_computer: saveApiKey.checked,
      }),
    });
    if (saveApiKey.checked) {
      state.sessionApiKey = "";
      geminiApiKey.value = "";
      updateApiKeyStatus(
        Boolean(result?.api_key_configured ?? true),
        Boolean(result?.saved_on_computer ?? true),
      );
      settingsMessage.textContent = "API key saved on this computer.";
    } else {
      state.sessionApiKey = apiKey;
      updateApiKeyStatus(
        Boolean(result?.api_key_configured ?? state.apiKeyConfigured),
        Boolean(result?.saved_on_computer ?? state.apiKeySaved),
      );
      settingsMessage.textContent = "API key kept for this browser session.";
    }
  } catch (error) {
    setInlineError(apiKeyError, error.message);
    showGlobalError(error.message);
  } finally {
    saveSettingsButton.disabled = false;
  }
}

async function clearSavedApiKey() {
  clearGlobalError();
  settingsMessage.textContent = "";
  clearSavedKeyButton.disabled = true;
  try {
    const result = await apiRequest("/api/settings/api-key", { method: "DELETE" });
    updateApiKeyStatus(
      Boolean(result?.api_key_configured),
      Boolean(result?.saved_on_computer),
    );
    settingsMessage.textContent = "Saved API key cleared.";
  } catch (error) {
    setInlineError(apiKeyError, error.message);
    showGlobalError(error.message);
  } finally {
    clearSavedKeyButton.disabled = false;
  }
}

function statusDescription(job) {
  const status = asText(job.status).toLocaleLowerCase();
  const current = Number(job.current_page) || 0;
  const total = Number(job.total_pages) || 0;
  if (status === "queued") {
    return "Waiting for the translator…";
  }
  if (status === "preparing" || status === "ingesting") {
    return "Separating text and images by physical page…";
  }
  if (status === "prepared") {
    return "The pages are prepared. Starting translation…";
  }
  if (status === "translating") {
    return total
      ? `Translating physical page ${Math.min(current, total)} of ${total}…`
      : "Translating the document page by page…";
  }
  if (READY_STATUSES.has(status)) {
    return "Translation complete. Opening the review…";
  }
  if (FAILED_STATUSES.has(status)) {
    return asText(job.error, "Translation stopped before the document was complete.");
  }
  return `${humanize(status)}…`;
}

function renderJobProgress(job) {
  state.job = job;
  const current = Math.max(0, Number(job.current_page) || 0);
  const total = Math.max(0, Number(job.total_pages) || 0);
  const status = asText(job.status, "queued").toLocaleLowerCase();
  progressFilename.textContent = asText(job.filename, state.selectedFile?.name || "PDF");
  progressAnnouncement.textContent = statusDescription(job);
  progressStatus.textContent = humanize(status);
  pageProgress.max = total || 1;
  pageProgress.value = total ? Math.min(current, total) : 0;
  pageProgress.textContent = total ? `${Math.round((current / total) * 100)}%` : "0%";
  progressPages.textContent = total
    ? `${Math.min(current, total)} of ${total} pages`
    : "Waiting for page count";
  progressErrorActions.hidden = !FAILED_STATUSES.has(status);
}

function stopPolling() {
  if (state.pollTimer !== null) {
    window.clearTimeout(state.pollTimer);
    state.pollTimer = null;
  }
}

function statusPollDelay(multiplier = 1) {
  const configured = Number(state.config?.limits?.status_poll_interval_ms);
  const delay = Number.isFinite(configured) && configured > 0 ? configured : 1000;
  return delay * multiplier;
}

async function pollJob(jobId) {
  stopPolling();
  if (jobId !== state.jobId) {
    return;
  }
  try {
    const job = await apiRequest(`/api/jobs/${encodeURIComponent(jobId)}`);
    if (jobId !== state.jobId) {
      return;
    }
    state.pollFailures = 0;
    renderJobProgress(job);
    const status = asText(job.status).toLocaleLowerCase();
    if (READY_STATUSES.has(status)) {
      await loadReview();
      return;
    }
    if (FAILED_STATUSES.has(status)) {
      return;
    }
    state.pollTimer = window.setTimeout(() => pollJob(jobId), statusPollDelay());
  } catch (error) {
    state.pollFailures += 1;
    progressAnnouncement.textContent =
      state.pollFailures > 2
        ? `${error.message} Retrying automatically…`
        : "Waiting for an update from the local translator…";
    state.pollTimer = window.setTimeout(() => pollJob(jobId), statusPollDelay(2));
  }
}

async function startTranslation(event) {
  event.preventDefault();
  clearGlobalError();
  setInlineError(formError, "");
  const file = selectedPdf();
  const validFile = validatePdf(file);
  const glossary = collectGlossary();
  const jobSettings = validateJobSettings();
  if (!validFile || glossary === null || jobSettings === null) {
    setInlineError(formError, "Correct the highlighted fields before starting.");
    if (glossary === null) {
      activateTab("mappings");
    }
    return;
  }

  setStartBusy(true);
  const body = new FormData();
  body.append("pdf", file, file.name);
  body.append(
    "glossary",
    JSON.stringify(
      Object.entries(glossary).map(([sourceTerm, targetTranslation]) => ({
        source_term: sourceTerm,
        target_translation: targetTranslation,
      })),
    ),
  );
  body.append("settings", JSON.stringify(jobSettings.settings));
  if (jobSettings.sessionKey) {
    body.append("gemini_api_key", jobSettings.sessionKey);
  }
  try {
    const job = await apiRequest("/api/jobs", { method: "POST", body });
    if (!job?.job_id) {
      throw new Error("The local server did not return a translation job identifier.");
    }
    state.jobId = asText(job.job_id);
    state.job = {
      ...job,
      filename: job.filename || file.name,
      current_page: job.current_page || 0,
      total_pages: job.total_pages || 0,
    };
    state.review = null;
    reviewTab.disabled = true;
    reviewTab.setAttribute("aria-disabled", "true");
    window.history.replaceState({}, "", `/?job=${encodeURIComponent(state.jobId)}`);
    translationSetup.hidden = true;
    jobProgress.hidden = false;
    activateTab("translate");
    renderJobProgress(state.job);
    await pollJob(state.jobId);
  } catch (error) {
    setInlineError(formError, error.message);
    showGlobalError(error.message);
  } finally {
    setStartBusy(false);
  }
}

function pageDescription(page) {
  const parts = [];
  if (page.pdf_page_label) {
    parts.push(`PDF label ${asText(page.pdf_page_label)}`);
  }
  if (page.detected_printed_page_label) {
    parts.push(`printed ${asText(page.detected_printed_page_label)}`);
  }
  return parts.join(" · ");
}

function makePageLabel(page) {
  const label = createElement(
    "p",
    "page-label",
    `Physical page ${Number(page.original_page_number) || 1}`,
  );
  const details = pageDescription(page);
  if (details) {
    label.append(createElement("span", "", details));
  }
  return label;
}

function reviewStatusClass(status) {
  if (status === "accepted") {
    return "accepted";
  }
  if (status === "needs_work") {
    return "needs-work";
  }
  return "";
}

function unresolvedUncertainties(block) {
  return Array.isArray(block.uncertainties)
    ? block.uncertainties.filter((uncertainty) => !uncertainty.resolved)
    : [];
}

function uncertaintyNeedle(uncertainty) {
  return asText(
    uncertainty.highlight_text ||
      uncertainty.proposed_translation ||
      uncertainty.source_term,
  ).trim();
}

function findAvailableRange(text, needle, occupied) {
  if (!needle) {
    return null;
  }
  let index = text.indexOf(needle);
  while (index !== -1) {
    const start = Array.from(text.slice(0, index)).length;
    const end = start + Array.from(needle).length;
    const overlaps = occupied.some((range) => start < range.end && end > range.start);
    if (!overlaps) {
      return { start, end };
    }
    index = text.indexOf(needle, index + Math.max(needle.length, 1));
  }

  const lowerText = text.toLocaleLowerCase();
  const lowerNeedle = needle.toLocaleLowerCase();
  if (lowerNeedle.length !== needle.length) {
    return null;
  }
  index = lowerText.indexOf(lowerNeedle);
  while (index !== -1) {
    const start = Array.from(text.slice(0, index)).length;
    const end = start + Array.from(text.slice(index, index + needle.length)).length;
    const overlaps = occupied.some((range) => start < range.end && end > range.start);
    if (!overlaps) {
      return { start, end };
    }
    index = lowerText.indexOf(lowerNeedle, index + Math.max(needle.length, 1));
  }
  return null;
}

function activateUncertaintyFromEvent(event, uncertaintyId) {
  event.preventDefault();
  event.stopPropagation();
  openUncertainty(uncertaintyId);
}

function makeUncertaintyMark(text, entry) {
  const mark = createElement("mark", "uncertainty-mark", text);
  mark.dataset.testid = "uncertainty-highlight";
  mark.dataset.uncertaintyId = asText(entry.uncertainty.uncertainty_id);
  mark.tabIndex = 0;
  mark.setAttribute("role", "button");
  mark.setAttribute("contenteditable", "false");
  mark.setAttribute(
    "aria-label",
    `Uncertain translation: ${text}. Open reviewer options.`,
  );
  mark.addEventListener("click", (event) =>
    activateUncertaintyFromEvent(event, entry.uncertainty.uncertainty_id),
  );
  mark.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      activateUncertaintyFromEvent(event, entry.uncertainty.uncertainty_id);
    }
  });
  return mark;
}

function renderHighlightedText(container, text, block) {
  const codePoints = Array.from(text);
  const ranges = [];
  const handled = new Set();
  for (const uncertainty of unresolvedUncertainties(block)) {
    const entry = state.uncertaintyIndex.get(asText(uncertainty.uncertainty_id));
    if (!entry) {
      continue;
    }
    let range = null;
    const startOffset = Number(uncertainty.start_offset);
    const endOffset = Number(uncertainty.end_offset);
    const offsetText =
      Number.isInteger(startOffset) &&
      Number.isInteger(endOffset) &&
      startOffset >= 0 &&
      endOffset > startOffset &&
      endOffset <= codePoints.length
        ? codePoints.slice(startOffset, endOffset).join("")
        : "";
    if (
      uncertainty.highlight_mode === "range" &&
      offsetText === uncertaintyNeedle(uncertainty)
    ) {
      const overlaps = ranges.some(
        (existing) => startOffset < existing.end && endOffset > existing.start,
      );
      range = overlaps ? null : { start: startOffset, end: endOffset };
    } else if (uncertainty.highlight_mode !== "block") {
      range = findAvailableRange(text, uncertaintyNeedle(uncertainty), ranges);
    }
    if (range) {
      ranges.push({ ...range, entry });
      handled.add(asText(uncertainty.uncertainty_id));
    }
  }

  ranges.sort((left, right) => left.start - right.start);
  let cursor = 0;
  for (const range of ranges) {
    if (range.start > cursor) {
      container.append(
        document.createTextNode(codePoints.slice(cursor, range.start).join("")),
      );
    }
    container.append(
      makeUncertaintyMark(
        codePoints.slice(range.start, range.end).join(""),
        range.entry,
      ),
    );
    cursor = range.end;
  }
  if (cursor < codePoints.length || codePoints.length === 0) {
    container.append(document.createTextNode(codePoints.slice(cursor).join("")));
  }
  return handled;
}

function insertPlainTextAtSelection(editor, text) {
  const selection = window.getSelection();
  if (!selection || selection.rangeCount === 0) {
    editor.append(document.createTextNode(text));
    return;
  }
  const range = selection.getRangeAt(0);
  if (!editor.contains(range.commonAncestorContainer)) {
    editor.append(document.createTextNode(text));
    return;
  }
  range.deleteContents();
  const node = document.createTextNode(text);
  range.insertNode(node);
  range.setStartAfter(node);
  range.collapse(true);
  selection.removeAllRanges();
  selection.addRange(range);
}

function setBlockDirty(blockElement, editor, machineText) {
  const dirty = (editor.textContent || "") !== machineText;
  blockElement.classList.toggle("is-dirty", dirty);
  editor.setAttribute("aria-invalid", "false");
}

function makeSourceBlock(block, page) {
  const article = createElement("article", "source-block");
  article.dataset.blockId = asText(block.block_id);
  article.dataset.pageNumber = asText(page.original_page_number);
  const meta = createElement("div", "block-meta");
  meta.append(createElement("span", "", humanize(block.type || "text")));
  const text = createElement("p", "", asText(block.source_text));
  article.append(meta, text);
  state.sourceElements.set(asText(block.block_id), article);
  return article;
}

function updateBlockStatus(blockElement, block) {
  const statusElement = blockElement.querySelector(".status");
  if (!statusElement) {
    return;
  }
  const status = asText(block.review_status, "unreviewed");
  statusElement.textContent = humanize(status);
  statusElement.className = `status ${reviewStatusClass(status)}`.trim();
}

function makeTranslationBlock(block, page, draftText) {
  const article = createElement("article", "translation-block");
  article.dataset.blockId = asText(block.block_id);
  article.dataset.pageNumber = asText(page.original_page_number);
  article.dataset.baseRevision = asText(block.base_revision, "0");

  const meta = createElement("div", "block-meta");
  meta.append(createElement("span", "", humanize(block.type || "text")));
  const status = createElement(
    "span",
    `status ${reviewStatusClass(asText(block.review_status))}`.trim(),
    humanize(block.review_status || "unreviewed"),
  );
  meta.append(status);

  const editor = createElement("div", "translated-editor");
  editor.dataset.testid = "translated-block";
  editor.contentEditable = "true";
  editor.spellcheck = true;
  editor.setAttribute("role", "textbox");
  editor.setAttribute("aria-multiline", "true");
  editor.setAttribute(
    "aria-label",
    `Translation for physical page ${asText(page.original_page_number)}, ${humanize(
      block.type || "text",
    )}`,
  );
  const effectiveText = asText(block.effective_text, asText(block.machine_text));
  const displayedText = draftText === undefined ? effectiveText : draftText;
  const handled = renderHighlightedText(editor, displayedText, block);

  editor.addEventListener("input", () => setBlockDirty(article, editor, effectiveText));
  editor.addEventListener("paste", (event) => {
    event.preventDefault();
    insertPlainTextAtSelection(editor, event.clipboardData?.getData("text/plain") || "");
    editor.dispatchEvent(new Event("input", { bubbles: true }));
  });

  const fallbackContainer = createElement("div", "uncertainty-fallbacks");
  for (const uncertainty of unresolvedUncertainties(block)) {
    const uncertaintyId = asText(uncertainty.uncertainty_id);
    if (handled.has(uncertaintyId)) {
      continue;
    }
    const fallback = createElement(
      "button",
      "uncertainty-fallback",
      `Review: ${asText(uncertainty.source_term, "uncertain passage")}`,
    );
    fallback.type = "button";
    fallback.dataset.testid = "uncertainty-highlight";
    fallback.addEventListener("click", (event) =>
      activateUncertaintyFromEvent(event, uncertaintyId),
    );
    fallbackContainer.append(fallback);
  }

  const actions = createElement("div", "block-actions");
  const save = createElement("button", "block-action save-action", "Save");
  save.type = "button";
  save.dataset.testid = "save-block";
  const validate = createElement("button", "block-action validate-action", "Validate");
  validate.type = "button";
  validate.dataset.testid = "validate-block";
  const message = createElement("span", "block-message");
  message.setAttribute("role", "status");
  message.setAttribute("aria-live", "polite");
  save.addEventListener("click", () =>
    saveBlock(block, article, editor, "in_review", [save, validate], message),
  );
  validate.addEventListener("click", () =>
    saveBlock(block, article, editor, "accepted", [save, validate], message),
  );
  actions.append(save, validate, message);

  article.append(meta, editor);
  if (fallbackContainer.childElementCount) {
    article.append(fallbackContainer);
  }
  article.append(actions);
  if (displayedText !== effectiveText) {
    article.classList.add("is-dirty");
  }
  return article;
}

function captureDrafts() {
  const drafts = new Map();
  for (const blockElement of translationContent.querySelectorAll(
    ".translation-block.is-dirty",
  )) {
    const editor = blockElement.querySelector(".translated-editor");
    if (editor) {
      drafts.set(asText(blockElement.dataset.blockId), editor.textContent || "");
    }
  }
  return drafts;
}

function buildReviewIndexes(pages) {
  state.blockIndex = new Map();
  state.uncertaintyIndex = new Map();
  for (const page of pages) {
    for (const block of Array.isArray(page.blocks) ? page.blocks : []) {
      block.base_revision = Math.max(0, Number(block.base_revision) || 0);
      block.effective_text = asText(block.effective_text, asText(block.machine_text));
      block.review_status = asText(block.review_status, "unreviewed");
      block.uncertainties = Array.isArray(block.uncertainties) ? block.uncertainties : [];
      const entry = { block, page };
      state.blockIndex.set(asText(block.block_id), entry);
      block.uncertainties.forEach((uncertainty, index) => {
        const id = asText(uncertainty.uncertainty_id, `${block.block_id}-u${index + 1}`);
        uncertainty.uncertainty_id = id;
        state.uncertaintyIndex.set(id, { ...entry, uncertainty });
      });
    }
  }
}

function updateReviewSummary() {
  if (!state.review) {
    return;
  }
  const blocks = [...state.blockIndex.values()].map((entry) => entry.block);
  const accepted = blocks.filter((block) => block.review_status === "accepted").length;
  const uncertain = blocks.reduce(
    (count, block) => count + unresolvedUncertainties(block).length,
    0,
  );
  const validation =
    blocks.length > 0 ? `${accepted} of ${blocks.length} validated` : "No text blocks";
  reviewProgress.textContent =
    uncertain > 0
      ? `${validation} · ${uncertain} ${uncertain === 1 ? "uncertainty" : "uncertainties"}`
      : `${validation} · no open uncertainties`;
}

function renderReview(drafts = new Map()) {
  sourceContent.replaceChildren();
  translationContent.replaceChildren();
  state.sourceElements = new Map();
  const pages = Array.isArray(state.review?.pages)
    ? [...state.review.pages].sort(
        (left, right) =>
          Number(left.original_page_number) - Number(right.original_page_number),
      )
    : [];
  buildReviewIndexes(pages);

  if (!pages.length) {
    sourceContent.append(createElement("p", "lede", "No source pages were returned."));
    translationContent.append(
      createElement("p", "lede", "No translated pages were returned."),
    );
  }

  for (const page of pages) {
    const sourcePage = createElement("section", "review-page");
    const translatedPage = createElement("section", "review-page");
    const pageNumber = asText(page.original_page_number);
    sourcePage.dataset.pageNumber = pageNumber;
    translatedPage.dataset.pageNumber = pageNumber;
    sourcePage.append(makePageLabel(page));
    translatedPage.append(makePageLabel(page));

    for (const block of Array.isArray(page.blocks) ? page.blocks : []) {
      sourcePage.append(makeSourceBlock(block, page));
      translatedPage.append(
        makeTranslationBlock(block, page, drafts.get(asText(block.block_id))),
      );
    }
    sourceContent.append(sourcePage);
    translationContent.append(translatedPage);
  }

  const filename =
    asText(state.review?.filename) ||
    asText(state.review?.source_file_name) ||
    asText(state.job?.filename) ||
    asText(state.selectedFile?.name, "Translated PDF");
  reviewDocument.textContent = filename;
  document.title = `${filename} · ArticleTranslator`;
  exportLink.href = `/api/jobs/${encodeURIComponent(state.jobId)}/export.md`;
  updateReviewSummary();
  requestAnimationFrame(syncSourceToTranslation);
}

function currentReviewVersion(response, oldVersion) {
  const candidates = [
    response?.current_revision,
    response?.revision,
    response?.version,
    response?.base_revision,
  ];
  for (const candidate of candidates) {
    const value = Number(candidate);
    if (Number.isInteger(value) && value > oldVersion) {
      return value;
    }
  }
  return oldVersion + 1;
}

async function saveBlock(block, blockElement, editor, status, buttons, message) {
  const editorialText = (editor.textContent || "").trim();
  if (!editorialText) {
    editor.setAttribute("aria-invalid", "true");
    message.textContent = "Translation cannot be blank.";
    editor.focus();
    return;
  }

  for (const button of buttons) {
    button.disabled = true;
  }
  blockElement.classList.add("is-saving");
  message.textContent = status === "accepted" ? "Validating…" : "Saving…";
  const oldVersion = Number(block.base_revision) || 0;
  const drafts = captureDrafts();
  drafts.delete(asText(block.block_id));
  const savedScroll = translationScroll.scrollTop;
  try {
    const response = await apiRequest(
      `/api/jobs/${encodeURIComponent(state.jobId)}/revisions`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          block_id: block.block_id,
          editorial_text: editorialText,
          expected_base_revision: oldVersion,
          status,
        }),
      },
    );
    if (Array.isArray(response?.pages)) {
      state.review = response;
      renderReview(drafts);
      requestAnimationFrame(() => {
        translationScroll.scrollTop = savedScroll;
        syncSourceToTranslation();
        const refreshedBlock = [
          ...translationContent.querySelectorAll(".translation-block"),
        ].find((element) => element.dataset.blockId === asText(block.block_id));
        const refreshedMessage = refreshedBlock?.querySelector(".block-message");
        if (refreshedMessage) {
          refreshedMessage.textContent = status === "accepted" ? "Validated" : "Saved";
        }
      });
      return;
    }
    block.effective_text = asText(response?.effective_text, editorialText);
    block.review_status = asText(response?.review_status || response?.status, status);
    block.base_revision = currentReviewVersion(response, oldVersion);
    if (Array.isArray(response?.uncertainties)) {
      block.uncertainties = response.uncertainties;
    }
    blockElement.dataset.baseRevision = asText(block.base_revision);
    editor.replaceChildren();
    renderHighlightedText(editor, block.effective_text, block);
    blockElement.classList.remove("is-dirty");
    editor.setAttribute("aria-invalid", "false");
    updateBlockStatus(blockElement, block);
    updateReviewSummary();
    message.textContent = status === "accepted" ? "Validated" : "Saved";
    window.setTimeout(() => {
      if (message.textContent === "Validated" || message.textContent === "Saved") {
        message.textContent = "";
      }
    }, 2400);
  } catch (error) {
    message.textContent = error.message;
    showGlobalError(error.message);
  } finally {
    for (const button of buttons) {
      button.disabled = false;
    }
    blockElement.classList.remove("is-saving");
  }
}

function relatedUncertainties(entry) {
  const termGroupId = asText(entry.uncertainty.term_group_id);
  if (!termGroupId) {
    return [entry];
  }
  return [...state.uncertaintyIndex.values()].filter(
    (candidate) =>
      !candidate.uncertainty.resolved &&
      asText(candidate.uncertainty.term_group_id) === termGroupId,
  );
}

function expectedVersionsFor(entries) {
  const versions = {};
  for (const entry of entries) {
    versions[asText(entry.block.block_id)] = Number(entry.block.base_revision) || 0;
  }
  return versions;
}

function openUncertainty(uncertaintyId) {
  const entry = state.uncertaintyIndex.get(asText(uncertaintyId));
  if (!entry) {
    showGlobalError("This uncertainty is no longer available. Refresh the review.");
    return;
  }
  clearGlobalError();
  state.activeUncertaintyId = asText(uncertaintyId);
  const uncertainty = entry.uncertainty;
  document.querySelector("#uncertainty-source").textContent = asText(
    uncertainty.source_term,
    "Not supplied",
  );
  document.querySelector("#uncertainty-proposed").textContent = asText(
    uncertainty.proposed_translation,
    "No single suggestion",
  );
  document.querySelector("#uncertainty-reason").textContent = asText(
    uncertainty.reason,
    "The model marked this passage for human review.",
  );
  const alternatives = Array.isArray(uncertainty.alternatives)
    ? uncertainty.alternatives.map(asText).filter(Boolean)
    : [];
  document.querySelector("#alternatives-row").hidden = alternatives.length === 0;
  document.querySelector("#uncertainty-alternatives").textContent = alternatives.join(" · ");
  replacementInput.value = asText(
    uncertainty.proposed_translation,
    uncertaintyNeedle(uncertainty),
  );
  setInlineError(replacementError, "");

  const isFallback = uncertainty.highlight_mode === "block";
  replacementField.hidden = isFallback;
  fallbackInstruction.hidden = !isFallback;
  translateOneButton.hidden = isFallback;
  const related = relatedUncertainties(entry);
  const occurrenceCount = Math.max(
    related.length,
    Number(uncertainty.matching_occurrence_count) || 0,
  );
  const canReplaceAll =
    uncertainty.can_replace_all === true && occurrenceCount > 1;
  translateAllButton.hidden = isFallback || !canReplaceAll;
  translateAllButton.textContent = `Translate All (${occurrenceCount})`;
  translateOneButton.disabled = false;
  translateAllButton.disabled = false;
  uncertaintyDialog.showModal();
  requestAnimationFrame(() => {
    if (isFallback) {
      closeUncertaintyButton.focus();
    } else {
      replacementInput.focus();
      replacementInput.select();
    }
  });
}

function closeUncertainty() {
  state.activeUncertaintyId = null;
  if (uncertaintyDialog.open) {
    uncertaintyDialog.close();
  }
}

async function replaceUncertainty(scope) {
  const entry = state.uncertaintyIndex.get(asText(state.activeUncertaintyId));
  if (!entry) {
    setInlineError(replacementError, "This uncertainty is no longer available.");
    return;
  }
  if (entry.uncertainty.highlight_mode === "block") {
    setInlineError(
      replacementError,
      "Edit this block manually because an exact replacement range is unavailable.",
    );
    return;
  }
  const replacement = replacementInput.value.trim();
  if (!replacement) {
    replacementInput.setAttribute("aria-invalid", "true");
    setInlineError(replacementError, "Enter the translation that should replace this term.");
    replacementInput.focus();
    return;
  }

  replacementInput.removeAttribute("aria-invalid");
  setInlineError(replacementError, "");
  const affectedEntries = scope === "all" ? relatedUncertainties(entry) : [entry];
  const drafts = captureDrafts();
  for (const affected of affectedEntries) {
    drafts.delete(asText(affected.block.block_id));
  }
  const savedScroll = translationScroll.scrollTop;
  translateOneButton.disabled = true;
  translateAllButton.disabled = true;
  try {
    await apiRequest(
      `/api/jobs/${encodeURIComponent(state.jobId)}/uncertainties/${encodeURIComponent(
        entry.uncertainty.uncertainty_id,
      )}/replace`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          replacement,
          scope,
          expected_versions: expectedVersionsFor(affectedEntries),
        }),
      },
    );
    closeUncertainty();
    await loadReview({ drafts, scrollTop: savedScroll, focusBlockId: entry.block.block_id });
  } catch (error) {
    setInlineError(replacementError, error.message);
    showGlobalError(error.message);
  } finally {
    translateOneButton.disabled = false;
    translateAllButton.disabled = false;
  }
}

async function loadReview(options = {}) {
  stopPolling();
  const review = await apiRequest(
    `/api/jobs/${encodeURIComponent(state.jobId)}/review`,
  );
  state.review = review || { pages: [] };
  renderReview(options.drafts);
  reviewTab.disabled = false;
  reviewTab.setAttribute("aria-disabled", "false");
  activateTab("review");
  if (options.scrollTop !== undefined) {
    requestAnimationFrame(() => {
      translationScroll.scrollTop = options.scrollTop;
      syncSourceToTranslation();
      if (options.focusBlockId) {
        const block = [...translationContent.querySelectorAll(".translation-block")].find(
          (element) => element.dataset.blockId === asText(options.focusBlockId),
        );
        block?.querySelector(".translated-editor")?.focus({ preventScroll: true });
      }
    });
  } else {
    sourceScroll.scrollTop = 0;
    translationScroll.scrollTop = 0;
  }
}

function elementPositionInScroller(element, scroller) {
  const elementRect = element.getBoundingClientRect();
  const scrollerRect = scroller.getBoundingClientRect();
  return elementRect.top - scrollerRect.top + scroller.scrollTop;
}

function syncSourceToTranslation() {
  state.scrollFrame = null;
  const translatedPages = [...translationContent.querySelectorAll(".review-page")];
  if (!translatedPages.length) {
    return;
  }
  const scrollTop = translationScroll.scrollTop;
  const anchor = scrollTop + 76;
  let activePage = translatedPages[0];
  for (const page of translatedPages) {
    if (elementPositionInScroller(page, translationScroll) <= anchor) {
      activePage = page;
    } else {
      break;
    }
  }

  const pageNumber = asText(activePage.dataset.pageNumber);
  const pageEntry = state.review?.pages?.find(
    (page) => asText(page.original_page_number) === pageNumber,
  );
  activePageLabel.textContent = pageEntry
    ? `Physical page ${pageNumber}${pageEntry.pdf_page_label ? ` · ${pageEntry.pdf_page_label}` : ""}`
    : `Physical page ${pageNumber}`;

  const blocks = [...activePage.querySelectorAll(".translation-block")];
  let activeBlock = blocks[0] || null;
  for (const block of blocks) {
    if (elementPositionInScroller(block, translationScroll) <= anchor) {
      activeBlock = block;
    } else {
      break;
    }
  }

  for (const sourceBlock of state.sourceElements.values()) {
    sourceBlock.classList.remove("is-following");
  }
  if (activeBlock) {
    const sourceBlock = state.sourceElements.get(asText(activeBlock.dataset.blockId));
    if (sourceBlock) {
      sourceBlock.classList.add("is-following");
      const translatedTop = elementPositionInScroller(activeBlock, translationScroll);
      const localProgress = Math.max(
        0,
        Math.min(1, (anchor - translatedTop) / Math.max(activeBlock.offsetHeight, 1)),
      );
      const sourceTop = elementPositionInScroller(sourceBlock, sourceScroll);
      sourceScroll.scrollTop =
        sourceTop + localProgress * sourceBlock.offsetHeight - Math.min(76, sourceScroll.clientHeight / 4);
      return;
    }
  }

  const sourcePage = [...sourceContent.querySelectorAll(".review-page")].find(
    (page) => page.dataset.pageNumber === pageNumber,
  );
  if (sourcePage) {
    sourceScroll.scrollTop = elementPositionInScroller(sourcePage, sourceScroll);
  }
}

function requestScrollSync() {
  if (state.scrollFrame === null) {
    state.scrollFrame = requestAnimationFrame(syncSourceToTranslation);
  }
}

async function restoreJobFromUrl() {
  const jobId = new URLSearchParams(window.location.search).get("job");
  if (!jobId) {
    return;
  }
  state.jobId = jobId;
  translationSetup.hidden = true;
  jobProgress.hidden = false;
  activateTab("translate");
  progressFilename.textContent = "Restoring translation…";
  try {
    const job = await apiRequest(`/api/jobs/${encodeURIComponent(jobId)}`);
    state.job = job;
    renderJobProgress(job);
    const status = asText(job.status).toLocaleLowerCase();
    if (READY_STATUSES.has(status)) {
      await loadReview();
    } else if (!FAILED_STATUSES.has(status)) {
      await pollJob(jobId);
    }
  } catch (error) {
    translationSetup.hidden = false;
    jobProgress.hidden = true;
    activateTab("translate");
    showGlobalError(error.message);
    window.history.replaceState({}, "", "/");
  }
}

async function initialize() {
  updateMappingEmptyState();
  try {
    const config = await apiRequest("/api/config");
    populateConfig(config || {});
  } catch (error) {
    apiKeyStatus.textContent = "Configuration unavailable.";
    showGlobalError(error.message);
  }
  await restoreJobFromUrl();
}

fileInput.addEventListener("change", () => applyFile(fileInput.files?.[0] || null));
addMappingButton.addEventListener("click", () => addMapping());
form.addEventListener("submit", startTranslation);
dismissAlert.addEventListener("click", clearGlobalError);
translationScroll.addEventListener("scroll", requestScrollSync, { passive: true });
window.addEventListener("resize", requestScrollSync);

for (const eventName of ["dragenter", "dragover"]) {
  dropZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropZone.classList.add("is-dragging");
  });
}
for (const eventName of ["dragleave", "drop"]) {
  dropZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropZone.classList.remove("is-dragging");
  });
}
dropZone.addEventListener("drop", (event) => {
  const file = event.dataTransfer?.files?.[0] || null;
  applyFile(file);
  if (file && typeof DataTransfer !== "undefined") {
    const transfer = new DataTransfer();
    transfer.items.add(file);
    fileInput.files = transfer.files;
  }
});

function resetForNewTranslation() {
  stopPolling();
  state.jobId = null;
  state.job = null;
  state.review = null;
  state.selectedFile = null;
  fileInput.value = "";
  const maxBytes = Number(state.config?.limits?.max_upload_bytes);
  fileDetail.textContent = Number.isFinite(maxBytes)
    ? `No file selected · ${formatBytes(maxBytes)} maximum`
    : "No file selected";
  setInlineError(fileError, "");
  setInlineError(formError, "");
  window.history.replaceState({}, "", "/");
  translationSetup.hidden = false;
  jobProgress.hidden = true;
  reviewTab.disabled = true;
  reviewTab.setAttribute("aria-disabled", "true");
  activateTab("translate");
  document.title = "ArticleTranslator";
}

backToSetupButton.addEventListener("click", resetForNewTranslation);
newTranslationButton.addEventListener("click", resetForNewTranslation);

for (const button of tabButtons) {
  button.addEventListener("click", () => activateTab(button.dataset.tab));
  button.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
      return;
    }
    event.preventDefault();
    const enabledTabs = tabButtons.filter((candidate) => !candidate.disabled);
    const currentIndex = enabledTabs.indexOf(button);
    let nextIndex = currentIndex;
    if (event.key === "Home") {
      nextIndex = 0;
    } else if (event.key === "End") {
      nextIndex = enabledTabs.length - 1;
    } else {
      const direction = event.key === "ArrowRight" ? 1 : -1;
      nextIndex = (currentIndex + direction + enabledTabs.length) % enabledTabs.length;
    }
    const nextTab = enabledTabs[nextIndex];
    activateTab(nextTab.dataset.tab, false);
    nextTab.focus();
  });
}

saveSettingsButton.addEventListener("click", saveSettings);
clearSavedKeyButton.addEventListener("click", clearSavedApiKey);
geminiApiKey.addEventListener("input", () => {
  setInlineError(apiKeyError, "");
  settingsMessage.textContent = "";
  updateApiKeyStatus();
});

closeUncertaintyButton.addEventListener("click", closeUncertainty);
cancelUncertaintyButton.addEventListener("click", closeUncertainty);
translateOneButton.addEventListener("click", () => replaceUncertainty("one"));
translateAllButton.addEventListener("click", () => replaceUncertainty("all"));
uncertaintyForm.addEventListener("submit", (event) => {
  event.preventDefault();
  replaceUncertainty("one");
});
uncertaintyDialog.addEventListener("cancel", () => {
  state.activeUncertaintyId = null;
});

initialize();
