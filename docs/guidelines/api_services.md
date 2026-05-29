# Admin API — Integration Guide for External Projects

> OAuth2 Bearer-token API exposed by `/admin/api/services.php`. This guide covers
> every step a new project needs to call the **Assets**, **Monitoring**,
> **Location**, and **Scheduler** services: endpoints, parameters, grants,
> scopes, sample tokens, and cURL / PHP / JavaScript examples.
>
> The same admin platform is deployed across multiple tenants. Substitute
> `https://your-host` below with the deployment you are integrating against.

---

## 1. Endpoints

| Purpose                      | Method   | URL                               | Content-Type                            |
| ---------------------------- | -------- | --------------------------------- | --------------------------------------- |
| Obtain an access token       | POST     | `/admin/api/oauth/token.php`      | `application/x-www-form-urlencoded`     |
| Introspect a token           | POST     | `/admin/api/oauth/introspect.php` | `application/x-www-form-urlencoded`     |
| Revoke a token               | POST     | `/admin/api/oauth/revoke.php`     | `application/x-www-form-urlencoded`     |
| Dispatch a service action    | POST     | `/admin/api/services.php`         | `application/x-www-form-urlencoded`     |

All four endpoints require **HTTPS** (`Authentication::RequireHTTPS()`).
Non-POST or wrong Content-Type returns `405 Method Not Allowed` /
`invalid_request`.

---

## 2. Authentication — OAuth2 Client Credentials Flow

### Step 2.1 — Request a token

**POST** `/admin/api/oauth/token.php`

| Field           | Required             | Type     | Description                                                                             |
| --------------- | -------------------- | -------- | --------------------------------------------------------------------------------------- |
| `grant_type`    | yes                  | string   | `client_credentials` (recommended), `password`, `refresh_token`, `authorization_code`   |
| `username`      | yes (CC & PW)        | string   | Web-service user username (e.g. `ws_assets_user`)                                       |
| `password`      | yes (CC & PW)        | string   | Web-service user password                                                               |
| `scope`         | yes (CC & PW)        | string   | Space-separated scopes (see §3). For a single scope just pass the value.                |
| `user_type`     | yes (PW grant)       | int      | Numeric user type (only used by the `password` grant)                                   |
| `refresh_token` | yes (RT grant)       | string   | Opaque refresh token                                                                    |
| `token_format`  | optional             | string   | `opaque` (default) or `jwt`                                                             |
| `lifetime`      | optional (CC)        | int      | `1` issues a long-lived token (no refresh). Default: standard TTL with refresh token    |

**Success response (HTTP 200):**
```json
{
  "access_token": "64a08ec5688cadd8872a645690c01cb9.9cc1f019c8e0c4e6df067724cef607b0c9d8589c674cdb822f7a6162a4242563",
  "token_type": "Bearer",
  "expires_in": 3600,
  "scope": "assets:read",
  "refresh_token": "…"
}
```

`refresh_token` is omitted when `lifetime=1`.

**Failure:** `400` / `401` with
`{error: "invalid_request|invalid_client|invalid_grant|unsupported_grant_type", error_description: "…"}`.

### Step 2.2 — Call a service with the Bearer token

```
POST /admin/api/services.php
Authorization: Bearer <access_token>
Content-Type: application/x-www-form-urlencoded
```

Every service call carries `service` + `action` (see §4). Responses always
include `reference_id` (a GUID) that you should log for correlation with our
server-side `webservice_request` table.

---

## 3. Scopes

| Scope                | Granted by webservice user                                    | Services it unlocks             |
| -------------------- | ------------------------------------------------------------- | ------------------------------- |
| `assets:read`        | `ws_assets_user`                                              | `service=assets`                |
| `monitoring:read`    | `ws_monitoring_user`                                          | `service=monitoring`            |
| `location:read`      | per-user (password grant from end-user accounts)              | `service=location`              |
| `scheduler:write`    | `ws_scheduler_user`                                           | `service=scheduler`             |

Source of truth: `webservices_oauth_scopes` table +
`admin/api/shared/OAuthScope.php` enum.

Calling an action with a token that does **not** include the required scope
returns HTTP `403 Forbidden` with `ERROR_3542` / `ERROR_3540` / `ERROR_3551` /
`ERROR_0053` (depending on the service).

