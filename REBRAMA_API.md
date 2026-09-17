# Rebrama REST API Reference

Reverse-engineered from APK `com.rebrama.app` v0.5.0 (versionCode 16) and
verified against live probes. Field names are the **exact** wire JSON
(kotlinx.serialization descriptors), including the production typo
`aceessPointName`. Auth = Bearer JWT with a rotating refresh token.

## Transport

- Base: `https://rebrama.com`; paths are relative (`api/auth/login` → `https://rebrama.com/api/auth/login`).
- **Success** envelope (every 2xx): `{ "error": false, "message": "", "data": <T> }` — read `data`. Status **200** GET, **201** POST/create/login/refresh.
- **Error** envelope: **always HTTP 400** (never 401/403/404): `{ "error": { "code": <int>, "message": "<str>" } }`. Disambiguate by `error.code` (see Errors).

### Headers

| Header | Value | Required |
|---|---|---|
| `Authorization` | `Bearer <access>` | **Yes** on protected endpoints (code 1100 if absent/bad) |
| `Content-Type` | `application/json` | No (server lenient; send anyway) |
| `Accept` | `application/json` | No |
| `App-Build-Number` | `16` | No (cosmetic; mimics app) |
| `User-Fingerprint` | stable per-install id | No (cosmetic) |
| `Is-Widget` | `0` (`1` from the Android widget) | No (not validated server-side) |

Only `Authorization` is enforced; the three app-mimicking headers are cosmetic (future WAF insurance). Send them anyway.

## Auth

Phone is **digits only, no leading `+`, min 12** (e.g. `380000000000`). Leading `+` → 1210; `< 12` → 1209.

### Token contract
- **login** `POST /api/auth/login` `{phone, password}` → `data:{access, refresh}` (both JWT).
- **refresh** `POST /api/auth/refresh` `{refresh, access}` **+** header `Authorization: Bearer <old access>` → `data:{access, refresh}`.
  - Server only validates `body.refresh` (any Rebrama-signed JWT). The Bearer header and `body.access` are ignored — but **stay bug-compatible and send all three.**
  - Refresh tokens are **single-use / rotating**: serialise refreshes (one lock); each call invalidates the prior refresh JWT.
- **1100 is the only auth-failure signal** (there is no 401): on 1100 → refresh once → retry. If refresh also 1100/9999 → full re-login → else reauth.

### Registration / recovery flow
`check-registration` → `send-verification-code` → `verify-code` → `registration` (or `forgot-password`).
- `check-registration {phone}` → **201** `data:{userRegistered:true, id, validUntil}` for a known phone; **400 / 1201 for an unknown well-formed phone** (no `userRegistered:false` — no enumeration leak). Treat 1201 here as "no account".
- `verify-code {phone, smsCode}` → `data:{codeIsValid}`.

## Endpoint matrix

`data` = value inside the envelope's `data`. Auth ✓ = needs Bearer.

| Method | Path | Auth | Body | `data` |
|---|---|---|---|---|
| POST | `/api/auth/check-registration` | – | `{phone}` | `{userRegistered, id, validUntil}` |
| POST | `/api/auth/send-verification-code` | – | `{phone}` | `null` |
| POST | `/api/auth/verify-code` | – | `{phone, smsCode}` | `{codeIsValid}` |
| POST | `/api/auth/registration` | – | `{phone, smsCode, password, passwordConfirmation}` | `{access, refresh}` |
| POST | `/api/auth/login` | – | `{phone, password}` | `{access, refresh}` |
| POST | `/api/auth/forgot-password` | – | `{phone, smsCode, password, passwordConfirmation}` | `null` |
| POST | `/api/auth/refresh` | Bearer(old) | `{refresh, access}` | `{access, refresh}` |
| POST | `/api/auth/change-password` | ✓ | `{oldPassword, password, passwordConfirmation}` | `null` |
| DELETE | `/api/auth/delete-account` | ✓ | – | `{isDeleted}` |
| GET | `/api/users/me` | ✓ | – | `{id, phone, validUntil}` |
| GET | `/api/settings` | ✓ | – | `settings` (below) |
| POST | `/api/feedbacks` | ✓ | `{userId, text, contactType}` | `{created}` |
| GET | `/api/places/user/devices` | ✓ | – | `[Place]` — **discovery** |
| POST | `/api/devices/open` | ✓ | `{accessPointId}` | `{isDelivered}` |
| GET | `/api/places/{placeId}/open-logs?page&limit` | ✓ + canManage | – | `{count, items:[OpenLog]}` |
| GET | `/api/places/{placeId}/users?page&limit&search?` | ✓ + canManage | – | `{count, items:[UserPlaceInfo]}` |
| GET | `/api/places/{placeId}/users/{userId}` | ✓ + canManage | – | `UserPlaceInfo` |
| POST | `/api/places/grant-access` | ✓ | `{phone, placeId, isAdmin, info, accessPoints:[apId]}` | `{id, phone, info, isAdmin}` |
| PUT | `/api/places/user/edit` | ✓ | `{placeId, userId, info, accessPoints:[apId], isAdmin}` | `{id, phone, info, isAdmin}` |
| DELETE | `/api/places/remove-access` | ✓ | `{placeId, userId}` | `null` |
| GET | `/api/temp-accesses/user` | ✓ | – | `[TempAccess]` |
| GET | `/api/temp-accesses/{slug}/info` | ✓ | – | `TempAccessDetails` |
| POST | `/api/temp-accesses` | ✓ | `{accessPointIds:[apId], dateStart, dateEnd, description, usesNumber}` | `{tempAccessLink}` (full URL — see Gotchas) |
| DELETE | `/api/temp-accesses/{slug}` | ✓ | – | `null` |
| GET | `/api/payment/info` | ✓ | – | `{price, validUntil}` |
| POST | `/api/payment/link-mobile` | ✓ | `{monthCount}` | `{link, orderId}` |
| GET | `/api/payment/order/{orderId}` | ✓ | – | `{id, status}` (`status` = OrderStatus enum) |

