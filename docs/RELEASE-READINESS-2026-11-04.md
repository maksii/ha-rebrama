# Release readiness audit — 2026-11-04

Scope: public custom-integration release 1.3.0, repository CI, and the owner's
Home Assistant 2026.10.4 installation. This is a maintainer audit, not an official
Home Assistant quality-tier award, Rebrama endorsement, or guarantee of future
private API compatibility.

## Findings and corrections

- Malformed successful place responses could become an empty account or escape
  as parsing exceptions. Envelopes, object/list shapes and identifiers now fail
  as transient errors before device cleanup can run.
- Debug logs included cloud response bodies and share links. Only HTTP method,
  sanitized endpoint, and status are logged; auth failures do not log tokens.
- HTTP 429/5xx could be misclassified by their JSON error body. HTTP transport
  failures take precedence, with account cooldown and Retry-After handling.
  A request lock and one-second spacing prevent bursts across all platforms.
- Secondary failures retained stale visible data silently. Affected entities now
  become unavailable, with one failure and recovery log per endpoint.
- Buttons, connectivity entities and per-place log entities had account-agnostic
  unique IDs. Two accounts sharing a door now get distinct entity identities.
  Config-entry migration 1.2 preserves existing entity IDs and customization,
  retains device registry IDs and history, and tolerates an interrupted migration.
- Device names and parent links are synchronized. Revoked management permission
  removes its opening-log entity, which returns when permission is restored.
- Share links are validated before use as URL paths. Usage/duration limits are
  checked, past starts are normalized at dispatch after authentication/pacing,
  and expired queued requests fail without creating a link. Missing create
  responses explicitly warn against repeating an accepted mutation.
- Calendar windows use an exclusive end so adjacent grants advance correctly.
- The poll interval is a fixed conservative five minutes, per HA guidance.
  The old interval option is removed; standard HA system polling controls and
  update_entity support custom schedules. Close refreshes are debounced.
- Strict typing is enforced against HA's Probatio types while runtime schema
  imports remain compatible with the supported HA 2026.8 validation engine.
- CI pins development tools/actions, tests both ends of the supported HA range,
  enforces 95% branch-inclusive coverage, runs CodeQL, and uses Dependabot.

## Live baseline

- Integration 1.2.1 loaded; 22 enabled entities, one place, nine access points.
  All nine were online. Poll options were empty (five-minute default).
- The live profile explicitly returned validUntil=null, so an unknown subscription
  expiry is correct. The place had canManage=false; no forbidden log polling or
  opening-log sensor is appropriate for this account.
- Seven-day recorder history contained online/offline transitions, cloud/network
  unavailability and recovery. The recorder database continued writing current
  states; an earlier HA/Postgres network interruption had recovered.
- Rebrama logs contained HTTP 520 and earlier network/DNS errors. No integration
  traceback was found. These are upstream/connectivity failures, not invalid
  credentials, and recovery restored the entities.
- An Apple Watch Home control stayed yellow after the owner tapped Intercom.
  Targeted bridge logs showed the on write and reset ten seconds later. Separate
  isolated tests using HA's real HomeKit Switch adapter passed on command success
  and failure. The owner excluded further Watch work after the fault was traced
  outside Rebrama. Temporary debug logging was restored to warning.
- No door was opened by the workstation agent. During post-deployment validation,
  one future one-use guest link was created, checked in the sensor and calendar,
  then immediately revoked before it could become valid.

## Automated evidence

Baseline: 97 tests passed with 100% statement coverage. Final local suite:
**142 passing tests on HA 2026.8.0 and 2026.10.4**, with **99.31% combined
statement/branch coverage**. Ruff check/format and strict mypy pass.
SonarQube reports gate OK, zero open issues and zero security hotspots after
individual false-positive review. No blanket rule exclusions were added.

The shared smarthome Sonar runner currently invokes unittest, which cannot run
this Linux-only pytest harness. The actual scan used the same runner's scanner
and gate checks with its coverage step adapted to execute pytest under WSL.
For a standalone scan, first generate coverage with
`pytest --cov-report=xml:.sonar-reports/coverage.xml`, then run
`uvx pysonar -Dsonar.qualitygate.wait=true` with SONAR_TOKEN and SONAR_HOST_URL
provided in the environment. Never put a token in a command argument or file.

## Deployment evidence

- Deployed candidate 1.3.0 to HA 2026.10.4 on 2026-11-04. HA configuration check
  passed (unrelated xiaomi_miot deprecation warnings remain outside this scope).
  Source compiled using the HA container's Python before replacement.
- Protected on-host backup retains original integration files and config-entry,
  entity and device registries. Backup location is in the private workstation
  deployment record; no credentials or storage contents are committed here.
- Entry schema migrated from 1.1 to 1.2. All 22 entity IDs and all device IDs were
  retained; 18 door entity unique IDs gained account scope. Registry persistence
  and live WebSocket state were both verified.
- Nine connectivity sensors remain on; account count is nine, temporary-access
  count is zero, calendar is off. Subscription remains correctly unknown.
- Live service smoke: create a one-use link for one door, starting an hour in
  the future and lasting two minutes; verify sensor count, validity and calendar;
  revoke; verify count zero and calendar removal. No physical open command was
  sent. Cloud replies were HTTP 201 for create and HTTP 200 for revoke and reads.
- Post-deployment log review found no Rebrama warning or error. Share-link paths
  are redacted, and no HTTP response bodies are logged.
- Scheduled-poll and final GitHub check evidence are appended below.

## Static analysis triage

SonarQube's real complexity findings were refactored. Framework-contract findings
for async hooks without awaits and unused callback arguments are false positives:
HA requires those exact async signatures. dataclasses.replace returns the concrete
TempAccess type (also verified by strict mypy); Sonar infers a generic dataclass.
The config-flow domain-error handler intentionally logs only the error code:
logging.exception would unnecessarily disclose server error text during login.
These individual findings are reviewed as false positives, not rule exclusions.

## Distribution boundary

The code is a custom integration using a private, reverse-engineered API and a
bundled client. Shipping a tested GitHub/HACS custom release does not make it a
Home Assistant Core integration or an officially supported Rebrama product.
Upstream inclusion and quality-tier recognition require their own maintainer
review. Local brand assets are shipped with the integration for HA's brands proxy.
