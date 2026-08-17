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
  uncertaintyGroups: [],
  reviewPages: [],
  reviewDrafts: new Map(),
  sourcePageNumber: null,
  allowPositionPersistence: false,
  lastPersistedPage: null,
  positionQueue: Promise.resolve(),
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
const reviewLibrary = document.querySelector("#review-library");
const reviewEditor = document.querySelector("#review-editor");
const reviewList = document.querySelector("#review-list");
const reviewListEmpty = document.querySelector("#review-list-empty");
const refreshTranslationsButton = document.querySelector("#refresh-translations");
const backToTranslationsButton = document.querySelector("#back-to-translations");
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
const continueJobButton = document.querySelector("#continue-job");
const cancelJobButton = document.querySelector("#cancel-job");
const reviewDocument = document.querySelector("#review-document");
const reviewProgress = document.querySelector("#review-progress");
const exportPdfLink = document.querySelector("#export-pdf-link");
const exportLatexLink = document.querySelector("#export-latex-link");
const exportMarkdownLink = document.querySelector("#export-markdown-link");
const exportTextLink = document.querySelector("#export-text-link");
const activePageLabel = document.querySelector("#active-page-label");
const sourceScroll = document.querySelector("#source-scroll");
const translationScroll = document.querySelector("#translation-scroll");
const sourceContent = document.querySelector("#source-content");
const translationContent = document.querySelector("#translation-content");
const sourcePageLabel = document.querySelector("#source-page-label");
const sourcePageImage = document.querySelector("#source-page-image");
const fullSizePageLink = document.querySelector("#full-size-page-link");
const uncertaintyDialog = document.querySelector("#uncertainty-dialog");
const uncertaintyListButton = document.querySelector("#uncertainty-list-button");
const uncertaintyListDialog = document.querySelector("#uncertainty-list-dialog");
const uncertaintyGroupList = document.querySelector("#uncertainty-group-list");
const uncertaintyGroupEmpty = document.querySelector("#uncertainty-group-empty");
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
const jobModelSelect = document.querySelector("#job-model-select");
const jobTranslationStyle = document.querySelector("#job-translation-style");
const previousPageContextCount = document.querySelector("#previous-page-context-count");
const pageImageDpi = document.querySelector("#page-image-dpi");
const autoContinue = document.querySelector("#auto-continue");
const autoContinueHelp = document.querySelector("#auto-continue-help");
const jobSettingsError = document.querySelector("#job-settings-error");
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
const EDITABLE_BLOCK_TYPES = [
  "title",
  "subtitle",
  "byline",
  "heading",
  "body",
  "list_item",
  "quote",
  "caption",
  "footnote",
  "page_number",
  "header",
  "footer",
  "equation",
  "other",
];

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
  remove.dataset.action = "remove-mapping";
  remove.setAttribute("aria-label", source ? `Remove mapping for ${source}` : "Remove mapping");
  actionCell.append(remove);

  row.append(sourceCell, targetCell, actionCell);
  mappingBody.append(row);
  updateMappingEmptyState();
  if (focus) {
    sourceInput.focus();
  }
}

function handleMappingClick(event) {
  const target = event.target instanceof Element ? event.target : null;
  const remove = target?.closest("[data-action='remove-mapping']");
  if (!remove || !mappingBody.contains(remove)) {
    return;
  }
  remove.closest("tr")?.remove();
  setInlineError(mappingError, "");
  updateMappingEmptyState();
}

function handleMappingInput(event) {
  const input = event.target instanceof HTMLInputElement ? event.target : null;
  if (!input || !mappingBody.contains(input)) {
    return;
  }
  input.removeAttribute("aria-invalid");
  setInlineError(mappingError, "");
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
  jobModelSelect.replaceChildren();
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
    for (const select of [modelSelect, jobModelSelect]) {
      const option = createElement("option", "", model.label);
      option.value = model.value;
      option.selected = model.value === configuredModel;
      select.append(option);
    }
  }
  jobTranslationStyle.value = translationStyle.value;
  previousPageContextCount.value = asText(translation.previous_page_context_count, "0");
  pageImageDpi.value = asText(config.extraction?.image_dpi, "150");
  autoContinue.checked = Boolean(config.automation?.auto_continue_default);
  const autoContinueAttempts = Number(config.automation?.auto_continue_attempts) || 1;
  autoContinueHelp.textContent =
    `Automatically retry a failed page up to ${autoContinueAttempts} ` +
    `${autoContinueAttempts === 1 ? "time" : "times"} before stopping.`;

  updateApiKeyStatus(
    Boolean(config.api_key_configured),
    Boolean(config.api_key_saved_on_computer),
  );
  const contextCount = Math.max(
    0,
    Number(translation.previous_page_context_count) || 0,
  );
  const continuityNotice = contextCount
    ? ` Finalized text from up to ${contextCount} previous pages is included for continuity.`
    : " Prior-page continuity context is disabled.";
  document.querySelector("#privacy-note").textContent =
    `${humanize(provider.name || "The configured provider")} receives the current page image ` +
    "and extracted text. Table-bearing pages send that page again for table reconstruction." +
    continuityNotice +
    " Files and review data are stored locally.";

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

  const contextCount = Number(previousPageContextCount.value);
  const imageDpi = Number(pageImageDpi.value);
  previousPageContextCount.removeAttribute("aria-invalid");
  pageImageDpi.removeAttribute("aria-invalid");
  if (!Number.isInteger(contextCount) || contextCount < 0 || contextCount > 10) {
    previousPageContextCount.setAttribute("aria-invalid", "true");
    setInlineError(jobSettingsError, "Previous-page context must be a whole number from 0 to 10.");
    return null;
  }
  if (!Number.isInteger(imageDpi) || imageDpi < 72 || imageDpi > 600) {
    pageImageDpi.setAttribute("aria-invalid", "true");
    setInlineError(jobSettingsError, "Page image resolution must be a whole number from 72 to 600 DPI.");
    return null;
  }
  setInlineError(jobSettingsError, "");

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
      model: jobModelSelect.value,
      source_language: sourceLanguage,
      target_language: targetLanguage,
      style: jobTranslationStyle.value,
      previous_page_context_count: contextCount,
      image_dpi: imageDpi,
      auto_continue: autoContinue.checked,
    },
    sessionKey: useConfiguredApiKey ? "" : enteredKey,
  };
}

