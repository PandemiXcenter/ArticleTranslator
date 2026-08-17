# Architecture

## Design goals

The system must preserve physical page provenance, isolate expensive provider
calls, validate everything persisted, resume safely after failure, and expose
application boundaries that both the CLI and the implemented local editor use.

The machine `DocumentTranslation` is canonical for its immutable translation
run. Editorial revisions are append-only. Machine and reviewed exports are
projections, not databases.

## Dependency direction

```text
CLI commands                  browser assets
      \                            |
       \                       FastAPI interface
        \                          /
         shared composition + application use cases
                  /                         \
                 v                           v
         domain contracts                 port protocols
                                               ^
                                               |
                      extraction / Gemini / filesystem / secret adapters
```

- `domain` imports the standard library and Pydantic only.
- `application` depends on domain contracts and port protocols.
- `adapters` own MarkItDown, pypdf, PDFium, Gemini, and filesystem details.
- `composition.py` wires shared concrete adapters.
- `cli.py` owns command inputs and starts the local server; it does not own
  business rules.
- `interfaces/web/` owns HTTP schemas, CSRF/upload handling, JSON projections, and
  the static tabbed interface. It calls application services and never reads
  canonical metadata from Markdown.
- Gemini SDK types never leave `adapters/llm/gemini.py`.
- Browser code never calls Gemini, MarkItDown, or filesystem adapters directly.

## Local web execution

The short launcher constructs the same pipeline and provider adapter used by the
CLI, then starts FastAPI/Uvicorn on the configured loopback host and port. It
prefers the conventional personal config when present and otherwise uses the
checked-in default:

```bash
uv run app
```

The server boundary is intentionally small:

1. `GET /api/config` returns safe non-secret defaults, limits, selectable models,
   and boolean API-key status.
2. `POST /api/jobs` streams a multipart PDF into an opaque staging directory,
   checks configured size limits, a safe `.pdf` display name, and the PDF header,
   then resolves strict per-job languages, model, style, previous-page context,
   image DPI, Auto continue policy, and term mappings from displayed TOML-backed
   defaults.
3. `WebJobManager` records an opaque browser job ID and runs prepare, translate,
   and compile in a bounded `ThreadPoolExecutor`.
4. The browser polls process-local progress. On failure it can continue the same
   active run or dismiss it without deleting checkpoints. When Auto continue is
   selected, the manager retries inline up to `web.auto_continue_attempts`; this
   avoids queuing behind itself when the configured executor has one worker.
   A ready job can be projected through `EditorialService` for review, revisions,
   uncertainty replacement, and reviewed Markdown, plain-text, and locally
   typeset PDF downloads.
5. `GET /api/jobs` lists every validated completed translation run discovered
   under the configured artifact root. Completed-run routes accept the stable
   translation-run ID rather than requiring the browser alias created at upload.
6. The staged upload and any ephemeral backend key are discarded after the
   background run. Durable artifacts remain under the configured artifact root.
7. `DELETE /api/jobs/{translation-run-id}` removes one selected completed run and
   its editorial history after CSRF validation. The manifest run index is updated;
   prepared pages and sibling immutable runs remain available.
8. `GET /api/jobs/recoverable` discovers stopped failed manifests by stable run
   ID. CSRF-protected `POST .../continue` validates saved semantics and resumes
   the same run; `POST .../cancel` dismisses the stopped attempt but retains its
   page artifacts.

The manager scans canonical manifests at startup. It rebuilds the Articles
catalog from completed `document.json` artifacts and rebuilds stopped-job records
from failed active manifests. Both use stable translation-run IDs. Successful
page checkpoints are fingerprint-validated again during continuation, and the
first missing/failed page is the next provider call. The resolved Auto continue
selection and retry bound are operational provenance in the active manifest;
they do not participate in page fingerprints. The submission-time browser
alias, executor queue, and live running progress still exist only in one server
process and are lost on restart. Discovery does not restart work automatically,
make the executor a durable queue, or introduce a database. The server has no
authentication, active-request interruption, multi-process coordination, or
remote deployment contract. `WebConfig.host` accepts loopback addresses only.