---

## 4. Services

### 4.1 Assets (`service=assets`, scope `assets:read`)

All Assets actions return their payload under the named keys `asset`
(singular, object) or `assets` (plural, array). The shape of every asset
object is documented in §5.2 (`Asset` schema). Nested `Type`, `Brand`,
`Location`, and `Status` objects are eagerly populated when the FK is set
on the row; they are `null` otherwise.

#### 4.1.1 `get_asset_by_id`

**Request body** (`application/x-www-form-urlencoded`):

| Field       | Required   | Type     | Notes                      |
| ----------- | ---------- | -------- | -------------------------- |
| `service`   | yes        | string   | `assets`                   |
| `action`    | yes        | string   | `get_asset_by_id`          |
| `id`        | yes        | int      | Primary key of the asset   |

**Success response (HTTP 200):**

| Field            | Type       | Description                                                 |
| ---------------- | ---------- | ----------------------------------------------------------- |
| `return`         | int        | `1` (success)                                               |
| `message`        | string     | `"Success"`                                                 |
| `reference_id`   | string     | GUID — echo back when opening tickets                       |
| `service_name`   | string     | `"AssetsWebService"`                                        |
| `asset`          | `Asset`    | Single asset object (see §5.2). Always non-null on success. |

```text
{
  "return": 1,
  "message": "Success",
  "reference_id": "9F1A4C3E-…",
  "service_name": "AssetsWebService",
  "asset": {
    "ID": 42,
    "CustomNumber": 178,
    "TypeID": 3, "Type": { "ID": 3, "Name": "Switch", "ShortName": "SW", "IsEnabled": true },
    "Name": "Core-SW-01",
    "SerialNumber": "ABC-12345",
    "BrandID": 7, "Brand": { "ID": 7, "Name": "Cisco" },
    "Model": "Catalyst 9300",
    "SKU": null,
    "LocationID": 12, "Location": { "ID": 12, "Name": "DC1 Rack A", "CalculatedName": "DC1 / Rack A" },
    "RAM": "16GB", "Firmware": "17.6.4", "HardwareVersion": "V03",
    "Hostname": "core-sw-01", "Barcode": null, "Comment": null,
    "Ports": [ <AssetPort[] -- see §5.2> ],
    "Images": null,
    "Interfaces": [ <AssetInterface[] -- see §5.2> ],
    "StatusID": 1, "Status": { "ID": 1, "Name": "Active", "Color": "#28a745" },
    "StatusDate": "2026-04-25 09:14:00", "StatusValue": null, "StatusComment": null,
    "CreatedOn": "2024-08-12 10:32:11",
    "LastModifiedOn": "2026-03-04 18:05:42",
    "IsDeleted": false, "DeletedOn": null
  }
}
```

**Errors:** `ERROR_1642` (missing ID), `ERROR_1643` (non-numeric ID),
`ERROR_1645` (asset not found).

#### 4.1.2 `get_asset_by_custom_number`

**Request body:**

| Field             | Required   | Type     | Notes                            |
| ----------------- | ---------- | -------- | -------------------------------- |
| `service`         | yes        | string   | `assets`                         |
| `action`          | yes        | string   | `get_asset_by_custom_number`     |
| `custom_number`   | yes        | int      | Asset `CustomNumber` value       |

**Success response:** identical envelope to §4.1.1 — single `asset` object
matching the `Asset` schema.

**Errors:** `ERROR_1688` (missing), `ERROR_1689` (non-numeric),
`ERROR_1691` (not found — the custom number is echoed in `details`).

#### 4.1.3 `get_assets_custom_number_range`

**Request body:**

| Field         | Required   | Type     | Notes                                |
| ------------- | ---------- | -------- | ------------------------------------ |
| `service`     | yes        | string   | `assets`                             |
| `action`      | yes        | string   | `get_assets_custom_number_range`     |
| `min_value`   | yes        | int      | Inclusive lower bound                |
| `max_value`   | yes        | int      | Inclusive upper bound                |