function setStartBusy(isBusy) {
  startButton.disabled = isBusy;
  addMappingButton.disabled = isBusy;
  fileInput.disabled = isBusy;
  autoContinue.disabled = isBusy;
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
    ? status === "failed" || status === "cancelled"
      ? `Stopped on page ${Math.min(current, total)} of ${total} · ${Math.max(
          0,
          Math.min(current - 1, total),
        )} completed`
      : `${Math.min(current, total)} of ${total} pages`
    : "Waiting for page count";
  progressErrorActions.hidden = !FAILED_STATUSES.has(status);
  continueJobButton.hidden = !FAILED_STATUSES.has(status);
  cancelJobButton.hidden = status !== "failed";
}

function setStoppedJobActionsBusy(busy) {
  continueJobButton.disabled = busy;
  cancelJobButton.disabled = busy;
  backToSetupButton.disabled = busy;
}

async function continueStoppedJob() {
  if (!state.jobId) {
    return;
  }
  clearGlobalError();
  setStoppedJobActionsBusy(true);
  const key = geminiApiKey.value.trim() || state.sessionApiKey;
  try {
    const job = await apiRequest(
      `/api/jobs/${encodeURIComponent(state.jobId)}/continue`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_key: key || null }),
      },
    );
    state.job = job;
    renderJobProgress(job);
    await pollJob(state.jobId);
  } catch (error) {
    showGlobalError(error.message);
  } finally {
    setStoppedJobActionsBusy(false);
  }
}

async function cancelStoppedJob() {
  if (!state.jobId) {
    return;
  }
  clearGlobalError();
  setStoppedJobActionsBusy(true);
  try {
    const job = await apiRequest(
      `/api/jobs/${encodeURIComponent(state.jobId)}/cancel`,
      { method: "POST" },
    );
    state.job = job;
    renderJobProgress(job);
  } catch (error) {
    showGlobalError(error.message);
  } finally {
    setStoppedJobActionsBusy(false);
  }
}

