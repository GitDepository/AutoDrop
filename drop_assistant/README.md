> Version 2.1: Review forms now contain only Name, Reason, and optional Additional info. Evidence is expandable. The synthetic exemption case offers Keep data or Reject exemption & delete. See REVIEWER_GUIDE.md for the current controls; older detailed form instructions below are superseded.

# DROP Assistant — working implementation prototype

A local browser application for an academic California DROP project. It includes a **real official sandbox API adapter**, a **1,000-record synthetic SQLite database**, deterministic hash matching, an experimental machine-learning review assistant, synthetic deletion/opt-out/exemption actions, audit logs, and DROP-format response exports.

**Python 3.10 or later. No packages, paid AI service, Node.js, or internet connection are needed for Demo mode.**

## Start on Windows

1. Extract the ZIP completely.
2. Open the `drop_assistant` folder.
3. Double-click `START_WINDOWS.bat`, or open PowerShell in that folder and run:

```powershell
py -3 app.py
```

4. Open **http://127.0.0.1:8765** in your browser. Keep the terminal open. Stop with Ctrl+C.

On macOS/Linux, use `python3 app.py` instead. If port 8765 is occupied, run `python app.py --port 8766` and open that port.

## Five-minute demo

1. Keep **Demo** selected; click **Load sample requests**, then **Match records**.
2. Expected initial state: 22 ready, 3 review cases, and 1 completed “Not found.”
3. Open **Human review**. Each case shows request/hash evidence, side-by-side fields, source references, uncertainty, and action scope. Select a case from the left queue.
4. For each intended fixture approval, enter reviewer, an approval reason, an evidence reference to `seed.py`, and a rationale. Acknowledge the evidence and scope, then preview and confirm. For the AI case, explicitly accept or deny each suggestion. For the exemption case, supply a basis and retention scope.
5. Click **Execute ready queue**, then download response CSVs from **Reporting & API** and the audit JSON from **Audit trail**.

Read **[REVIEWER_GUIDE.md](REVIEWER_GUIDE.md)** for the exact shared-email walkthrough, AI denial, deferral/dispute, reopening, and upgrade instructions.

With all intended fixture approvals, final work-item outcomes are **23 Deleted, 1 Opted out, 1 Exempted, 1 Not found**, with **976 records remaining** when the AI duplicate is accepted. Denying that suggestion instead leaves 977 records. These outcomes demonstrate synthetic processing, not a legal decision or real-world identity validation.

For a fresh experiment without overwriting earlier evidence:

```powershell
py -3 app.py --data-dir fresh_demo --port 8766
```

Extract upgrades into a new folder; never overwrite your working database with the pristine database in the package. See the reviewer guide for preserving existing work.

## Connect to the actual DROP sandbox API

Official sandbox base URL: `https://api.drop.privacy.ca.gov/sandbox`