Mutating routes require a same-site CSRF cookie/header pair. Responses use
no-store and basic browser hardening headers. These controls reduce accidental
local misuse; they do not turn the interface into a remotely safe multi-user
service.

## Preparation

MarkItDown 0.1.6 processes individual PDF pages internally but joins them with
ordinary blank lines; its `extract_pages=True` keyword is accepted and ignored.
Preparation therefore:

1. copies the source into a short-lived immutable snapshot and hashes it;
2. establishes the physical page count with pypdf;
3. creates an in-memory one-page PDF for every physical page;
4. sends each stream through MarkItDown;
5. renders that same indexed page to PNG with PDFium;
6. stores the Markdown/image pair and hashes in a staging directory;
7. refuses to continue if the independent readers disagree about page count;
8. atomically publishes `prepared/<preparation-id>` and then switches the
   manifest to it before any provider request.

An empty or failed Markdown extraction does not erase the page. Its status and
warning are retained, and the image can still provide the page content.
Failed preparation leaves the prior manifest and preparation untouched. Old
published preparations are retained pending an explicit cleanup policy. A
successful forced preparation preserves the manifest's ordered translation-run
index, resets its active run, and leaves every prior run directory untouched.

## Domain ownership

The language model owns only fields it can observe or infer:

```text
block reading order
block type
translated-region source-text segmentation and translated text
heading level
visibly printed page label
qualitative uncertainty terms/reasons/alternatives
segment continuation and classification-review flags
target-language GFM structure for tagged tables in the second pass
```

The pipeline owns trusted provenance:

```text
document/job identity
physical original page number
block IDs
PDF page-label metadata
artifact paths and hashes
provider/model and response identity
main/table prompt and schema versions
token usage and timestamps
first-pass and optional table-pass fingerprints
```

The provider-facing `GeneratedPagePayload` is intentionally smaller than
`PageTranslation`. Pydantic forbids unknown fields and validates contiguous block
order before the application adds trusted metadata. The separate
`GeneratedTablePayload` contains exactly one result for each tagged current-page
table order. It cannot provide trusted identity or claim exact cell-level source
text.

## Page-number meanings

Never overload the phrase “page number”:

- `original_page_number`: 1-based physical position in the source PDF;
- `pdf_page_label`: optional label embedded in the PDF page tree;
- `detected_printed_page_label`: optional label visibly printed and detected by
  the model;
- a block of type `page_number`: printed page-number text retained with the other
  blocks, normally hidden from clean Markdown.

## Translation and uncertainty

Every request contains exactly one current-page PNG, that page's complete
Markdown, and the fully resolved translation settings. The primary pass uses
`translate-page-v8`. When that pass tags at least one table or table-like region,
the pipeline immediately makes one additional batched request for that page using
`reconstruct-tables-v1`; it sends the same PNG and complete page MarkItDown/OCR,
plus the first-pass segmentation and exact table targets. Multiple table regions
on one page do not create multiple follow-ups.

Both prompts treat delimited material as untrusted document data and use Gemini
structured output with their respective Pydantic payload schemas. The Gemini
adapter sends each schema through the SDK's JSON Schema field and revalidates
returned text locally; it does not depend on the legacy provider-schema field.
The ordinary suite mocks both calls; neither the live primary request nor the
live table follow-up has been verified against a Gemini account.

Configured glossary entries and the per-job Term mappings table are merged into
the resolved `TranslationSettings`. The prompt states that these source-to-target
mappings are authoritative; they are not suggestions inferred by the model.
Input language, output language, model, and style can be selected for one web
job. Their initial values and the selectable model allowlist come from TOML, and
the resolved values participate in run provenance and checkpoint identity.
The same boundary accepts optional user-authored `footnote_appearance_instructions`
describing likely placement, typography, rules, or markers in simple terms. The
prompt treats this as an inspection hint while keeping the page image authoritative;
the model's per-note appearance field records observed evidence rather than copying
the hint.

