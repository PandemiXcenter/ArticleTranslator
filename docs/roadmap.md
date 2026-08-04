# Roadmap

ArticleTranslator now has a working backend, immutable editorial layer, and a
minimal local FastAPI workbench. The remaining roadmap hardens those components
without presenting the current loopback tool as a production, remote, or
multi-user service.

## Phase 1 — backend contracts and local execution

Implemented:

- `uv` project, lockfile, package entry point, lint/type/test tooling;
- strict TOML defaults and limits for every non-secret behavior;
- API-key-only `.env`;
- versioned Pydantic machine and provider payloads;
- page-preserving MarkItDown extraction and matching PNG rendering;
- structured Gemini image+Markdown adapter;
- qualitative uncertain-term output;
- immutable translation runs, per-page atomic checkpoints, and safe resume;
- canonical JSON dataset and deterministic machine Markdown compiler;
- unit, local-integration, and fake-provider end-to-end tests.

Still required before production claims:

- verify live Gemini behavior only through an explicit, opt-in live exercise;
- evaluate representative born-digital and scanned historical PDFs;
- improve provider error classification and privacy-safe telemetry;
- test very large, malformed, rotated, and password-protected PDFs;
- decide retention/deletion policy for preparations, images, and completed runs.

The ordinary suite has not contacted Gemini, so live translation quality and
provider-account behavior remain unverified.

## Phase 2 — translation quality controls

Implemented:

- strict per-page structured output with block types and qualitative
  uncertainties;
- configured glossary plus an authoritative per-job Term mappings table;
- per-job input/output languages, style, and allowlisted Gemini model, with TOML
  defaults (Danish to English in the checked-in config);
- prompt/checkpoint versioning for translation-affecting settings.

Deferred:

- evaluate and tune output on a representative corpus of historical Danish
  material;
- reusable named glossary files and document-level terminology packages;
- neighboring-page context that preserves independent page checkpoint semantics;
- source-coverage and duplicate-block diagnostics;
- opt-in schema repair with a strict attempt/cost cap;
- additional provider adapters, added only when a concrete need exists.

## Phase 3 — editorial revision model

Implemented:

- immutable, coexisting translation runs: retry stays in a failed/in-progress
  run, while retranslation starts a new run;
- append-only `BlockRevision` JSON scoped to document, run, and block;
- atomic revision creation, contiguous history validation, and optimistic base
  versions;
- effective review documents that retain machine text unchanged;
- review states and service support for editor/note metadata;
- stable uncertainty highlighting with exact offsets and structured fallback;
- one-occurrence correction and all-occurrence correction only for multiple
  unresolved model-annotated matches;
- reviewed Markdown generated from latest effective revisions without mutating
  canonical machine output;
- revision, conflict, uncertainty, and reproducible export tests.

Deferred:

- block split/merge and block-type correction;
- editor identity entry and a dedicated revision-history screen;
- explicit machine/latest/accepted-only export policy selection;
- multi-editor locking beyond optimistic conflict detection;
- carrying corrections between translation runs, which requires an explicit
  matching and approval design.

## Phase 4 — local application interface

Implemented:

- FastAPI routes for safe public configuration, PDF upload, job progress, review
  projection, block revision, uncertainty replacement, and Markdown download;
- bounded background execution in one process;
- upload size/type/name validation, opaque staging directories, cleanup, CSRF
  checks, no-store headers, and redacted public errors;
- strict per-job language/model/style resolution and model allowlisting;
- optional local API-key persistence behind **Save on this computer**, with only
  boolean key status returned to the browser;
- application services between the HTTP layer and provider/filesystem adapters.

Deferred:

- durable job registry, recovery, discovery, and queue persistence;
- job cancellation and per-document execution locks;
- SQLite or another database adapter after access patterns justify it;
- multi-process workers;
- authentication, authorization, and every remote-deployment concern.

## Phase 5 — colleague web workbench

Implemented:

- plain tabbed Translate, Term mappings, Settings, and Review workspace;
- laptop PDF selection and language direction on job start;
- job progress polling;
- authoritative source-term/required-translation rows;
- allowlisted Gemini model, translation style, and key controls;
- side-by-side original/effective translation blocks grouped by
  `original_page_number`;
- translated-pane-driven source synchronization;
- an active-page review window with two configurable context pages on either
  side, draft-preserving boundary shifts, and delegated generated-control events;
- block editing and validation backed by append-only revisions;
- visible qualitative uncertainty details and conditional Translate One /
  Translate All correction;
- reviewed Markdown download.

Deferred:

- reopening completed jobs after a server restart;
- cancellation and per-page retry controls in the browser;
- filters for unreviewed, uncertain, failed, or changed blocks;
- block type editing, split/merge, revision history, and reviewer identity UI;
- comprehensive browser automation and cross-browser visual verification;
- remote access or deployment.

## Explicitly out of current scope

- pixel-perfect PDF layout reconstruction;
- calibrated probabilities from models that do not expose them;
- silent partial-document compilation;
- arbitrary raw-provider-response storage;
- automatic provider upload without an explicit user action and privacy notice;
- a public product/landing-page presentation;
- exposing the current unauthenticated server beyond loopback.