Server caps the result size via the `ASSET_SERVICES_DATABASE_RECORDS_LIMITS`
configuration value (default **50**). The endpoint **does not paginate** —
`min_value`/`max_value` are filters, not page bounds. Asking for an empty
range (no asset has a `CustomNumber` between min and max) returns
`ERROR_1686`, not an empty array.

**Success response (HTTP 200):**

| Field            | Type          | Description                                                    |
| ---------------- | ------------- | -------------------------------------------------------------- |
| `return`         | int           | `1` (success)                                                  |
| `message`        | string        | `"Success"`                                                    |
| `reference_id`   | string        | GUID                                                           |
| `service_name`   | string        | `"AssetsWebService"`                                           |
| `assets`         | `Asset[]`     | Array of asset objects (see §5.2), max length = config limit   |

```text
{
  "return": 1, "message": "Success",
  "reference_id": "9F1A4C3E-…",
  "service_name": "AssetsWebService",
  "assets": [ { "ID": 42, "CustomNumber": 178, "...": "..." }, "...": "..." ]
}
```

**Errors:** `ERROR_1681` / `ERROR_1682` (missing min/max),
`ERROR_1683` / `ERROR_1684` (non-numeric min/max),
`ERROR_1686` (no records in the requested range).

### 4.2 Monitoring (`service=monitoring`, scope `monitoring:read`)

#### 4.2.1 `get_error_stats_in_last_hour`

**Request body:**

| Field        | Required   | Type     | Notes                                |
| ------------ | ---------- | -------- | ------------------------------------ |
| `service`    | yes        | string   | `monitoring`                         |
| `action`     | yes        | string   | `get_error_stats_in_last_hour`       |
| `interval`   | yes        | int      | Lookback window in **hours** (≥1)    |

**Success response (HTTP 200):**

| Field            | Type             | Description                    |
| ---------------- | ---------------- | ------------------------------ |
| `return`         | int              | `1`                            |
| `message`        | string           | `"Success"`                    |
| `reference_id`   | string           | GUID                           |
| `service_name`   | string           | `"MonitoringWebService"`       |
| `monitoring`     | `Monitoring`     | Stats object (see §5.2)        |

```json
{
  "return": 1, "message": "Success",
  "reference_id": "9F1A4C3E-…",
  "service_name": "MonitoringWebService",
  "monitoring": {
    "Interval": 24,
    "IntervalFormat": "PT24H",
    "StartDate": "2026-04-24 09:00:00",
    "EndDate":   "2026-04-25 09:00:00",
    "WarningsCount": 142,
    "ErrorsCount": 7,
    "CriticalCount": 0,
    "CriticalErrorsCount": 0
  }
}
```

> **Note on date direction:** despite the field names, the server populates
> `StartDate` with the **earlier** boundary (`now − interval`) and `EndDate`
> with `now`. Callers that need a chronological window should treat the pair
> as `[StartDate, EndDate]` regardless.

**Errors:** `ERROR_0169` (missing interval), `ERROR_0315` (non-numeric).

### 4.3 Location (`service=location`, scope `location:read`)

#### 4.3.1 `update_location`

Intended for end-user clients (authenticated via the `password` grant so each
location update is attributed to a real user via `UserID`).

**Request body:**

| Field         | Required   | Type    | Notes                  |
| ------------- | ---------- | ------- | ---------------------- |
| `service`     | yes        | string  | `location`             |
| `action`      | yes        | string  | `update_location`      |
| `latitude`    | yes        | float   | Range `-90 … 90`       |
| `longitude`   | yes        | float   | Range `-180 … 180`     |

**Success response (HTTP 200):**

| Field            | Type     | Description                                               |
| ---------------- | -------- | --------------------------------------------------------- |
| `return`         | int      | `1`                                                       |
| `message`        | string   | `"Success"`                                               |
| `reference_id`   | string   | GUID                                                      |
| `service_name`   | string   | `"LocationUpdateWebService"`                              |
| `location_id`    | int      | Primary key of the inserted `users_locations` row         |

```json
{
  "return": 1, "message": "Success",
  "reference_id": "9F1A4C3E-…",
  "service_name": "LocationUpdateWebService",
  "location_id": 90834
}
```