1. Sign in to the [official Data Broker Portal](https://databroker.drop.privacy.ca.gov/).
2. Navigate to **SANDBOX ENVIRONMENT → ISSUE SANDBOX API KEY**. Complete the portal's required account setup and list selection. A sandbox key is separate from a production key.
3. Set the key locally before launching the app. Do not paste it into chat, commit it, or put it in screenshots.

PowerShell, with an interactive prompt that avoids putting the literal key in shell command history:

```powershell
$dropSecret = Read-Host "DROP sandbox API key" -AsSecureString
$env:DROP_API_KEY = [System.Net.NetworkCredential]::new('', $dropSecret).Password
py -3 app.py
```

After stopping the app:

```powershell
Remove-Item Env:DROP_API_KEY
Remove-Variable dropSecret
```

macOS/Linux:

```bash
read -s -p 'DROP sandbox API key: ' DROP_API_KEY
export DROP_API_KEY
python3 app.py
# After stopping:
unset DROP_API_KEY
```

4. Select **DROP sandbox** in the application and click **Download from DROP**.
5. If the API is preparing the archive, try again later. Match and process the returned batch, then use **Upload to sandbox** in Reporting & API.
6. Inspect file acceptance in the application. **Queued for validation is not final success.** Check the portal/email for row-level validation and record that evidence using **Record portal validation**.

Implemented official operations:

| Operation | HTTP request |
|---|---|
| Download | `GET /sandbox/data/download` |
| Submit statuses | `POST /sandbox/data/upload` |
| Submit amendments | `POST /sandbox/data/amend` |

The client sends `X-API-KEY`, downloads ZIP/CSV data, and uploads individual CSV files as multipart field `files`. It handles preparation/no-data responses, authentication errors, rate limits, partial file acceptance, and unknown upload delivery. GET retries are bounded. POST retries require reconciliation because a timeout does not prove the server rejected a request. File suffixes avoid duplicate-name rejection.

**Actual sandbox identifiers will usually not match the independently generated sample database.** “Not found” is expected in that case. To test positive matches with official sandbox downloads, use fake consumer identifiers that the portal makes available or allows you to configure; this app does not invent an undocumented consumer-request creation endpoint. The included simulated ZIP supplies known positive matches offline.

Demo and sandbox use separate databases. The app deliberately has no production base URL option: outcomes calculated against a fictional broker database must not be sent to production.

## What was actually verified

- **60 automated tests passed**, including official normalization/hash examples; all six list types; ZIP validation; repeat imports; deletion idempotency; review gates; suppression; removed identifiers; mode isolation; local HTTP/CSRF behavior; and mocked API download/upload/amend flows.
- A complete CLI demo ran against 1,000 records and produced actual exported CSVs, an audit file, and measured timings.
- A second run used 10,000 records with the same 26 requests. It measures a larger database, not larger request volume or enterprise throughput.
- JavaScript syntax and dependency-free review rendering regression checks passed. Automated visual/browser testing could not run because the build environment restricted the browser runtime. Screenshots are not fabricated or included.
- **No authenticated DROP API round trip was completed.** No sandbox credentials were provided, and an unauthenticated network probe was blocked by the build environment (`403: Calls to this URL are not allowed`). That response is not evidence of DROP authentication behavior.

See `evidence/test_results.txt`, `evidence/demo_results.json`, `evidence/benchmark_10000.json`, `evidence/demo_audit.json`, and `evidence/demo_status_responses.zip`.

Run tests:

```powershell
py -3 -m unittest discover -s tests -v
```

Run a reproducible, isolated demo without opening the browser:

```powershell
py -3 run_demo.py --output my_demo_run
```

Use a new output directory on each run. The script refuses to overwrite an existing run database.

## Sample database and data dictionary

`data/demo.sqlite3` is a pristine, preseeded SQLite database. `data/sample_customers.csv` contains the same 1,000 fictional source records. `data/simulated_drop_requests.zip` contains 26 hashed work items across Email, Phone, NDZ, NameVIN, MAID, and CTVID lists. `data/ground_truth.json` describes the expected cases.

No real customer records were downloaded. Emails use `example.test`. The populated phone fixture uses the fictional `202-555-01xx` range. Device/VIN values are artificial identifiers and are never used for contact.

| Table | Role |
|---|---|
| `customers` | Synthetic personal fields in a JSON payload; internal person ID; exemption and opt-out flags |
| `hash_index` | Exact lookup by identifier type and hash; cascades when a customer is deleted |
| `batches` | Source, received timestamp, and archive digest |
| `requests` | Original case-sensitive work item ID, hash, list, state, candidates, and outcome |
| `suppression` | Active request identifiers used to reject matching new intake through `add_customer` |
| `audit` | Timestamped decisions/actions and reviewer rationale |
| `record_evidence` | Source references, verification labels, and synthetic context; absent historical provenance is marked unknown |
| `review_cases` | Follow-up ownership and due dates for held cases |
| `submissions` | API submission attempts, response details, and reconciliation state |
| `deleted_records` | Created on first action; minimal record-ID ledger for idempotency |

The source CSV is a reusable synthetic fixture. Deleting a record from the runtime database does not delete that source fixture, previous run databases, or ZIP backups. Do not use this arrangement for real personal information.

## Architecture and AI boundary

- `app.py`: loopback-only HTTP server, JSON endpoints, session token, separate environments.
- `static/index.html`: browser UI; no external scripts or assets.
- `normalization.py`: field-specific standardization, SHA-256/Base64, NDZ and NameVIN composite hashes. Greek/Cyrillic mappings and preservation of non-Latin scripts are covered by tests.
- `store.py`: SQLite schema, parameterized persistence, transactions, suppression on intake.
- `review.py`: evidence snapshots, per-candidate decisions, holds, stale-version checks, and atomic approval/action records.
- `engine.py`: archive ingestion, matching, review decisions, synthetic execution, and reporting.
- `drop_api.py`: actual authenticated HTTPS sandbox adapter; redirects disabled to avoid forwarding a secret to a different destination.
- `ai.py`: a small logistic-regression model trained deterministically on 1,600 generated record pairs. It uses name similarity, DOB, ZIP, and phone agreement to rank internal links.
- `seed.py`: reproducible synthetic database and request fixture generator.
- `run_demo.py`: end-to-end measurements and evidence generation.

The model sees internal readable synthetic records, never tries to reverse a hash, and never independently authorizes deletion. It runs locally and sends no data to an AI provider. Its displayed score is **not a calibrated identity probability**. The training distribution is artificial; no real-world precision/recall or business ROI is claimed. The exact-match baseline handles format normalization without AI.

## Scope and limitations

This is an academic prototype, not a production compliance service. Actions are manually triggered. The app has no production broker connectors, scheduled polling, enterprise authentication, distributed job queue, external contractor execution, backup erasure, or immutable audit storage. The SQLite audit trail is inspectable but can be edited by a user with file access.

The browser shows up to 100 customer records and 100 recent audit events; exports retain the full audit. Imports are capped at 20 MB uncompressed and 100 archive members. API response bodies are capped at 20 MB. Real workload limits require redesign and measurement.

The reviewer workflow supports approval, AI-link denial, exact-match dispute, deferral, evidence-based reopening, and case export. All held cases remain unresolved and block batch reporting. The prototype policy logic supports the seeded exemption and shared-identifier scenarios, not all combinations of partial exemptions or complex identity graphs. Suppression is enforced when intake uses `store.add_customer`; no external feed is connected. Removed identifiers deactivate the corresponding suppression entry. Fully automatic late-match amendments, reactivation handling, and per-row partial-upload repair are not implemented. The amendment endpoint is available for controlled resubmission, but there is no general-purpose UI to edit historical outcomes.

For a partially accepted upload, inspect and repair the outstanding records externally before recording safe-to-retry evidence. Do not merely retry the full batch. A manual reconciliation action records your assertion; the application cannot independently verify portal/email contents.

The official guidance announces a move from a 45-day cycle to a 30-day cycle on January 1, 2027. A production scheduler must use current requirements; this app makes no scheduling/compliance claim.

## Official references

Consulted November 14, 2026; technical documentation identifies version 1.2.0. Follow future changes in the official documentation before live use.

- [Getting started and sandbox URL](https://privacy.ca.gov/drop-for-data-brokers/technical-specifications/getting-started/)
- [API operations](https://privacy.ca.gov/drop-for-data-brokers/technical-specifications/api-operations/)
- [Data formats, hashing, and status codes](https://privacy.ca.gov/drop-for-data-brokers/technical-specifications/working-with-data/)
- [Integration workflow and validation](https://privacy.ca.gov/drop-for-data-brokers/technical-specifications/integration-workflow/)
- [Processing requirements](https://privacy.ca.gov/drop-for-data-brokers/process-drop-requests/)

## Optional UI rendering checks

The application itself does not require Node.js. If Node.js is available, run `node tests/check_review_ui.js evidence/review_ui_fixture.json` to check evidence rendering, denial controls, stale-confirmation resets, draft preservation, and HTML escaping. These are rendering regression checks, not a full browser visual test.
