# Design

## Principle
Anything that must be correct or secure is plain, tested Python: eligibility, precedence, access control,
the token budget, permissions, working days, leave rules and the confirmation step. The LLM only plans
(intent, dates, tool arguments, search query; it sees no corpus) and composes (wording, picking the evidence
it used). Must-pass checks therefore hold by construction rather than by prompting.

## Ingestion: tables and the scanned document
`ingestion/loaders.py` turns every format into ordered blocks (heading / text / table):
- **PDF**: pdfplumber gives words with font size and weight. Bold, larger lines become headings, smaller lines
  (running headers and footers) are dropped. `find_tables()` regions become table blocks in reading order.
- **Scanned PDF**: a page with fewer than 20 extracted characters is rendered at 300 dpi (PyMuPDF), binarised and
  read with **Tesseract** (`ocr.py`). Tesseract is installed in the Docker image. Chunks from OCR pages get a
  `p. N` locator.
- **DOCX**: body elements are walked in order so tables stay in their section. Heading/Title styles mark headings.
- **HTML / Markdown**: h1-h6 or `#` become headings; tables are parsed into rows.
- The header table ("Document ID / Version / ...") duplicates the manifest and is dropped.
- **Tables** are rendered row by row as `Header: value | Header: value`, so each row stands alone after splitting.
- **Holidays** (`holidays.py`): for `calendar` documents, any table row with a parseable date becomes a row in a new
  `extracted_holidays` table. A section or column labelled restricted/optional/floating marks optional holidays,
  which are not closures. OCR text lines are a fallback. Nothing from the corpus is hard-coded.

## Chunking and retrieval
- One chunk per section (split at about 350 tokens), prefixed with `[title] heading` and carrying a locator: the
  nearest heading, or `p. N` when a document has no headings. HR documents are short and sectioned, so a section
  is the natural unit for both an answer and a citation.
- **Hybrid search** (`retrieval/index.py`): BM25 for exact terms (holiday names, "carry forward") plus local CPU
  embeddings (bge-small via fastembed, baked into the image), fused with reciprocal rank. Search is exact over a
  NumPy matrix because the corpus is small.
- **Access before scoring** (`retriever.retrieve`): only chunks of documents the requester's role and region can
  read are candidates, so a restricted chunk can never be retrieved or reach the LLM.
- **Budget** (`retrieval/budget.py`): evidence is packed until the rendered block (text plus metadata labels) reaches
  3,000 cl100k_base tokens, with about 600 reserved for upcoming documents. `llm/provider.py` refuses any call over
  the budget and logs `corpus_tokens` for every call. Conversation history holds answers only, never chunks.

## Section 5 rules (`retrieval/policy.py`)
- `is_eligible` = role can read the access level, plus a region match, plus effective date on or before the
  reference date. The reference date is today unless the planner extracts a past date.
- `precedence_key` = (authority, scope, -effective date). `precedence_ranks` gives a dense rank, so equal keys
  share a rank: an unresolved conflict.
- Each passage reaches the composer with its rank and status (IN FORCE / UPCOMING). The instructions: use the
  lowest rank; report equal-rank disagreements as a conflict and cite both; mention an UPCOMING passage only if
  it ranks above the in-force winner. Future documents for other regions or restricted levels are never
  retrieved. Tests cover the task's worked example.

## Where access control is enforced
| Concern | File / function |
|---|---|
| Identity, 401, 403 session ownership | `api/main.py: chat`, `orchestrator/sessions.py: SessionStore.get_or_create` |
| Documents (never sent to the LLM) | `retrieval/policy.py: is_readable`, `retrieval/retriever.py: retrieve` (filters before `SearchIndex.search`) |
| Citations limited to retrieved evidence | `orchestrator/agent.py: _compose` |
| Leave data per role | `tools/leave_tools.py: can_view_employee`, `_require_view`, `get_team_leave` |
| Own-only writes | `tools/leave_tools.py: validate_apply`, `validate_cancel` (use `RequestContext` from the header) |
| Bulk requests | `orchestrator/guardrails.py: is_bulk_request`, `agent.py: _handle_read` (more than one other employee) |
| Writes only after confirmation | `agent.py: handle_message` / `_execute_pending` (proposal stored in session) |

Not-allowed and not-found both raise `PermissionDenied` with one fixed message.

## Personal data
- **Logs**: structured events with IDs, counts and statuses only (no message text). A `RedactingFilter` on every
  handler replaces any name, email or phone found in the employees table with the employee ID, as a safety net.
- **Prompts**: the planner gets the requester's ID, role, region and direct-report IDs; the composer gets ID, role,
  region, join date and level. Tool results contain no names, emails or phones.
- **Answers**: `PII.sanitize_answer` removes phones, masks employee emails (`r***@domain`) and withholds the names of
  employees the requester cannot see.

## Failure modes found
| Found | Status |
|---|---|
| Restricted optional holidays would have reduced working days | Fixed: kind detected from section/column label |
| Markdown header table not detected (has a "Field/Value" row first) | Fixed |
| Running PDF headers polluting chunks | Fixed: font-size filter |
| Prompt-injection text in a wiki page | Planner never sees corpus text; composer told to ignore it; writes need user confirmation |
| Out-of-date FAQ and wiki values | Outranked by authority |
| Composer citing ids it was not given | Dropped in `_compose`; test |
| Two LLM calls versus the 30 s limit | 12 s timeout, no retries, effort low |
| Conflict detection depends on the LLM | Open (see README limitations) |
| No real-LLM end-to-end run yet | Open |

## At 10,000 documents
What breaks first: **startup ingestion and the in-memory index**. Parsing, OCR and embedding everything at
every start would take far longer than the 10-minute limit, and exact search plus a Python-side access filter
over ~500k chunks grows linearly. Fix: offline, incremental ingestion keyed by file hash (cache parsed blocks,
OCR and embeddings); a persistent vector store with **metadata filtering inside the query** (Qdrant or pgvector,
filtering on access level, regions and effective date so the security property is kept); BM25 in the same engine
or OpenSearch; a cross-encoder reranker on the top 50; and per-document version groups so superseded versions do
not crowd out the top-k. Holiday extraction and precedence stay the same.