**Errors:** `ERROR_1693` / `ERROR_1708` / `ERROR_1709` (latitude),
`ERROR_1710` / `ERROR_1711` / `ERROR_1712` (longitude),
`ERROR_2345` (insert failure).

### 4.4 Scheduler (`service=scheduler`, scope `scheduler:write`)

#### 4.4.1 `run_schedule`

Triggers the background scheduler loop — all due `webservice_scheduler`
entries are executed. Designed to be invoked from an external cronjob; the
endpoint flushes a `200 accepted` response via `fastcgi_finish_request()`
and continues work in the background.

**Request body:**

| Field       | Required   | Type     | Notes            |
| ----------- | ---------- | -------- | ---------------- |
| `service`   | yes        | string   | `scheduler`      |
| `action`    | yes        | string   | `run_schedule`   |

**Success responses (HTTP 200) — three flavours:**

1. **No pending services** (return path: `ServiceConstants::NO_PENDING_SERVICES`):
   ```json
   {
     "return": 1, "message": "Success",
     "details": "No pending services found",
     "reference_id": "…",
     "service_name": "SchedulerWebService"
   }
   ```
2. **Scheduler dispatched at least one service** (return path: `ServiceConstants::SCHEDULER_FINALIZED`):
   ```json
   {
     "return": 1, "message": "Success",
     "details": "Scheduler finalized",
     "reference_id": "…",
     "service_name": "SchedulerWebService"
   }
   ```
3. **Internal error during execution** — HTTP 500 with `code: "Ex3541"` and a `details` line.

The response is sent **before** the scheduler loop starts via
`fastcgi_finish_request()` only when the underlying SAPI supports it
(PHP-FPM in production); on classic mod_php deployments the request blocks
until the loop exits. Either way the response shape is identical.

---

## 5. Response envelope and object schemas

### 5.1 Common envelope

Every service response is a JSON object with this shape:

| Field            | Type              | When present                    | Description                                                                                          |                                   |
| ---------------- | ----------------- | --------------                  | ----------------------------------------------------------------                                     |                                   |
| `return`         | int               | always                          | `0` Failure, `1` Success, `2` Processing, `3` Stalled                                                |                                   |
| `message`        | string            | always                          | Short human-readable summary (`"Success"` / `"Failure"`)                                             |                                   |
| `reference_id`   | string (GUID)     | always                          | Trace ID — log on the caller side, supply when opening tickets                                       |                                   |
| `service_name`   | string            | always                          | One of `AssetsWebService`, `MonitoringWebService`, `LocationUpdateWebService`, `SchedulerWebService` |                                   |
| `details`        | string            | failure path, sometimes success | Long description; on failure includes the `ExNNNN` tag                                               |                                   |
| `code`           | string            | failure only                    | The full `"ExNNNN"` tag (e.g. `"Ex1645"`), **not** a number                                          |                                   |
| `<payload>`      | object \          | array                           | success only                                                                                         | Varies per action — see §4 / §5.2 |

**Success**

```json
{
  "return": 1,
  "message": "Success",
  "reference_id": "9F1A4C3E-…",
  "service_name": "AssetsWebService",
  "asset": {"...": "see §5.2"}
}
```

**Failure**

```json
{
  "return": 0,
  "message": "Failure",
  "details": "ERROR occurred while getting Asset by ID, asset with ID [99] cannot be found in the database Ex1645",
  "code": "Ex1645",
  "reference_id": "9F1A4C3E-…",
  "service_name": "AssetsWebService"
}
```

> **Note:** `code` is the full extracted error tag (`"Ex1645"`), already
> prefixed with `Ex`. Caller-side formatters that wrap it as
> ``[`Ex${response.code}`]`` will produce `[ExEx1645]` (double prefix). Use
> `[${response.code}]` instead, or strip the `Ex` prefix before formatting.

**HTTP status mapping**

| HTTP                        | When                                                           |
| --------------------------- | -------------------------------------------------------------- |
| `200 OK`                    | All successful service calls                                   |
| `400 Bad Request`           | Validation failure (missing/invalid params, no records found)  |
| `401 Unauthorized`          | Missing / invalid / expired bearer token                       |
| `403 Forbidden`             | Token valid, but does not include the required scope           |
| `405 Method Not Allowed`    | Non-POST or wrong `Content-Type`                               |
| `500 Internal Server Error` | Uncaught server-side exception (always carries `reference_id`) |