function handleProgressAction(event) {
  const button = event.target.closest("button[data-action]");
  if (!button || !progressErrorActions.contains(button)) {
    return;
  }
  if (button.dataset.action === "continue-job") {
    void continueStoppedJob();
  } else if (button.dataset.action === "cancel-job") {
    void cancelStoppedJob();
  } else if (button.dataset.action === "new-translation") {
    resetForNewTranslation();
  }
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
  mark.dataset.action = "review-uncertainty";
  mark.dataset.uncertaintyId = asText(entry.uncertainty.uncertainty_id);
  mark.tabIndex = 0;
  mark.setAttribute("role", "button");
  mark.setAttribute("contenteditable", "false");
  mark.setAttribute(
    "aria-label",
    `Uncertain translation: ${text}. Open reviewer options.`,
  );
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

function setBlockDirty(blockElement, editor, effectiveText) {
  const dirty = (editor.textContent || "") !== effectiveText;
  blockElement.classList.toggle("is-dirty", dirty);
  editor.setAttribute("aria-invalid", "false");
  return dirty;
}

function recordEditorDraft(editor) {
  const blockElement = editor.closest(".translation-block");
  const blockId = asText(blockElement?.dataset.blockId);
  const entry = state.blockIndex.get(blockId);
  if (!blockElement || !entry) {
    return;
  }
  const editorialText = editor.textContent || "";
  const effectiveText = asText(entry.block.effective_text, entry.block.machine_text);
  if (setBlockDirty(blockElement, editor, effectiveText)) {
    state.reviewDrafts.set(blockId, editorialText);
  } else {
    state.reviewDrafts.delete(blockId);
  }
}

function handleReviewClick(event) {
  const target = event.target instanceof Element ? event.target : null;
  const actionTarget = target?.closest("[data-action]");
  if (!actionTarget || !translationContent.contains(actionTarget)) {
    return;
  }
  const action = actionTarget.dataset.action;
  if (action === "review-uncertainty") {
    activateUncertaintyFromEvent(event, actionTarget.dataset.uncertaintyId);
    return;
  }
  if (action !== "save-block") {
    return;
  }
  const blockElement = actionTarget.closest(".translation-block");
  const blockId = asText(blockElement?.dataset.blockId);
  const entry = state.blockIndex.get(blockId);
  const editor = blockElement?.querySelector(".translated-editor");
  const message = blockElement?.querySelector(".block-message");
  if (!blockElement || !entry || !editor || !message) {
    return;
  }
  const status = actionTarget.dataset.reviewStatus === "accepted" ? "accepted" : "in_review";
  const buttons = [...blockElement.querySelectorAll("[data-action='save-block']")];
  void saveBlock(entry.block, blockElement, editor, status, buttons, message);
}

function handleReviewKeydown(event) {
  if (event.key !== "Enter" && event.key !== " ") {
    return;
  }
  const target = event.target instanceof Element ? event.target : null;
  const uncertainty = target?.closest("[data-action='review-uncertainty']");
  if (!uncertainty || !translationContent.contains(uncertainty)) {
    return;
  }
  if (uncertainty instanceof HTMLButtonElement) {
    return;
  }
  activateUncertaintyFromEvent(event, uncertainty.dataset.uncertaintyId);
}

function handleReviewInput(event) {
  const target = event.target instanceof Element ? event.target : null;
  const editor = target?.closest(".translated-editor");
  if (editor && translationContent.contains(editor)) {
    recordEditorDraft(editor);
  }
}

function handleReviewControlChange(event) {
  const control =
    event.target instanceof HTMLSelectElement || event.target instanceof HTMLInputElement
      ? event.target
      : null;
  if (!control || !translationContent.contains(control)) {
    return;
  }
  const blockElement = control.closest(".translation-block");
  if (!blockElement) {
    return;
  }
  if (control.dataset.action === "change-section-type") {
    const ownerField = blockElement.querySelector(".footnote-owner-field");
    const anchorField = blockElement.querySelector(".footnote-anchor-field");
    const anchorHelp = blockElement.querySelector(".footnote-anchor-help");
    if (ownerField) {
      ownerField.hidden = control.value !== "footnote";
    }
    if (anchorField) {
      anchorField.hidden = control.value !== "footnote";
    }
    if (anchorHelp) {
      anchorHelp.hidden = control.value !== "footnote";
    }
  }
  if (control.dataset.action === "change-footnote-owner") {
    const anchorInput = blockElement.querySelector(".footnote-anchor-input");
    const ownerEntry = state.blockIndex.get(control.value);
    if (anchorInput instanceof HTMLInputElement) {
      const ownerLength = asText(
        ownerEntry?.block?.effective_text,
        ownerEntry?.block?.machine_text,
      ).length;
      anchorInput.max = asText(ownerLength);
      anchorInput.value = control.value ? asText(ownerLength) : "0";
      anchorInput.disabled = !control.value;
    }
  }
  blockElement.classList.add("is-dirty");
}

function handleReviewPaste(event) {
  const target = event.target instanceof Element ? event.target : null;
  const editor = target?.closest(".translated-editor");
  if (!editor || !translationContent.contains(editor)) {
    return;
  }
  event.preventDefault();
  insertPlainTextAtSelection(editor, event.clipboardData?.getData("text/plain") || "");
  editor.dispatchEvent(new Event("input", { bubbles: true }));
}

function pageImageUrl(pageNumber) {
  return `/api/jobs/${encodeURIComponent(state.jobId)}/pages/${encodeURIComponent(
    pageNumber,
  )}/image`;
}

function showSourcePage(page) {
  const pageNumber = Number(page?.original_page_number) || 1;
  if (state.sourcePageNumber === pageNumber && sourcePageImage.getAttribute("src")) {
    return;
  }
  state.sourcePageNumber = pageNumber;
  const details = pageDescription(page || {});
  sourcePageLabel.replaceChildren(
    document.createTextNode(`Physical page ${pageNumber}`),
  );
  if (details) {
    sourcePageLabel.append(createElement("span", "", details));
  }
  const url = pageImageUrl(pageNumber);
  sourcePageImage.alt = `Original physical page ${pageNumber}`;
  sourcePageImage.src = url;
  fullSizePageLink.href = url;
}

function provenanceLabel(block) {
  const revision = Math.max(0, Number(block.base_revision) || 0);
  const reconstructedTable = block.segment_handling === "table_reconstruction";
  if (block.segment_handling === "manual_insertion" && revision === 0) {
    return "Manual insertion required";
  }
  if (reconstructedTable && revision === 0) {
    return "Machine-reconstructed table";
  }
  if (revision === 0) {
    return "Machine translation";
  }
  const changed = asText(block.effective_text) !== asText(block.machine_text);
  if (reconstructedTable) {
    return changed
      ? `Machine-reconstructed table · manually edited · revision ${revision}`
      : `Machine-reconstructed table · reviewed unchanged · revision ${revision}`;
  }
  return changed ? `Manually edited · revision ${revision}` : `Reviewed unchanged · revision ${revision}`;
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
  const manualInsertion = block.segment_handling === "manual_insertion";
  article.classList.toggle("manual-insertion-block", manualInsertion);
  article.classList.toggle(
    "continued-paragraph-block",
    Boolean(block.continues_from_block_id),
  );

  const meta = createElement("div", "block-meta");
  meta.append(createElement("span", "", humanize(block.type || "text")));
  const provenance = createElement(
    "span",
    Number(block.base_revision) > 0 ? "provenance edited" : "provenance machine",
    provenanceLabel(block),
  );
  meta.append(provenance);
  if (block.continuation) {
    meta.append(createElement("span", "continuation", humanize(block.continuation)));
  }
  if (block.paragraph_continuation) {
    const paragraphLabel = block.continues_from_block_id
      ? `Linked paragraph · ${humanize(block.paragraph_continuation)}`
      : humanize(block.paragraph_continuation);
    meta.append(createElement("span", "paragraph-continuation", paragraphLabel));
  }
  if (block.classification_review_required === true) {
    meta.append(createElement("span", "classification-warning", "Check classification"));
  }
  if (block.footnote_owner_review_required === true) {
    meta.append(createElement("span", "classification-warning", "Choose footnote owner"));
  }
  if (block.footnote_id?.id) {
    const printedReference = block.footnote_id.text
      ? ` · printed reference ${block.footnote_id.text}`
      : " · no visible printed reference";
    meta.append(
      createElement("span", "footnote-identity", `Footnote ${block.footnote_id.id}${printedReference}`),
    );
  }
  if (block.footnote_continues_from_block_id) {
    meta.append(
      createElement(
        "span",
        "footnote-continuation",
        `Continues ${block.footnote_continues_from_block_id}`,
      ),
    );
  }
  const ownedFootnotes = state.reviewPages
    .flatMap((candidatePage) => candidatePage.blocks || [])
    .filter((candidate) => candidate.footnote_owner_block_id === block.block_id);
  if (ownedFootnotes.length) {
    const ownedFootnoteCount = new Set(
      ownedFootnotes.map((candidate) => candidate.footnote_id?.id || candidate.block_id),
    ).size;
    meta.append(
      createElement(
        "span",
        "footnote-owner-badge",
        `Owns ${ownedFootnoteCount} ${ownedFootnoteCount === 1 ? "footnote" : "footnotes"}`,
      ),
    );
  }
  const status = createElement(
    "span",
    `status ${reviewStatusClass(asText(block.review_status))}`.trim(),
    humanize(block.review_status || "unreviewed"),
  );
  meta.append(status);

  const sectionControls = createElement("div", "section-controls");
  const typeLabel = createElement("label", "field-label compact-field", "Section type");
  const typeSelect = createElement("select", "section-type-select");
  typeSelect.dataset.testid = "section-type-select";
  typeSelect.dataset.action = "change-section-type";
  const availableTypes = block.segment_handling === "translate"
    ? EDITABLE_BLOCK_TYPES
    : [asText(block.type)];
  for (const type of availableTypes) {
    const option = createElement("option", "", humanize(type));
    option.value = type;
    option.selected = type === asText(block.type);
    typeSelect.append(option);
  }
  typeSelect.disabled = block.segment_handling !== "translate";
  typeLabel.append(typeSelect);

  const ownerLabel = createElement("label", "field-label compact-field footnote-owner-field", "Footnote owner");
  const ownerSelect = createElement("select", "footnote-owner-select");
  ownerSelect.dataset.testid = "footnote-owner-select";
  ownerSelect.dataset.action = "change-footnote-owner";
  const unknownOwner = createElement("option", "", "Unknown — requires review");
  unknownOwner.value = "";
  ownerSelect.append(unknownOwner);
  for (const candidatePage of state.reviewPages) {
    for (const candidate of Array.isArray(candidatePage.blocks) ? candidatePage.blocks : []) {
      if (
        candidate.block_id === block.block_id ||
        candidate.type === "footnote" ||
        candidate.segment_handling !== "translate"
      ) {
        continue;
      }
      const preview = asText(candidate.effective_text, candidate.machine_text)
        .replace(/\s+/g, " ")
        .slice(0, 58);
      const option = createElement(
        "option",
        "",
        `Page ${candidatePage.original_page_number} · ${humanize(candidate.type)} · ${preview}`,
      );
      option.value = asText(candidate.block_id);
      option.selected = option.value === asText(block.footnote_owner_block_id);
      ownerSelect.append(option);
    }
  }
  ownerLabel.append(ownerSelect);
  ownerLabel.hidden = asText(block.type) !== "footnote";
  const selectedOwnerEntry = state.blockIndex.get(asText(block.footnote_owner_block_id));
  const selectedOwnerLength = asText(
    selectedOwnerEntry?.block?.effective_text,
    selectedOwnerEntry?.block?.machine_text,
  ).length;
  const anchorLabel = createElement(
    "label",
    "field-label compact-field footnote-anchor-field",
    "Marker after character",
  );
  const anchorInput = createElement("input", "footnote-anchor-input");
  anchorInput.type = "number";
  anchorInput.min = "0";
  anchorInput.max = asText(selectedOwnerLength);
  anchorInput.step = "1";
  anchorInput.value = asText(block.footnote_anchor_offset, selectedOwnerLength);
  anchorInput.disabled = !block.footnote_owner_block_id;
  anchorInput.dataset.testid = "footnote-anchor-input";
  anchorInput.dataset.action = "change-footnote-anchor";
  anchorLabel.append(anchorInput);
  anchorLabel.hidden = asText(block.type) !== "footnote";
  sectionControls.append(typeLabel, ownerLabel, anchorLabel);
  sectionControls.append(
    createElement(
      "p",
      "footnote-anchor-help",
      "Use 0 for a marker before the first character; the text length places it at the end.",
    ),
  );
  sectionControls.querySelector(".footnote-anchor-help").hidden =
    asText(block.type) !== "footnote";

  if (block.footnote_description) {
    const description = createElement("details", "footnote-description");
    description.append(createElement("summary", "", "Footnote description"));
    description.append(
      createElement("p", "", `Appearance: ${asText(block.footnote_description.appearance)}`),
      createElement("p", "", `Handling: ${asText(block.footnote_description.handling)}`),
    );
    sectionControls.append(description);
  }

  const editor = createElement("div", "translated-editor");
  editor.dataset.testid = "translated-block";
  editor.contentEditable = "true";
  editor.spellcheck = true;
  editor.setAttribute("role", "textbox");
  editor.setAttribute("aria-multiline", "true");
  editor.setAttribute(
    "aria-label",
    manualInsertion
      ? `Manual insertion for physical page ${asText(page.original_page_number)}, ${humanize(
          block.type || "material",
        )}`
      : `Translation for physical page ${asText(page.original_page_number)}, ${humanize(
          block.type || "text",
        )}`,
  );
  if (manualInsertion) {
    editor.classList.add("manual-insertion-editor");
    editor.dataset.placeholder = `Insert the ${humanize(
      block.manual_insertion_reason || block.type || "material",
    ).toLocaleLowerCase()} manually from the page image.`;
  }
  const effectiveText = asText(block.effective_text, asText(block.machine_text));
  const displayedText = draftText === undefined ? effectiveText : draftText;
  const handled = renderHighlightedText(editor, displayedText, block);

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
    fallback.dataset.action = "review-uncertainty";
    fallback.dataset.uncertaintyId = uncertaintyId;
    fallbackContainer.append(fallback);
  }

  const actions = createElement("div", "block-actions");
  const save = createElement(
    "button",
    "block-action save-action",
    manualInsertion ? "Save insertion" : "Save",
  );
  save.type = "button";
  save.dataset.testid = "save-block";
  save.dataset.action = "save-block";
  save.dataset.reviewStatus = "in_review";
  const validate = createElement("button", "block-action validate-action", "Validate");
  validate.type = "button";
  validate.dataset.testid = "validate-block";
  validate.dataset.action = "save-block";
  validate.dataset.reviewStatus = "accepted";
  const message = createElement("span", "block-message");
  message.setAttribute("role", "status");
  message.setAttribute("aria-live", "polite");
  actions.append(save, validate, message);

  article.append(meta, sectionControls, editor);
  if (
    Number(block.base_revision) > 0 &&
    asText(block.machine_text) &&
    asText(block.effective_text) !== asText(block.machine_text)
  ) {
    const machineDetails = createElement("details", "machine-text-details");
    machineDetails.append(
      createElement(
        "summary",
        "",
        block.segment_handling === "table_reconstruction"
          ? "Show original machine reconstruction"
          : "Show original machine translation",
      ),
      createElement("p", "", asText(block.machine_text)),
    );
    article.append(machineDetails);
  }
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
  for (const blockElement of translationContent.querySelectorAll(".translation-block")) {
    const editor = blockElement.querySelector(".translated-editor");
    const blockId = asText(blockElement.dataset.blockId);
    const entry = state.blockIndex.get(blockId);
    if (!editor || !entry) {
      continue;
    }
    const editorialText = editor.textContent || "";
    if (editorialText !== asText(entry.block.effective_text, entry.block.machine_text)) {
      state.reviewDrafts.set(blockId, editorialText);
    } else {
      state.reviewDrafts.delete(blockId);
    }
  }
  return new Map(state.reviewDrafts);
}

