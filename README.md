<p align="center">
  <img src="app/static/FtrIOToaster.png" alt="FtrIO Toaster" width="600"/>
</p>

# FtrIO Toaster

A lightweight Docker-hosted web UI for managing [FtrIO](https://github.com/FtrOnOff/FtrIO) feature toggles. View, edit, add, and delete toggles in your `appsettings.json` without touching a file.

## The FtrIO Ecosystem

| Project | Role |
|---|---|
| [FtrIO](https://github.com/FtrOnOff/FtrIO) | The core library. Weaves `[Toggle]` into your IL at compile time, reads state from `appsettings.json` at runtime, and optionally syncs from remote sources via the provider pipeline. |
| **FtrIO.Toaster** | A lightweight web UI for managing toggles live. Writes values through `ToggleProviderBuffer` so changes flush to `appsettings.json` and are picked up instantly via `ReloadOnChange` — no file editing, no restart. |
| [FtrIO.onetwo](https://github.com/FtrOnOff/FtrIO.onetwo) | A .NET CLI audit tool. Scans your source tree for every toggle reference, cross-references against `appsettings.json`, and reports each toggle's state (`ON` / `OFF` / `20%` / `BLUE` / `MISSING`) with file and line number. |

## Why Toaster?

Toast is binary — it's either toasted or it's not. Much like a feature toggle.

It's also a nod to the Dungeon Master who runs our D&D sessions. Every good campaign needs someone deciding what's enabled and what isn't.

## Features

- **Boolean** on/off toggles
- **Percentage rollout** — slider and number input in sync
- **Blue/Green** deployment switching
- **User targeting** — manage the `users:alice,bob` allow-list directly from the UI
- **Attribute rules** — edit `attribute:plan equals premium` rules inline
- **A/B test** — percentage control with optional salt; labelled to distinguish from rollout
- Change toggle type at any time
- Add and delete toggles
- **Per-user overrides** — pin any toggle ON or OFF for a specific user ID, regardless of the active strategy; stored in `TogglesOverrides` and managed from a collapsible panel on each toggle card
- Implements FtrIO's buffer logic — changes are staged in memory and flushed atomically to `appsettings.json` on the `FlushInterval` defined in your config, exactly as a native FtrIO provider would
- Multi-environment support — manage any number of environments from a single UI instance
- **Audit log** — every toggle change and override is recorded with timestamp, environment, key, old value, new value, acting user, and entry type; viewable in-app via the Audit Log drawer

## Getting Started

Create a `compose.yml` and paste in the snippet below — no cloning required:

```yaml
services:
  toaster:
    image: thescottbot/ftrio:latest
    ports:
      - "8000:8000"
    environment:
      APP_NAME: "My Application"
      APPSETTINGS_PATH: /data/appsettings.json
      # AUTH_USERNAME: admin
      # AUTH_PASSWORD: secret
      # CHANGES_LOG_PATH: /log/changes.log
      # APPSETTINGS_PATH_STAGING: /env/staging/appsettings.json
    volumes:
      - type: bind
        source: /path/to/your/appsettings.json
        target: /data/appsettings.json
      - toaster-logs:/log
    restart: unless-stopped

volumes:
  toaster-logs:
```

```bash
docker compose up -d
```

Open `http://localhost:8000`.

> **Want to build from source or contribute?** Clone the repo and run `docker compose up -d --build` instead.

## Configuration

Two env vars control general behaviour:

| Variable | Default | Description |
|---|---|---|
| `APPSETTINGS_PATH` | `/data/appsettings.json` | Path to the base environment's config file inside the container |
| `APP_NAME` | *(empty)* | Display name shown in the UI header |
| `AUTH_USERNAME` | *(empty)* | Basic auth username — set alongside `AUTH_PASSWORD` to enable |
| `AUTH_PASSWORD` | *(empty)* | Basic auth password — set alongside `AUTH_USERNAME` to enable |
| `CHANGES_LOG_PATH` | `/log/changes.log` | Path inside the container where the audit log is written |

Auth is disabled when either variable is unset, which is fine for local dev. For any shared or production-accessible deployment, always set both. Credentials are compared in constant time to prevent timing attacks.

### Multiple Environments

Toaster supports any number of environments via `APPSETTINGS_PATH_<NAME>` env vars. Each one registers an environment in the UI dropdown and points directly at that environment's own `appsettings.json`. Every environment is fully independent — there is no merging or layering between them.

Each env var needs a matching volume mount. The name shown in the dropdown is derived from the variable suffix: `APPSETTINGS_PATH_STAGING` → `Staging`, `APPSETTINGS_PATH_MY_SERVICE` → `My Service`.

**Separate locations (different servers or machines):**

```yaml
environment:
  APPSETTINGS_PATH:            /env/local/appsettings.json
  APPSETTINGS_PATH_STAGING:    /env/staging/appsettings.json
  APPSETTINGS_PATH_PRODUCTION: /env/production/appsettings.json

volumes:
  - { type: bind, source: C:/local/app,        target: /env/local }
  - { type: bind, source: //staging-server/app, target: /env/staging }
  - { type: bind, source: //prod-server/app,    target: /env/production }
```

**Same location (overlay files on one machine):**

```yaml
environment:
  APPSETTINGS_PATH:              /env/app/appsettings.json
  APPSETTINGS_PATH_STAGING:      /env/app/appsettings.Staging.json
  APPSETTINGS_PATH_UAT:          /env/app/appsettings.UAT.json

volumes:
  - { type: bind, source: C:/local/app, target: /env/app }
```

**Mix of both:**

```yaml
environment:
  APPSETTINGS_PATH:              /env/local/appsettings.json
  APPSETTINGS_PATH_STAGING:      /env/local/appsettings.Staging.json
  APPSETTINGS_PATH_UAT:          /env/local/appsettings.UAT.json
  APPSETTINGS_PATH_PRODUCTION:   /env/production/appsettings.json

volumes:
  - { type: bind, source: C:/local/app,        target: /env/local }
  - { type: bind, source: //prod-server/app,    target: /env/production }
```

Remote machines must be reachable as a network share or mount point on the Docker host before the container starts.

## Authentication

### Basic Auth

Set `AUTH_USERNAME` and `AUTH_PASSWORD` in `docker-compose.yml` to enable HTTP Basic Auth. The browser will prompt for credentials on every new session. Leave either variable blank to disable — suitable for local dev only.

### SSO / Identity Providers (Google, Microsoft, GitHub, GitLab, OIDC)

For team deployments, use [OAuth2 Proxy](https://oauth2-proxy.github.io/oauth2-proxy/) as a sidecar. It handles the full sign-in flow with your identity provider and forwards authenticated requests to Toaster. No code changes to Toaster are required.

`docker-compose.yml` includes a commented-out `oauth2-proxy` service block. To enable it:

1. Uncomment the `oauth2-proxy` service
2. Switch the `toaster` service from `ports` to `expose` so it is no longer publicly reachable
3. Fill in your provider credentials:

```yaml
OAUTH2_PROXY_PROVIDER: google           # or github, azure, gitlab, oidc, …
OAUTH2_PROXY_CLIENT_ID: <client-id>
OAUTH2_PROXY_CLIENT_SECRET: <secret>
OAUTH2_PROXY_COOKIE_SECRET: <openssl rand -base64 32>
OAUTH2_PROXY_EMAIL_DOMAINS: yourcompany.com
```

4. Access Toaster via `http://localhost:4180` — the proxy will redirect unauthenticated users to your provider's sign-in page.

To restrict by individual email addresses instead of a domain, replace `OAUTH2_PROXY_EMAIL_DOMAINS` with `OAUTH2_PROXY_AUTHENTICATED_EMAILS_FILE` pointing to a mounted file containing one address per line.

## Audit Log

Every toggle change is recorded to an append-only JSONL file (`changes.log`). Each line contains:

Toggle change:
```json
{"timestamp": "2026-06-20T14:32:01Z", "environment": "Production", "type": "toggle", "key": "PaymentV2", "old": "blue", "new": "green", "user": "alice"}
```

Override change:
```json
{"timestamp": "2026-06-20T14:32:45Z", "environment": "Production", "type": "override", "key": "NewCheckoutFlow", "userId": "bob", "old": null, "new": false, "user": "alice"}
```

The **Audit Log** button in the toolbar opens an in-app drawer showing all recorded changes newest first, with toggle and override entries visually distinguished.

### Who is logged as the user?

| Setup | Logged as |
|---|---|
| Basic Auth enabled | The authenticated username |
| OAuth2 Proxy sidecar | Value of `X-Forwarded-User` or `X-Auth-Request-User` header |
| No auth configured | `anonymous` |

### Persisting the log

By default the log is written to `/log/changes.log` inside the container and is lost on restart. To persist it, mount a host directory:

```yaml
environment:
  CHANGES_LOG_PATH: /log/changes.log

volumes:
  - type: bind
    source: /path/to/your/log/dir
    target: /log
```

## Toggle Types

| Type | Config value | UI control | Requires `IFtrIOContextAccessor` |
|---|---|---|---|
| Boolean | `true` / `false` | On/off switch | No |
| Percentage rollout | `"20%"` | Slider + number input | No |
| Blue/Green | `"blue"` / `"green"` | Two-button selector | No |
| User targeting | `"users:alice,bob"` | Editable comma-separated list | Yes |
| Attribute rule | `"attribute:plan equals premium"` | Editable rule string | Yes |
| A/B test | `"ab:50"` / `"ab:50:round2"` | Percentage control + salt field | Yes (probabilistic fallback if absent) |

## Per-User Overrides

Any toggle can have per-user overrides stored in the `TogglesOverrides` section of `appsettings.json`. An override pins a specific user's experience to ON or OFF regardless of the active strategy — useful for QA, debugging, or VIP access.

```json
{
  "Toggles": {
    "NewCheckoutFlow": "ab:50",
    "SendWelcomeEmail": true
  },
  "TogglesOverrides": {
    "NewCheckoutFlow": {
      "alice":    true,
      "bob":      false
    },
    "SendWelcomeEmail": {
      "charlie":  false
    }
  }
}
```

Each toggle card has an **Overrides** button that expands a panel to add, remove, or flip individual user overrides. Changes flush through the same buffer pipeline as regular toggle edits and are recorded in the audit log with `"type": "override"`.

## appsettings.json Format

```json
{
  "FtrIO": {
    "ReloadOnChange": true,
    "FlushInterval": 5
  },
  "Toggles": {
    "MyBoolToggle":       true,
    "MyPercentToggle":    "20%",
    "MyBlueGreenToggle":  "blue",
    "MyUserToggle":       "users:alice,bob",
    "MyAttributeToggle":  "attribute:plan equals premium",
    "MyABToggle":         "ab:50"
  },
  "TogglesOverrides": {
    "MyABToggle": {
      "alice": true
    }
  }
}
```

All other keys in the file (`Logging`, etc.) are left untouched.