Return codes live in `admin/api/shared/WebServiceReturnCode.php`:
`0 = Failure`, `1 = Success`, `2 = Processing`, `3 = Stalled` (the latter
two are persisted in the audit table but the live service responses today
only emit `0` or `1`).

### 5.2 Object schemas

All date/datetime fields are emitted as **strings** in the server's local
timezone. Datetimes use `"Y-m-d H:i:s"` (e.g. `"2026-04-25 09:14:00"`),
plain dates use `"Y-m-d"`. Booleans are real JSON booleans (`true`/`false`),
not `0`/`1`. `null` is used wherever the source column is nullable and
empty.

#### 5.2.1 `Asset` (returned by §4.1.1, §4.1.2; element type of §4.1.3)

| Field                | Type                    | Description                                                  |                                                                    |
| -------------------- | ----------------------- | ------------------------------------------------------------ |                                                                    |
| `ID`                 | int                     | Primary key, never null                                      |                                                                    |
| `CustomNumber`       | int \                   | null                                                         | Tenant-defined asset number (used by `get_asset_by_custom_number`) |
| `TypeID`             | int \                   | null                                                         | FK → `assets_types.ID`                                             |
| `Type`               | `AssetType` \           | null                                                         | Eager-loaded type object (see §5.2.4)                              |
| `Name`               | string \                | null                                                         | Asset display name                                                 |
| `SerialNumber`       | string \                | null                                                         | Vendor-issued serial                                               |
| `BrandID`            | int \                   | null                                                         | FK → `assets_brands.ID`                                            |
| `Brand`              | `AssetBrand` \          | null                                                         | Eager-loaded brand object (see §5.2.5)                             |
| `Model`              | string \                | null                                                         | Vendor model designation                                           |
| `SKU`                | string \                | null                                                         | Stock keeping unit                                                 |
| `LocationID`         | int \                   | null                                                         | FK → `assets_locations.ID`                                         |
| `Location`           | `AssetLocation` \       | null                                                         | Eager-loaded location object (see §5.2.6)                          |
| `RAM`                | string \                | null                                                         | Free-text memory description                                       |
| `Firmware`           | string \                | null                                                         | Firmware version string                                            |
| `HardwareVersion`    | string \                | null                                                         | Hardware revision string                                           |
| `Hostname`           | string \                | null                                                         | Network hostname                                                   |
| `Barcode`            | string \                | null                                                         | Barcode value                                                      |
| `Comment`            | string \                | null                                                         | Free-text comment                                                  |
| `Ports`              | `AssetPort[]` \         | null                                                         | Open ports configured for this asset (see §5.2.2)                  |
| `Images`             | array \                 | null                                                         | Reserved (not populated by these endpoints)                        |
| `Interfaces`         | `AssetInterface[]` \    | null                                                         | Network interfaces for this asset (see §5.2.3)                     |
| `StatusID`           | int \                   | null                                                         | FK → `assets_statuses.ID`                                          |
| `Status`             | `AssetStatus` \         | null                                                         | Eager-loaded status object (see §5.2.7)                            |
| `StatusDate`         | string (datetime) \     | null                                                         | When the current status was set                                    |
| `StatusLink`         | string \                | null                                                         | Optional URL associated with the status                            |
| `StatusValue`        | float \                 | null                                                         | Optional numeric value associated with the status                  |
| `StatusComment`      | string \                | null                                                         | Optional free-text status note                                     |
| `CreatedOn`          | string (datetime) \     | null                                                         | Row creation timestamp                                             |
| `LastModifiedOn`     | string (datetime) \     | null                                                         | Last update timestamp                                              |
| `IsDeleted`          | bool \                  | null                                                         | Soft-delete flag                                                   |
| `DeletedOn`          | string (datetime) \     | null                                                         | Soft-delete timestamp                                              |

#### 5.2.2 `AssetPort` (element of `Asset.Ports`)