### Previous-page continuity context

`translation.previous_page_context_count` selects 0–10 finalized preceding pages
from the same translation run; the checked-in default is 2. Both the primary and
table prompts receive the same read-only projection. It contains physical page
identity, labels, and ordered block type/source/machine translation/segment
metadata. It deliberately excludes previous page images, editorial revisions,
provider metadata, block IDs, hashes, token counts, and timestamps. The current
page's full image and Markdown remain the only raw page evidence in each request.

The projection is visibly delimited and the prompt requires current-page-only
output: prior content must not be repeated, revised, or moved. Every current
`body` block carries the required `paragraph_continuation` state; non-body text
uses null. The next page decides whether its first body block continues the
preceding page's final body block. When confirmed, the pipeline—not the model—adds
`continues_from_block_id` using trusted block identity. Only the first main-flow
body can continue from a prior page and only the last main-flow body can remain
unfinished at the next boundary.

Canonical fragments remain page-local, independently checkpointed, and separately
revisable. The compiler follows the trusted link and emits both fragments as one
Markdown paragraph. With page comments enabled, the new physical-page marker is
inline at the join and names the linked prior block. The current page can confirm
continuity but cannot retroactively revise the already persisted prior fragment.
Because prior finalized output is embedded in the exact prompt, context content
and window size participate in checkpoint identity while page artifacts remain
independently serializable and resumable.

### Historical-page segment policy

The structured response preserves one ordered block for each meaningful page
region. A table or table-like region is a region whose meaning depends on
two-dimensional row and column alignment. This includes ruled tables as well as
unruled schedules, registers, matrices, statistical arrays, and aligned date,
age, month, or count series. Ordinary prose laid out in columns is not a table.

The primary pass must not transcribe or translate the cells of a table or
table-like region. It emits an ordered text-free tag with the applicable reason
and continuation metadata instead. This decision is regional, not page-wide:
prose, headings, captions, and table notes surrounding the table are separate
translated blocks in their actual reading order.

The dedicated follow-up returns target-language GitHub-flavored Markdown for
every tag on that page. It may use bounded structural modernization to infer
strongly supported headers, fill down implied labels, expand ditto marks and
shorthand, transpose/reorder columns, or turn sentence-like records into modern
rows. That flexibility never permits inventing or changing facts, totals, names,
units, dates, categories, or rows. Unreadable values remain explicit and
qualitative uncertainties remain structured. Each completed table block has
`segment_handling="table_reconstruction"`, retains its tag reason and
continuation, contains machine Markdown in `translated_text`, and leaves
`source_text` null because it is not represented as an exact cell transcription.
A continued table still produces one page-local table per physical page.
The compiler emits each reconstructed or reviewer-entered table exactly at its
ordered block position between surrounding paragraphs. An invisible
`table-placement: [H!]` comment records here-placement, physical page, and block
identity without wrapping the GFM table in a floating layout construct.

Figures are not sent through the table follow-up. They remain ordered, text-free
manual-insertion blocks for the reviewer.

Footnotes are classified by document function rather than position, font size,
marker presence, or proportion of the page. Each note carries a semantic ID
`fn-p<starting-page>-n<sequence>`, optional exact printed-reference evidence, and
required `appearance` and `handling` descriptions. A starting fragment places an
ID-bearing control token at its exact translated entrypoint. The pipeline removes
the token, records its owner and Unicode offset, and requires an incoming fragment
to reuse the immediately preceding fragment's identity. Canonical continuation
links preserve page-local serialization while exporters merge same-ID text in
physical-page order. Ambiguous type, ownership, and continuation remain explicit
review work rather than guesses. Running headers, page numbers, catchwords,
digitizer watermarks, and printer gathering signatures stay outside this category.

Uncertainty is not a confidence probability. The initial contract records:

- exact source term;
- proposed translation, when available;
- qualitative reason;
- alternatives.

This is useful to an editor without pretending an uncalibrated model self-report
is a probability.

