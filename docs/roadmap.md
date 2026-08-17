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
- conditional structured table-reconstruction adapter using the same page image
  and complete MarkItDown/OCR;
- qualitative uncertain-term output;
- immutable translation runs, per-page atomic checkpoints, intermediate
  first-pass table checkpoints, and stage-aware safe resume;
- canonical JSON dataset and deterministic machine XeLaTeX and Markdown compilers;
- schema 6.0 footnote-identity/description/ownership and table-reconstruction
  contracts, with read-only schema 2.0–5.0 compatibility migrations;
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
- semantic ID-bearing footnote entrypoint tokens resolved into trusted owner
  block IDs and character offsets, with same-ID next-page fragments linked and
  merged at export and unknown ownership retained for review;
- corpus-informed segment rules: two-dimensional tables and table-like regions
  become ordered text-free first-pass tags, while surrounding prose, captions,
  and notes remain translated;
- one immediate batched second pass per table-bearing page, receiving the same
  PNG and complete page OCR plus first-pass segmentation;
- target-language GFM table reconstruction with bounded modernization of
  sentence-like records, shorthand, repeated labels, and supported headers,
  without permission to invent or alter facts;
- reconstructed table provenance and
  `segment_handling="table_reconstruction"`, with machine Markdown but no claim
  of exact cell-level `source_text`; figures remain manual insertions;
- functional footnote classification with printed-reference provenance, required
  appearance/handling descriptions, and explicit continuation metadata for short,
  full-page, and cross-page notes;
- TOML-defaulted per-job guidance in simple terms for how footnotes appear, used
  as an inspection hint while the rendered page remains decisive;
- required body-paragraph continuation state, next-page confirmation, and
  pipeline-owned links to the preceding page's final body block;
- one-paragraph Markdown projection for confirmed cross-page fragments while
  retaining page-local canonical blocks and append-only revisions;
- exact ordered table emission with an invisible `[H!]` placement anchor;
- TOML-configured context from 0–10 finalized preceding same-run machine page
  translations (default 2), shared by primary and table prompts and constrained
  to current-page-only output;
- configured glossary plus an authoritative per-job Term mappings table;
- per-job input/output languages, style, and allowlisted Gemini model, with TOML
  defaults (Danish to English in the checked-in config);
- distinct main/table prompt versions, hashes, provenance, and checkpoint
  invalidation for translation-affecting settings and context.

Deferred:

- evaluate and tune output on a representative corpus of historical Danish
  material;
- reusable named glossary files and document-level terminology packages;
- optional forward-looking context or a reviewed mechanism for retroactively
  repairing a previous page's trailing fragment;
- source-coverage and duplicate-block diagnostics;
- broader opt-in schema repair with a strict attempt/cost cap; the Gemini adapter
  only repairs impossible page-edge continuation claims into explicit review
  states;
- additional provider adapters, added only when a concrete need exists.

## Phase 3 — editorial revision model

Implemented:

- immutable, coexisting translation runs: retry stays in a failed/in-progress
  run, while retranslation starts a new run;
- append-only `BlockRevision` JSON scoped to document, run, and block;
- atomic revision creation, contiguous history validation, and optimistic base
  versions;
- effective review documents that retain machine text unchanged;
- manual-insertion regions that begin without fabricated machine text and accept
  reviewer content only through append-only revisions (figures and migrated
  legacy manual tables); reconstructed tables instead begin with machine GFM
  Markdown and can be revised through the same append-only mechanism;
- review states and service support for editor/note metadata;
- a run-scoped physical-page review cursor stored outside canonical machine data
  and revision history;
- stable uncertainty highlighting with exact offsets and structured fallback;
- one-occurrence correction and all-occurrence correction only for multiple
  unresolved model-annotated matches;
- reviewed Markdown, plain text, and LaTeX/PDF generated from latest effective
  revisions without mutating canonical machine output;
- append-only section-type, footnote-owner, and inline-marker editing;
- revision, conflict, uncertainty, and reproducible export tests.

Deferred:

- block split/merge;
- editor identity entry and a dedicated revision-history screen;
- explicit machine/latest/accepted-only export policy selection;
- multi-editor locking beyond optimistic conflict detection;
- carrying corrections between translation runs, which requires an explicit
  matching and approval design.

## Phase 4 — local application interface

Implemented:

- FastAPI routes for safe public configuration, PDF upload, job progress, review
  projection, block revision, uncertainty replacement, and multi-format download;
- startup discovery and validation of completed canonical runs under the artifact
  root, exposed to Articles by stable translation-run ID;
- bounded background execution in one process;
- upload size/type/name validation, opaque staging directories, cleanup, CSRF
  checks, no-store headers, and redacted public errors;
- strict per-job language/model/style resolution and model allowlisting;
- optional local API-key persistence behind **Save on this computer**, with only
  boolean key status returned to the browser;
- application services between the HTTP layer and provider/filesystem adapters.

Deferred:

- durable in-progress job registry, queue persistence, and recovery;
- job cancellation and per-document execution locks;
- SQLite or another database adapter after access patterns justify it;
- multi-process workers;
- authentication, authorization, and every remote-deployment concern.

## Phase 5 — colleague web workbench

Implemented:

- plain tabbed Translate, Term mappings, Settings, and Articles workspace;
- laptop PDF selection and language direction on job start;
- job progress polling;
- failed-run discovery plus Continue/Cancel controls that preserve and revalidate
  successful same-run page checkpoints across browser/server restarts;
- a TOML-defaulted per-job Auto continue switch with a bounded retry count and
  persisted operational provenance;
- authoritative source-term/required-translation rows;
- allowlisted Gemini model, translation style, and key controls;
- side-by-side original/effective translation blocks grouped by
  `original_page_number`;
- an always-available completed-run catalog with **Continue from page X** after a
  browser or server restart;
- the full translated document mounted for review, with translated-pane-driven
  synchronization that fetches and displays only the active original page PNG;
- delegated generated-control events;
- block editing and validation backed by append-only revisions;
- visible provenance distinguishing machine translations (including reconstructed
  tables), reviewed-unchanged blocks, and manually edited revisions;
- visible qualitative uncertainty details and conditional Translate One /
  Translate All correction;
- an Uncertain terms list grouped and ordered by unresolved occurrence count,
  with page locations and first-occurrence navigation;
- conditional Review/Read actions based on accepted-block progress;
- export menus for reviewed XeLaTeX source, Markdown, plain text, and locally
  typeset XeLaTeX PDF;
- section-type correction and footnote owner/marker controls.

Deferred:

- interruption of an active provider request and arbitrary per-page retry;
- filters for unreviewed, uncertain, failed, or changed blocks;
- split/merge, revision history, and reviewer identity UI;
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