| Field               | Type                         | Description                                  |                             |
| ------------------- | ---------------------------- | -------------------------------------------- |                             |
| `ID`                | int                          | Primary key                                  |                             |
| `Name`              | string \                     | null                                         | Friendly name               |
| `Port`              | int \                        | null                                         | TCP/UDP port number         |
| `ServiceName`       | string \                     | null                                         | Service running on the port |
| `Protocol`          | string \                     | null                                         | `tcp`, `udp`, etc.          |
| `Comment`           | string \                     | null                                         | Free-text                   |
| `IsEnabled`         | bool \                       | null                                         | Soft-disable flag           |
| `CreatedOn`         | string (datetime) \          | null                                         |                             |
| `LastModifiedOn`    | string (datetime) \          | null                                         |                             |
| `IsDeleted`         | bool \                       | null                                         |                             |
| `DeletedOn`         | string (datetime) \          | null                                         |                             |

#### 5.2.3 `AssetInterface` (element of `Asset.Interfaces`)

| Field               | Type                        | Description                                    |                                             |
| ------------------- | --------------------------- | ---------------------------------------------- |                                             |
| `ID`                | int                         | Primary key                                    |                                             |
| `AssetID`           | int \                       | null                                           | FK → `assets.ID` (matches the parent asset) |
| `Name`              | string \                    | null                                           | Interface label (e.g. `eth0`, `Gi1/0/1`)    |
| `IP`                | string \                    | null                                           | IPv4/IPv6 address as text                   |
| `IPVersion`         | string \                    | null                                           | `"4"` or `"6"`                              |
| `MacAddress`        | string \                    | null                                           | MAC, no enforced format                     |
| `Number`            | int \                       | null                                           | Display ordering                            |
| `Comment`           | string \                    | null                                           |                                             |
| `IsEnabled`         | bool \                      | null                                           |                                             |
| `CreatedOn`         | string (datetime) \         | null                                           |                                             |
| `LastModifiedOn`    | string (datetime) \         | null                                           |                                             |
| `IsDeleted`         | bool \                      | null                                           |                                             |
| `DeletedOn`         | string (datetime) \         | null                                           |                                             |

#### 5.2.4 `AssetType` (nested under `Asset.Type`)

| Field                                                      | Type             | Description                  |                            |
| -------------                                              | ---------------- | ---------------------------- |                            |
| `ID`                                                       | int              | Primary key                  |                            |
| `Name`                                                     | string \         | null                         | Full label                 |
| `ShortName`                                                | string \         | null                         | Short label / abbreviation |
| `IsEnabled`                                                | bool \           | null                         |                            |
| `CreatedOn` / `LastModifiedOn` / `IsDeleted` / `DeletedOn` | as `Asset`       |                              |                            |

#### 5.2.5 `AssetBrand` (nested under `Asset.Brand`)

| Field                                                      | Type             | Description                         |                                   |
| -------------                                              | ---------------- | ----------------------------------- |                                   |
| `ID`                                                       | int              | Primary key                         |                                   |
| `Name`                                                     | string \         | null                                | Brand name                        |
| `Link`                                                     | string \         | null                                | Vendor URL                        |
| `ImageID`                                                  | int \            | null                                | FK → `files.ID` for the logo file |
| `IsEnabled`                                                | bool \           | null                                |                                   |
| `CreatedOn` / `LastModifiedOn` / `IsDeleted` / `DeletedOn` | as `Asset`       |                                     |                                   |

#### 5.2.6 `AssetLocation` (nested under `Asset.Location`)

| Field                                                      | Type             | Description                                         |                                               |
| ------------------                                         | ---------------- | --------------------------------------------------- |                                               |
| `ID`                                                       | int              | Primary key                                         |                                               |
| `Name`                                                     | string \         | null                                                | Top-level location name                       |
| `Sub`                                                      | string \         | null                                                | Sub-location (e.g. rack, shelf)               |
| `Details`                                                  | string \         | null                                                | Free-text address / description               |
| `CalculatedName`                                           | string \         | null                                                | Server-computed display string (`Name / Sub`) |
| `IsEnabled`                                                | bool \           | null                                                |                                               |
| `CreatedOn` / `LastModifiedOn` / `IsDeleted` / `DeletedOn` | as `Asset`       |                                                     |                                               |

#### 5.2.7 `AssetStatus` (nested under `Asset.Status`)