The editorial service derives stable uncertainty occurrence IDs and exact Unicode
code-point offsets when a proposed translation can be located in effective text.
Replacement uses only those annotated offsets. “Translate All” groups unresolved
occurrences by the structured term identity and is allowed only when more than
one annotated match exists; it never performs unrestricted global string
replacement. If an uncertainty cannot be aligned, the review projection retains
its reason and alternatives as a whole-block fallback rather than inventing an
offset. The web projection additionally groups every unresolved highlight and
fallback by structured term identity, sorts groups by descending occurrence
count with deterministic textual tie-breakers, and exposes the first occurrence
and physical pages for the Review list.

## Checkpoints and failure behavior

The primary page fingerprint includes:

- Markdown artifact hash;
- image artifact hash;
- complete translation-settings snapshot;
- provider, model, and output-affecting semantic provider configuration;
- actual main prompt hash, including the selected previous-page projection;
- main prompt version plus the table prompt contract hash/version;
- persisted schema version.

The optional table-pass fingerprint includes the same source/settings/provider
inputs, its exact prompt, the primary fingerprint, and the ordered table-target
contract. `ProviderMetadata` on the page records primary prompt/response/token
provenance; `TableReconstructionMetadata` records the second prompt version,
response/tokens, target block IDs, timestamp, and table fingerprint.

Any change invalidates reuse. Without `--force`, stale translation data stops
visibly. Image DPI is handled earlier at the preparation boundary: changing it
automatically publishes a newly rendered preparation, preserves prior immutable
run IDs, and clears the active run so the next translation cannot reuse the old
image fingerprints. The first translation appends a UUID run ID to the manifest and makes it active. A
validated primary result is written atomically to the page's `translation.json`
before a required table call. While it contains current-schema table tags it is
an intermediate stage checkpoint and cannot enter the completed document. The
completed table pass atomically replaces it with reconstructed blocks and
second-pass provenance.

If table reconstruction fails, the page's `failure.json` records stage
`table_reconstruction`; retry reads the matching intermediate
`translation.json` and repeats only pass two. It does not rerun or potentially
change the first-pass segmentation. Other page failures use stage
`page_translation`. In either case the manifest enters `failed` and earlier
finalized pages remain resumable in the same run. Gemini SDK failures are reduced
at the adapter boundary to an HTTP code, a validated canonical status token, and
fixed operator guidance. Raw provider messages, response bodies, page content,
and keys never enter that artifact or the web job status. Successful retry
removes the page failure artifact. Forced translation appends and activates a new
run, leaving older successful run bytes untouched.

The Gemini JSON schema cannot express page-edge position rules such as “only the
first body block may continue from the previous page.” The adapter leaves valid
responses untouched. If Gemini returns a contradictory edge claim, the adapter
removes only the impossible direction, marks the block's classification for
review, and uses `unknown` when no safe direction remains. This prevents the same
semantic validation edge case from repeatedly stopping a run while keeping the
ambiguity visible to editors.

Operational provider settings such as timeouts, retries, and the inline-size
guard are retained as page/manifest provenance but do not invalidate already
successful content. This allows an operator to raise a transport limit and resume
at the failed page. Model, API/schema semantics, prompt, translation policy, and
page inputs do invalidate content.

Compilation requires the canonical document containing every physical page.
There is intentionally no silent partial-success mode.

## Canonical dataset and compiled projections

`runs/<run-id>/output/document.json` contains ordered `PageTranslation` records,
including the shared translation-run ID, page source Markdown, and immutable
machine-produced blocks. Translated text regions retain source and machine text.
Reconstructed table and table-like regions retain their ordered tag metadata,
machine target-language GFM Markdown, and distinct table-pass provenance. Their
`source_text` remains null so the dataset does not claim an unreliable exact
cell transcription. Figure regions retain ordered, text-free manual-insertion
placeholders.

`runs/<run-id>/output/document.tex` is the primary compiled projection. It emits
safe XeLaTeX source directly from canonical blocks, merges cross-page fragments,
and inserts each owned note once as `\footnote{...}` at its trusted owner offset.
The printed reference evidence is not emitted; LaTeX generates the display marker.
Unowned notes remain visibly review-required standalone material.
`runs/<run-id>/output/document.md`
is an additional projection that:

