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
- No door was opened and no guest link was created by the workstation agent.

## Automated evidence

Baseline: 97 tests passed with 100% statement coverage. Expanded suite and
post-deployment evidence are recorded below after final verification.

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