function buildReviewIndexes(pages) {
  state.blockIndex = new Map();
  state.uncertaintyIndex = new Map();
  state.uncertaintyGroups = [];
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

function renderUncertaintyGroupList(groups) {
  uncertaintyGroupList.replaceChildren();
  uncertaintyGroupEmpty.hidden = groups.length > 0;
  for (const group of groups) {
    const item = createElement("article", "uncertainty-group-item");
    item.setAttribute("role", "listitem");
    const heading = createElement("div", "uncertainty-group-heading");
    heading.append(
      createElement("h3", "", asText(group.source_term, "Uncertain passage")),
      createElement(
        "span",
        "uncertainty-count",
        `${Number(group.occurrence_count) || 1} ${
          Number(group.occurrence_count) === 1 ? "occurrence" : "occurrences"
        }`,
      ),
    );
    const details = [];
    if (group.proposed_translation) {
      details.push(`Model: ${asText(group.proposed_translation)}`);
    }
    const pages = Array.isArray(group.page_numbers)
      ? group.page_numbers.map(Number).filter(Number.isInteger)
      : [];
    if (pages.length) {
      details.push(`Physical ${pages.length === 1 ? "page" : "pages"} ${pages.join(", ")}`);
    }
    const open = createElement(
      "button",
      "text-button uncertainty-group-open",
      "Review first occurrence",
    );
    open.type = "button";
    open.dataset.action = "open-uncertainty-group";
    open.dataset.uncertaintyId = asText(group.first_uncertainty_id);
    item.append(heading);
    if (details.length) {
      item.append(createElement("p", "uncertainty-group-details", details.join(" · ")));
    }
    item.append(createElement("p", "uncertainty-group-reason", asText(group.reason)), open);
    uncertaintyGroupList.append(item);
  }
}

function openUncertaintyList() {
  if (!state.uncertaintyGroups.length) {
    return;
  }
  uncertaintyListDialog.showModal();
  requestAnimationFrame(() => {
    uncertaintyGroupList.querySelector("[data-action='open-uncertainty-group']")?.focus();
  });
}

function handleUncertaintyGroupClick(event) {
  const target = event.target instanceof Element ? event.target : null;
  const button = target?.closest("[data-action='open-uncertainty-group']");
  if (!button || !uncertaintyGroupList.contains(button)) {
    return;
  }
  const uncertaintyId = asText(button.dataset.uncertaintyId);
  const entry = state.uncertaintyIndex.get(uncertaintyId);
  if (!entry) {
    showGlobalError("This uncertainty is no longer available. Refresh the review.");
    return;
  }
  uncertaintyListDialog.close();
  const blockElement = [...translationContent.querySelectorAll(".translation-block")].find(
    (element) => element.dataset.blockId === asText(entry.block.block_id),
  );
  if (blockElement) {
    translationScroll.scrollTop = Math.max(
      0,
      elementPositionInScroller(blockElement, translationScroll) - 72,
    );
    syncSourceToTranslation();
  }
  requestAnimationFrame(() => openUncertainty(uncertaintyId));
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
  state.uncertaintyGroups = Array.isArray(state.review?.uncertainty_groups)
    ? [...state.review.uncertainty_groups]
    : [];
  uncertaintyListButton.disabled = state.uncertaintyGroups.length === 0;
  uncertaintyListButton.textContent = `Uncertain terms (${uncertain})`;
  renderUncertaintyGroupList(state.uncertaintyGroups);
  const classificationChecks = blocks.filter(
    (block) => block.classification_review_required === true,
  ).length;
  const validation =
    blocks.length > 0 ? `${accepted} of ${blocks.length} validated` : "No text blocks";
  const uncertaintySummary =
    uncertain > 0
      ? `${validation} · ${uncertain} ${uncertain === 1 ? "uncertainty" : "uncertainties"}`
      : `${validation} · no open uncertainties`;
  reviewProgress.textContent = classificationChecks
    ? `${uncertaintySummary} · ${classificationChecks} classification ${
        classificationChecks === 1 ? "check" : "checks"
      }`
    : uncertaintySummary;
}

function renderedPageElement(container, pageNumber) {
  return [...container.querySelectorAll(".review-page")].find(
    (page) => Number(page.dataset.pageNumber) === Number(pageNumber),
  );
}

function renderAllReviewPages(options = {}) {
  if (state.scrollFrame !== null) {
    cancelAnimationFrame(state.scrollFrame);
    state.scrollFrame = null;
  }
  translationContent.replaceChildren();
  translationContent.dataset.renderedPageCount = asText(state.reviewPages.length);

  if (!state.reviewPages.length) {
    translationContent.append(
      createElement("p", "lede", "No translated pages were returned."),
    );
  }

  for (const page of state.reviewPages) {
    const translatedPage = createElement("section", "review-page");
    const pageNumber = asText(page.original_page_number);
    translatedPage.dataset.pageNumber = pageNumber;
    translatedPage.append(makePageLabel(page));

    for (const block of Array.isArray(page.blocks) ? page.blocks : []) {
      translatedPage.append(
        makeTranslationBlock(
          block,
          page,
          state.reviewDrafts.get(asText(block.block_id)),
        ),
      );
    }
    translationContent.append(translatedPage);
  }

  if (options.resetScroll) {
    sourceScroll.scrollTop = 0;
    translationScroll.scrollTop = 0;
  }

  requestAnimationFrame(() => {
    const requestedPageNumber = Number(
      options.anchorPageNumber ?? options.resumePageNumber,
    );
    if (Number.isInteger(requestedPageNumber)) {
      const anchorPage = renderedPageElement(translationContent, requestedPageNumber);
      if (anchorPage && Number.isFinite(options.anchorViewportOffset)) {
        const currentOffset =
          anchorPage.getBoundingClientRect().top -
          translationScroll.getBoundingClientRect().top;
        translationScroll.scrollTop += currentOffset - options.anchorViewportOffset;
      } else if (anchorPage) {
        translationScroll.scrollTop = elementPositionInScroller(
          anchorPage,
          translationScroll,
        );
      }
    }
    syncSourceToTranslation();
    if (options.focusBlockId) {
      const block = [...translationContent.querySelectorAll(".translation-block")].find(
        (element) => element.dataset.blockId === asText(options.focusBlockId),
      );
      block?.querySelector(".translated-editor")?.focus({ preventScroll: true });
    }
    requestAnimationFrame(() => {
      state.allowPositionPersistence = true;
    });
  });
}

function renderReview(drafts = new Map(), options = {}) {
  state.allowPositionPersistence = false;
  state.reviewDrafts = new Map(drafts);
  state.reviewPages = Array.isArray(state.review?.pages)
    ? [...state.review.pages].sort(
        (left, right) =>
          Number(left.original_page_number) - Number(right.original_page_number),
      )
    : [];
  buildReviewIndexes(state.reviewPages);

  const filename =
    asText(state.review?.filename) ||
    asText(state.review?.source_file_name) ||
    asText(state.job?.filename) ||
    asText(state.selectedFile?.name, "Translated PDF");
  const stableRunId = asText(state.review?.translation_run_id);
  if (/^[0-9a-f]{32}$/.test(stableRunId)) {
    state.jobId = stableRunId;
    window.history.replaceState({}, "", `/?job=${encodeURIComponent(stableRunId)}`);
  }
  reviewLibrary.hidden = true;
  reviewEditor.hidden = false;
  reviewDocument.textContent = filename;
  document.title = `${filename} · ArticleTranslator`;
  configureExportLinks(state.jobId, {
    pdf: exportPdfLink,
    tex: exportLatexLink,
    md: exportMarkdownLink,
    txt: exportTextLink,
  });
  updateReviewSummary();
  const focusedPage = options.focusBlockId
    ? state.blockIndex.get(asText(options.focusBlockId))?.page.original_page_number
    : null;
  renderAllReviewPages({
    ...options,
    resumePageNumber:
      options.centerPageNumber ||
      focusedPage ||
      Number(state.review?.continue_page) ||
      state.reviewPages[0]?.original_page_number,
  });
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
  state.reviewDrafts.delete(asText(block.block_id));
  const pageNumber = Number(blockElement.dataset.pageNumber);
  const anchorPage = renderedPageElement(translationContent, pageNumber);
  const anchorViewportOffset = anchorPage
    ? anchorPage.getBoundingClientRect().top -
      translationScroll.getBoundingClientRect().top
    : null;
  try {
    const selectedType = asText(
      blockElement.querySelector(".section-type-select")?.value,
      block.type,
    );
    const selectedOwner = blockElement.querySelector(".footnote-owner-select")?.value || null;
    const anchorControl = blockElement.querySelector(".footnote-anchor-input");
    const selectedAnchor = anchorControl instanceof HTMLInputElement
      ? Number(anchorControl.value)
      : null;
    const response = await apiRequest(
      `/api/jobs/${encodeURIComponent(state.jobId)}/revisions`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          block_id: block.block_id,
          editorial_text: editorialText,
          type: selectedType,
          footnote_owner_block_id: selectedType === "footnote" ? selectedOwner : null,
          footnote_anchor_offset:
            selectedType === "footnote" && selectedOwner && Number.isInteger(selectedAnchor)
              ? selectedAnchor
              : null,
          expected_base_revision: oldVersion,
          status,
        }),
      },
    );
    if (Array.isArray(response?.pages)) {
      state.review = response;
      renderReview(drafts, {
        centerPageNumber: pageNumber,
        anchorPageNumber: pageNumber,
        anchorViewportOffset,
        focusBlockId: block.block_id,
      });
      requestAnimationFrame(() => {
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
  const pageNumber = Number(entry.page.original_page_number);
  const anchorPage = renderedPageElement(translationContent, pageNumber);
  const anchorViewportOffset = anchorPage
    ? anchorPage.getBoundingClientRect().top -
      translationScroll.getBoundingClientRect().top
    : null;
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
    await loadReview({
      drafts,
      centerPageNumber: pageNumber,
      anchorPageNumber: pageNumber,
      anchorViewportOffset,
      focusBlockId: entry.block.block_id,
    });
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
  state.sourcePageNumber = null;
  state.lastPersistedPage = Number(state.review.continue_page) || null;
  const hasAnchor = options.anchorPageNumber !== undefined;
  renderReview(options.drafts instanceof Map ? options.drafts : new Map(), {
    centerPageNumber: options.centerPageNumber,
    anchorPageNumber: options.anchorPageNumber,
    anchorViewportOffset: options.anchorViewportOffset,
    focusBlockId: options.focusBlockId,
    resetScroll: !hasAnchor,
  });
  activateTab("review");
}

function formatReviewDate(value) {
  const date = new Date(asText(value));
  return Number.isNaN(date.getTime())
    ? ""
    : new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(date);
}

function exportUrl(jobId, format) {
  return `/api/jobs/${encodeURIComponent(jobId)}/export.${format}`;
}

function configureExportLinks(jobId, links) {
  for (const [format, link] of Object.entries(links)) {
    if (link) {
      link.href = jobId ? exportUrl(jobId, format) : "#";
    }
  }
}

function makeExportMenu(jobId) {
  const menu = createElement("details", "export-menu article-export-menu");
  const summary = createElement("summary", "secondary-button compact-control", "Export");
  const options = createElement("div", "export-menu-options");
  options.setAttribute("aria-label", "Export article");
  const formats = [
    ["pdf", "LaTeX PDF (.pdf)"],
    ["tex", "LaTeX source (.tex)"],
    ["md", "Markdown (.md)"],
    ["txt", "Plain text (.txt)"],
  ];
  for (const [format, label] of formats) {
    const link = createElement("a", "", label);
    link.href = exportUrl(jobId, format);
    link.download = "";
    options.append(link);
  }
  menu.append(summary, options);
  return menu;
}

function renderTranslationLibrary(payload) {
  const reviews = Array.isArray(payload) ? payload : payload?.jobs || payload?.translations || [];
  reviewList.replaceChildren();
  reviewListEmpty.hidden = reviews.length > 0;
  for (const review of reviews) {
    const jobId = asText(review.job_id || review.translation_run_id);
    if (!jobId) {
      continue;
    }
    const pageCount = Math.max(1, Number(review.page_count) || 1);
    const continuePage = Math.min(
      pageCount,
      Math.max(1, Number(review.continue_page) || 1),
    );
    const acceptedBlocks = Math.max(0, Number(review.accepted_blocks) || 0);
    const totalBlocks = Math.max(0, Number(review.total_blocks) || 0);
    const reviewComplete = review.review_complete === true;
    const card = createElement("article", "review-list-card");
    card.setAttribute("role", "listitem");
    const summary = createElement("div");
    summary.append(createElement("h2", "", asText(review.filename, "Translated PDF")));
    const details = [`${pageCount} ${pageCount === 1 ? "page" : "pages"}`];
    const updated = formatReviewDate(review.updated_at);
    if (updated) {
      details.push(`Updated ${updated}`);
    }
    details.push(
      reviewComplete
        ? "Review complete"
        : totalBlocks > 0
          ? `${acceptedBlocks} of ${totalBlocks} blocks reviewed`
          : "Review not started",
    );
    summary.append(createElement("p", "", details.join(" · ")));
    const open = createElement(
      "button",
      "primary-button compact-control",
      reviewComplete
        ? "Read"
        : continuePage > 1
          ? `Continue review · page ${continuePage}`
          : "Review",
    );
    open.type = "button";
    open.dataset.action = "open-review";
    open.dataset.jobId = jobId;
    const actions = createElement("div", "review-list-actions");
    const remove = createElement("button", "text-button danger-button", "Delete");
    remove.type = "button";
    remove.dataset.action = "delete-review";
    remove.dataset.jobId = jobId;
    remove.dataset.filename = asText(review.filename, "this article");
    actions.append(open, makeExportMenu(jobId), remove);
    card.append(summary, actions);
    reviewList.append(card);
  }
}

async function loadTranslationLibrary({ activate = false } = {}) {
  try {
    renderTranslationLibrary(await apiRequest("/api/jobs"));
  } catch (error) {
    renderTranslationLibrary([]);
    showGlobalError(error.message);
  }
  if (activate) {
    reviewEditor.hidden = true;
    reviewLibrary.hidden = false;
    reviewDocument.textContent = "";
    activateTab("review");
  }
}

async function openLibraryReview(jobId, button) {
  clearGlobalError();
  if (button) {
    button.disabled = true;
  }
  try {
    state.jobId = jobId;
    state.job = await apiRequest(`/api/jobs/${encodeURIComponent(jobId)}`);
    state.lastPersistedPage = null;
    window.history.replaceState({}, "", `/?job=${encodeURIComponent(jobId)}`);
    await loadReview();
  } catch (error) {
    showGlobalError(error.message);
  } finally {
    if (button) {
      button.disabled = false;
    }
  }
}

function handleReviewListClick(event) {
  const target = event.target instanceof Element ? event.target : null;
  const button = target?.closest("[data-action]");
  if (!button || !reviewList.contains(button)) {
    return;
  }
  if (button.dataset.action === "open-review") {
    void openLibraryReview(asText(button.dataset.jobId), button);
    return;
  }
  if (button.dataset.action === "delete-review") {
    void deleteLibraryReview(button);
  }
}

async function deleteLibraryReview(button) {
  const jobId = asText(button.dataset.jobId);
  const filename = asText(button.dataset.filename, "this article");
  if (!jobId || !window.confirm(`Delete ${filename} and all of its review edits?`)) {
    return;
  }
  clearGlobalError();
  button.disabled = true;
  try {
    await apiRequest(`/api/jobs/${encodeURIComponent(jobId)}`, { method: "DELETE" });
    await loadTranslationLibrary();
  } catch (error) {
    button.disabled = false;
    showGlobalError(error.message);
  }
}

function elementPositionInScroller(element, scroller) {
  const elementRect = element.getBoundingClientRect();
  const scrollerRect = scroller.getBoundingClientRect();
  return elementRect.top - scrollerRect.top + scroller.scrollTop;
}

function persistReviewPosition(pageNumber) {
  if (
    !state.allowPositionPersistence ||
    !Number.isInteger(pageNumber) ||
    state.lastPersistedPage === pageNumber ||
    !state.jobId
  ) {
    return;
  }
  const jobId = state.jobId;
  state.lastPersistedPage = pageNumber;
  state.positionQueue = state.positionQueue
    .catch(() => undefined)
    .then(() =>
      apiRequest(`/api/jobs/${encodeURIComponent(jobId)}/review-position`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ original_page_number: pageNumber }),
      }),
    )
    .catch(() => {
      if (state.jobId === jobId && state.lastPersistedPage === pageNumber) {
        state.lastPersistedPage = null;
      }
    });
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
  if (pageEntry) {
    showSourcePage(pageEntry);
  }
  persistReviewPosition(Number(pageNumber));
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
    try {
      const payload = await apiRequest("/api/jobs/recoverable");
      const recoverable = Array.isArray(payload?.jobs) ? payload.jobs : [];
      if (recoverable.length === 1) {
        const job = recoverable[0];
        state.jobId = asText(job.job_id);
        state.job = job;
        window.history.replaceState({}, "", `/?job=${encodeURIComponent(state.jobId)}`);
        renderJobProgress(job);
        return;
      }
    } catch {
      // The original status error below is more useful than a recovery-list failure.
    }
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
  await loadTranslationLibrary();
  await restoreJobFromUrl();
}