- emits invisible physical-page comments when configured;
- renders titles/headings/lists/quotes/captions/footnotes logically;
- emits reconstructed GFM tables as machine content and placeholders only for
  remaining manual figures or migrated legacy manual tables;
- hides headers, footers, and printed page numbers by default;
- is byte-stable for the same canonical document and export config.

Export config changes do not require another provider call.

## Configuration boundary

`config/default.toml` is the complete source of non-secret runtime defaults,
limits, and selectable choices. Every section and field is required and loaded
into strict nested Pydantic models; there are no hidden Python fallbacks for
omitted file settings. Relative paths resolve from the selected TOML file's
directory. CLI arguments identify the operation and its input; they do not
duplicate translation settings.

The local interface uses those settings as defaults and limits. Explicit
per-job language, model, style, and term-mapping inputs are validated at the HTTP
boundary and applied to a copied runtime config. The default Gemini model must be
present in `selectable_models`; the interface rejects any model outside that
allowlist. `translation.previous_page_context_count` is a required TOML setting
bounded from 0 through 10 and defaults to 2 in the checked-in file. Its resolved
value is persisted with the translation settings and participates in the prompt
and checkpoint fingerprint. `[pdf_export]` selects the constrained local XeLaTeX
engine and compilation timeout used only for reviewed PDF downloads.

`.env` contains only `GEMINI_API_KEY`. The Settings interface labels persistence
as **Save on this computer**. Checked persistence writes the key through a narrow
local secret adapter; unchecked use keeps it ephemeral for the current browser
session/job. The API returns only key-availability booleans and never returns,
prefills, logs, or persists the key in run artifacts. Secrets must never enter
TOML, manifests, logs, tests, prompt snapshots, or stored provider responses.

## Editorial model

Machine output is immutable and coexists in UUID-keyed translation runs. The
manifest keeps an ordered `translation_run_ids` index and a nullable active
`translation_run_id`. A retry completes the active failed/in-progress run;
retranslation creates a new run; compilation reads only the active run.

Editorial correction uses append-only `BlockRevision` records:

```text
machine translated block
          |
          v
append-only editorial revisions
          |
          v
effective reviewed document view
          |
          v
Markdown and other exporters
```

A revision has a stable revision ID, document ID, translation-run ID, target block
ID, expected base revision, edited text, effective section type, optional
footnote-owner block ID and marker offset, review status, editor/note, and
timestamp. Block IDs are unique only within a run, so the composite
`(document_id, translation_run_id, block_id)` is the revision target. Revisions
never carry automatically to a regenerated run. Optimistic base versions prevent
two editors from silently overwriting one another.

The filesystem repository stores each history as:

```text
runs/<translation-run-id>/revisions/<block-id>/<revision-number>.json
```

Creation is atomic and refuses to replace an existing revision. Reading validates
scope, filenames, and contiguous base/revision numbers. `EditorialService`
projects a strict `ReviewDocument` by combining immutable machine text with the
latest valid revision for each block. Revision schema 2.0 adds effective section
type and footnote placement metadata; legacy schema 1.0 text-only revisions remain
readable and inherit their machine block metadata.

Review navigation is stored independently at:

```text
runs/<translation-run-id>/review/position.json
```

This small mutable sidecar contains the document/run scope, the latest
`original_page_number` visited, and its timestamp. It drives **Continue from page
X** but is not canonical translation data and is not revision history.

Reviewed Markdown, plain text, and LaTeX are projected directly from canonical
blocks plus this effective view; exporters never parse a compiled projection to
recover structure. PDF download passes the generated LaTeX through a configured local
XeLaTeX adapter with a timeout and shell escape disabled. Temporary compilation
files are removed after the response bytes are produced. These formats do not
change `runs/<run-id>/output/document.json` or its machine `document.tex` and
`document.md` projections. The
current policy uses the latest effective revision whether its status is
`in_review`, `accepted`, or `needs_work`; an accepted-only export policy is not
implemented.