## Object shapes

```jsonc
Place            : { id, name, canManage, isOwner, accessPoints:[AccessPoint] }
AccessPoint      : { id, name, canShareAccess, isOnline }
OpenLog          : { id, isTempAccess, userPhone, userInfo, aceessPointName /*sic*/, createdAt /*ISO8601*/ }
UserPlaceInfo    : { id, phone, isAdmin, info, accessPoints:[{ id, name, hasAccess }] }
TempAccess       : { link, description, dateStart, dateEnd, usesNumber, url }   // dateStart/End = epoch Long, unit unverified (s|ms)
TempAccessDetails: TempAccess + { usesNumberLeft, places:[{ place:Place, accessPoints:[AccessPoint] }] }
settings         : { minTemporaryAccessTimeInterval, maxTemporaryAccessTimeInterval,
                     minTemporaryAccessUsesNumber, maxTemporaryAccessUsesNumber,
                     remindAccessEndBeforeDays, widgetUpdatePeriod /*poll interval*/, donateLink }
```

## Errors (HTTP always 400; branch on `error.code`)

| Code | Meaning | Handling |
|---|---|---|
| 1100 | `Unauthorized` — bad/absent bearer, or refresh body missing `refresh` | refresh → retry; still 1100 → reauth |
| 1102 | password rule (`min length 32`; applied on register/change only) | show message |
| 1103 | `<Field> must be string` (null / wrong-typed field) | client bug |
| 1107 | `<Field> must be a valid uuid string` | client bug |
| 1109 | `<Field> is required` (missing/misnamed field; array body parsed as object) | client bug |
| 1111 | `<Field> should have a minimum length of 1` (empty array) | client bug |
| 1201 | `Phone must be a valid phone number` — **also returned for an unregistered phone** | "wrong phone / no account" |
| 1203 | `Wrong user credentials` — canonical login failure | "wrong phone or password" |
| 1209 | phone `< min length 12` | show message |
| 1210 | phone format (has `+`) — strip it | show message |
| 1301 | `You cant use current device` (sic) — accessPoint not yours / nonexistent | mark entity unavailable |
| 1304 | `You cant manage current place` (sic) — caller not admin/owner | gate on `canManage` |
| 1501 | `Temp access not found` | drop stale temp-access from state |
| 1502 | `Not enough permissions for temp access creation` (apId not owned) | show message |
| 1504 | `Start date less than current date` | show message |
| 1505 | `End date less than start date` | show message |
| 1507 | `Uses number is too small` (`usesNumber:0`) | show message |
| 1508 | `Uses number is too big` (`> max`) | show message |
| 9999 | unclassified malformed input (empty-string field, non-JSON body, malformed JWT in refresh) | log + raise |

## Gotchas (audited corrections)

1. **`tempAccessLink` is a full URL, not a slug.** `POST /api/temp-accesses` returns e.g. `https://rebrama.com/access/<slug>`; `{slug}/info` and `DELETE {slug}` take **only the trailing slug** — parse it off the URL.
2. **`canManage` gating.** `open-logs`, `users`, `users/{id}` return **1304** unless the place has `canManage:true` — *even for your own place*. `/api/places/user/devices` is the only place-scoped endpoint safe to call unconditionally; gate management/log features on `canManage`.
3. **`validUntil`** (subscription expiry, ISO8601) is returned by both `check-registration` and `/api/users/me` though the app's DTOs ignore it — lets you skip an extra call.
4. **Temp-access validation is partial server-side:** enforces `dateStart ≥ now` (1504), `dateEnd ≥ dateStart` (1505), `1 ≤ usesNumber ≤ max` (1507/1508), non-empty owned `accessPointIds` (1111/1502). It does **NOT** enforce the min time interval from settings (`usesNumber:null` = unlimited; sub-minute windows are accepted) — enforce that client-side from `/api/settings`.
5. **Settings units** (`*TimeInterval`, `widgetUpdatePeriod`) are `Long` of unverified unit (s vs ms) — sanity-clamp before using as a poll interval.
6. **Rate limits unknown** — serialise calls, ≲1 req/s, exponential back-off on 429/5xx.

## Out of scope
Firebase (FCM push, Realtime DB at `…europe-west1.firebasedatabase.app`) is wired for analytics/crashlytics only — no Firebase Auth, no user-data REST surface. The `rebrama.com` REST API is authoritative.

## Live probes

The probe scripts below live in the maintainer's private analysis folder and are **not** part of this repository (they need real account credentials).

- `rebrama_test.py` — happy-path E2E (login → refresh → me → settings → places → temp-access create/info/delete → open). Set `REBRAMA_OPEN=1` to actually open a door; otherwise step 9 is dry-run.
- `rebrama_error_test.py` — 54-case validation/auth/not-found probe → `_analysis/error_cases.json`. Side-effect-free except section D (rotates tokens) and F5/F6 (create temp-accesses not auto-cleaned).

Both use a digits-only phone + password and can be re-run to refresh transcripts.