fileInput.addEventListener("change", () => applyFile(fileInput.files?.[0] || null));
addMappingButton.addEventListener("click", () => addMapping());
mappingBody.addEventListener("click", handleMappingClick);
mappingBody.addEventListener("input", handleMappingInput);
form.addEventListener("submit", startTranslation);
modelSelect.addEventListener("change", () => {
  jobModelSelect.value = modelSelect.value;
});
translationStyle.addEventListener("change", () => {
  jobTranslationStyle.value = translationStyle.value;
});
jobModelSelect.addEventListener("change", () => {
  modelSelect.value = jobModelSelect.value;
});
jobTranslationStyle.addEventListener("change", () => {
  translationStyle.value = jobTranslationStyle.value;
});
dismissAlert.addEventListener("click", clearGlobalError);
translationContent.addEventListener("click", handleReviewClick);
translationContent.addEventListener("keydown", handleReviewKeydown);
translationContent.addEventListener("input", handleReviewInput);
translationContent.addEventListener("change", handleReviewControlChange);
translationContent.addEventListener("paste", handleReviewPaste);
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
  state.blockIndex = new Map();
  state.uncertaintyIndex = new Map();
  state.uncertaintyGroups = [];
  state.reviewPages = [];
  state.reviewDrafts = new Map();
  state.sourcePageNumber = null;
  state.allowPositionPersistence = false;
  state.lastPersistedPage = null;
  sourcePageImage.removeAttribute("src");
  fullSizePageLink.href = "#";
  configureExportLinks(null, {
    pdf: exportPdfLink,
    tex: exportLatexLink,
    md: exportMarkdownLink,
    txt: exportTextLink,
  });
  translationContent.replaceChildren();
  uncertaintyGroupList.replaceChildren();
  uncertaintyListButton.disabled = true;
  uncertaintyListButton.textContent = "Uncertain terms (0)";
  if (state.scrollFrame !== null) {
    cancelAnimationFrame(state.scrollFrame);
    state.scrollFrame = null;
  }
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
  reviewEditor.hidden = true;
  reviewLibrary.hidden = false;
  activateTab("translate");
  document.title = "ArticleTranslator";
}