| Field                                                      | Type             | Description                                              |                                         |
| ------------------                                         | ---------------- | -------------------------------------------------------- |                                         |
| `ID`                                                       | int              | Primary key                                              |                                         |
| `Name`                                                     | string \         | null                                                     | Status label (`Active`, `In Repair`, …) |
| `Sub`                                                      | string \         | null                                                     | Sub-status                              |
| `CalculatedName`                                           | string \         | null                                                     | Server-computed display string          |
| `Color`                                                    | string \         | null                                                     | `#RRGGBB` hex used by the admin badge   |
| `IsEnabled`                                                | bool \           | null                                                     |                                         |
| `CreatedOn` / `LastModifiedOn` / `IsDeleted` / `DeletedOn` | as `Asset`       |                                                          |                                         |

#### 5.2.8 `Monitoring` (returned by §4.2.1)

| Field                   | Type                        | Description                                               |
| ----------------------- | --------------------------- | --------------------------------------------------------- |
| `Interval`              | int                         | The interval in hours that was requested (echoed back)    |
| `IntervalFormat`        | string                      | ISO-8601 duration form, e.g. `"PT24H"` for 24 hours       |
| `StartDate`             | string (datetime)           | Window start = `now − interval` (the **earlier** bound)   |
| `EndDate`               | string (datetime)           | Window end   = `now`                                      |
| `WarningsCount`         | int                         | `WARNING`-level log count over the window                 |
| `ErrorsCount`           | int                         | `ERROR`-level log count over the window                   |
| `CriticalCount`         | int                         | `CRITICAL`-level log count over the window                |
| `CriticalErrorsCount`   | int                         | Subset of `ErrorsCount` that was classified as critical   |

---

## 6. Web-service users (operational credentials)

> These are created per tenant — the strings below are **illustrative
> examples**, not production credentials. Every new deployment must regenerate
> its own user/password/token set; never reuse a token across tenants, and
> never commit live credentials to git.

| Username                | Example password      | Scope               | Notes                                  |
| ----------------------- | --------------------- | ------------------- | -------------------------------------- |
| `ws_assets_user`        | `assetsuser22`        | `assets:read`       | Sample assets-integration creds        |
| `ws_monitoring_user`    | `monitoringuser22`    | `monitoring:read`   | Sample monitoring creds                |
| `ws_scheduler_user`     | `scheduleruser1`      | `scheduler:write`   | Invoked by cronjob on a short interval |

**Sample live tokens** are one per tenant, long-lifetime `client_credentials`.
Format: `Bearer <id>.<secret>` (64 hex + dot + 64 hex). Live token values are
stored out-of-band (your secrets manager / ops handover notes); never commit
them to source control or share across deployments.

To mint a new set for your project:

1. Ask the platform admin to create a web-service user in the admin panel
   (*Web Services → Webservice Users*) and grant the required scope rows.
2. Request a long-lived token via the `client_credentials` grant with
   `lifetime=1` — no refresh token, no expiry.
3. Store the token in the caller project's secrets manager. Never commit it
   to git; never log it in plain text.

---

## 7. Examples

### 7.1 cURL — Get an asset by ID

```bash
# Step 1 — mint a short-lived token
ACCESS_TOKEN=$(curl -sS -X POST \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data 'grant_type=client_credentials&username=ws_assets_user&password=assetsuser22&scope=assets:read' \
  https://your-host/admin/api/oauth/token.php \
  | jq -r .access_token)

# Step 2 — call the service
curl -sS -X POST \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data 'service=assets&action=get_asset_by_id&id=42' \
  https://your-host/admin/api/services.php
```

### 7.2 PHP (Guzzle)

```php
use GuzzleHttp\Client;

$http = new Client(['base_uri' => 'https://your-host/']);

// 1) Token
$tokenResp = $http->post('/admin/api/oauth/token.php', [
    'form_params' => [
        'grant_type' => 'client_credentials',
        'username'   => 'ws_assets_user',
        'password'   => getenv('API_ASSETS_PASSWORD'),
        'scope'      => 'assets:read',
        'lifetime'   => 1,           // long-lived — no refresh token
    ],
]);
$accessToken = json_decode((string) $tokenResp->getBody(), true)['access_token'];

// 2) Call
$resp = $http->post('/admin/api/services.php', [
    'headers'     => ['Authorization' => "Bearer {$accessToken}"],
    'form_params' => [
        'service' => 'assets',
        'action'  => 'get_asset_by_id',
        'id'      => 42,
    ],
]);
$body = json_decode((string) $resp->getBody(), true);
```