## Tabbed colleague interface

The browser interface is intentionally operational rather than promotional:

- **Translate** selects a PDF and per-job input/output languages. TOML supplies
  the defaults; the checked-in direction is Danish to English.
- **Term mappings** supplies authoritative archaic or specialist translations
  for the next job.
- **Settings** selects an allowlisted Gemini model, translation style, and key
  persistence behavior.
- **Articles** first presents the filesystem-backed catalog of completed runs,
  editorial progress, a conditional Review/Read action, and the four-format
  export menu. Selecting one uses its stable translation-run ID, loads the complete strict
  review projection, and mounts the full translated document. Only the translated
  pane is user-scrollable. Its current `original_page_number` fetches and displays
  that one physical page's original PNG; the browser does not mount every source
  image. The current page is persisted in the run-scoped position sidecar so a
  later session can continue there. Generated block, uncertainty, editor, and
  mapping controls use stable container-level delegated handlers rather than one
  listener per element. Each block distinguishes immutable machine translation
  from the latest append-only manual revision. Editing and validation append
  revisions. Linked paragraph fragments display their continuation state without
  merging their editors or revision histories. Structured uncertainties are
  highlighted and expose one/all replacement according to the service contract.
  The **Uncertain terms** control opens all unresolved groups in descending
  occurrence order and jumps to the first marked instance. Reviewers can change a
  translated section's effective type. A footnote additionally exposes its owner
  section and Unicode character offset for the inline marker; an unknown owner is
  an explicit review state. Reviewed XeLaTeX source, Markdown, plain text, and
  locally typeset PDF are downloaded from the effective document view.

The interface does not expose artifact paths, raw provider objects, or raw
responses. It does not parse `document.md` to rebuild pages or blocks.

## Deliberate extension points

- New LLM: implement both `PageTranslator` methods and keep the primary/table
  generated payloads unchanged when possible.
- New persistence: implement `ArtifactRepository`.
- New revision persistence: implement `RevisionRepository` and preserve
  append-only/optimistic semantics.
- Improved extraction/rendering: implement `PageExtractor` and keep the 1:1 page
  invariant.
- New export format: project `DocumentTranslation`; do not parse Markdown or
  another derivative format.
- Durable/multi-process execution: replace the process-local submission manager
  behind an application port, then add automatic running-job recovery, active
  cancellation, locking, and queue semantics. Existing completed/failed-run
  discovery does not provide those guarantees.
- Remote use: design authentication, authorization, CSRF/origin policy, encrypted
  secret storage, retention, and deployment explicitly before allowing a
  non-loopback host.
- Broader or forward-looking cross-page context: preserve current-page-only raw
  evidence and output, explicitly version/fingerprint the new projection, and
  design how a future page could safely revise an already finalized prior page.
- Schema evolution: bump `SCHEMA_VERSION` and provide migration or clear
  incompatibility handling.
- Prompt evolution: update the applicable resource, bump `PROMPT_VERSION` and/or
  `TABLE_PROMPT_VERSION`, and add checkpoint-invalidation tests.

New core persisted artifacts use schema version 6.0 because stable footnote
identity, descriptions, cross-page links, ownership, reconstructed-table handling,
and per-pass provenance are canonical. Filesystem reads apply explicit in-memory
schema 2.0 through 5.0 compatibility migrations without rewriting immutable
artifacts. Legacy footnotes retain their text but receive synthetic per-block IDs,
an explicit legacy description, and unknown ownership where applicable; their
cross-page relationships are not guessed. Schema 2.0 translated tables
remain marked as legacy translated tables; schema 3.0 manual table placeholders
remain marked as legacy manual
tables. Neither is presented as a schema 6.0 reconstruction or automatically
sent through the new follow-up. Translated figures are rejected because they were
never valid schema 2.0 blocks. Version 1.0 manifests and translations remain
rejected; no run identity is inferred for them.