progressErrorActions.addEventListener("click", handleProgressAction);
newTranslationButton.addEventListener("click", () => {
  if (state.reviewDrafts.size && !window.confirm("Discard unsaved block edits?")) {
    return;
  }
  resetForNewTranslation();
});
reviewList.addEventListener("click", handleReviewListClick);
refreshTranslationsButton.addEventListener("click", () => {
  void loadTranslationLibrary({ activate: true });
});
backToTranslationsButton.addEventListener("click", () => {
  if (state.reviewDrafts.size && !window.confirm("Discard unsaved block edits?")) {
    return;
  }
  state.reviewDrafts = new Map();
  window.history.replaceState({}, "", "/");
  void loadTranslationLibrary({ activate: true });
});

for (const button of tabButtons) {
  button.addEventListener("click", () => {
    if (button.dataset.tab === "review" && reviewEditor.hidden) {
      void loadTranslationLibrary({ activate: true });
      return;
    }
    activateTab(button.dataset.tab);
  });
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
uncertaintyListButton.addEventListener("click", openUncertaintyList);
uncertaintyGroupList.addEventListener("click", handleUncertaintyGroupClick);
translateOneButton.addEventListener("click", () => replaceUncertainty("one"));
translateAllButton.addEventListener("click", () => replaceUncertainty("all"));
uncertaintyForm.addEventListener("submit", (event) => {
  event.preventDefault();
  replaceUncertainty("one");
});
uncertaintyDialog.addEventListener("cancel", () => {
  state.activeUncertaintyId = null;
});

window.addEventListener("beforeunload", (event) => {
  if (!state.reviewDrafts.size) {
    return;
  }
  event.preventDefault();
  event.returnValue = "";
});

initialize();