### 7.3 JavaScript (fetch)

```javascript
const BASE = 'https://your-host';

// 1) Token
const tokenRes = await fetch(`${BASE}/admin/api/oauth/token.php`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
        grant_type: 'client_credentials',
        username:   'ws_assets_user',
        password:   process.env.API_ASSETS_PASSWORD,
        scope:      'assets:read',
    }),
});
const { access_token } = await tokenRes.json();

// 2) Call
const res = await fetch(`${BASE}/admin/api/services.php`, {
    method: 'POST',
    headers: {
        'Authorization': `Bearer ${access_token}`,
        'Content-Type':  'application/x-www-form-urlencoded',
    },
    body: new URLSearchParams({
        service: 'assets',
        action:  'get_asset_by_id',
        id:      '42',
    }),
});
const json = await res.json();
```

---

## 8. Token hygiene & troubleshooting

- **Always pass the Authorization header** — our server accepts
  `Authorization`, `REDIRECT_HTTP_AUTHORIZATION`, or `X-Authorization`
  (for reverse proxies that strip `Authorization`). See
  `OAuthServer::ExtractBearerToken()`.
- **HTTPS-only.** HTTP requests are rejected before auth runs.
- **Introspection.** `POST /admin/api/oauth/introspect.php` with
  `token=<access|refresh>` returns `{active: false}` for invalid/expired
  tokens (RFC 7662) and `{active: true, scope, user_id, grant_type,
  exp}` when valid.
- **Revocation.** `POST /admin/api/oauth/revoke.php` with
  `token=<…>` + optional `token_type_hint=refresh_token` always
  responds with HTTP 200 regardless of whether the token existed
  (RFC 7009).
- **Reference IDs.** Every success/failure carries `reference_id`.
  When opening a support ticket, always include it — our
  `webservice_request` table is indexed by it.
- **Rate limits.** The API itself is not rate-limited at the OAuth
  layer, but the Scheduler service holds a global lock per-job
  (`webservice_scheduler_lock` rows) — overlapping calls are skipped,
  not queued.
- **Clock skew.** JWT tokens (`token_format=jwt`) embed `iat` / `exp`.
  Keep the caller's clock within ±60 s of UTC.

---

## 9. File map (source references)

| Concern                      | File                                                                                                           |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------- |
| Service dispatcher           | `admin/api/services.php`                                                                                       |
| OAuth token endpoint         | `admin/api/oauth/token.php`                                                                                    |
| OAuth introspect endpoint    | `admin/api/oauth/introspect.php`                                                                               |
| OAuth revoke endpoint        | `admin/api/oauth/revoke.php`                                                                                   |
| Orchestrator                 | `admin/api/shared/OAuthServer.php`                                                                             |
| Grant handlers               | `admin/api/shared/GrantHandler.php`                                                                            |
| Token manager (opaque/JWT)   | `admin/api/shared/TokenManager.php`                                                                            |
| Scope validation             | `admin/api/shared/ScopeValidator.php`                                                                          |
| Service traits               | `admin/api/services/AssetsService.php`, `MonitoringService.php`, `LocationService.php`, `SchedulerService.php` |
| Service handler composer     | `admin/api/shared/ServiceHandler.php`                                                                          |
| Response envelope helpers    | `admin/api/shared/ApiResponse.php`, `OAuthResponse.php`, `ServiceConstants.php`                                |
| Scopes enum                  | `admin/api/shared/OAuthScope.php`                                                                              |
| Grant types enum             | `admin/api/shared/OAuthGrantType.php`                                                                          |
| Service names enum           | `admin/api/shared/WebServiceName.php`                                                                          |
| Return-code enum             | `admin/api/shared/WebServiceReturnCode.php`                                                                    |
| HTTP-code enum               | `admin/api/shared/HttpCode.php`                                                                                |
