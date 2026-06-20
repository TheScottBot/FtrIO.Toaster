<p align="center">
  <img src="app/static/FtrIOToaster.png" alt="FtrIO Toaster" width="600"/>
</p>

# FtrIO Toaster

A lightweight Docker-hosted web UI for managing [FtrIO](https://github.com/TheScottBot/FtrIO) feature toggles. View, edit, add, and delete toggles in your `appsettings.json` without touching a file.

## The FtrIO Ecosystem

| Project | Role |
|---|---|
| [FtrIO](https://github.com/TheScottBot/FtrIO) | The core library. Weaves `[Toggle]` into your IL at compile time, reads state from `appsettings.json` at runtime, and optionally syncs from remote sources via the provider pipeline. |
| **FtrIO.Toaster** | A lightweight web UI for managing toggles live. Writes values through `ToggleProviderBuffer` so changes flush to `appsettings.json` and are picked up instantly via `ReloadOnChange` — no file editing, no restart. |
| [ftrio-onetwo](https://github.com/TheScottBot/ftrio-onetwo) | A .NET CLI audit tool. Scans your source tree for every toggle reference, cross-references against `appsettings.json`, and reports each toggle's state (`ON` / `OFF` / `20%` / `BLUE` / `MISSING`) with file and line number. |

## Why Toaster?

Toast is binary — it's either toasted or it's not. Much like a feature toggle.

It's also a nod to the Dungeon Master who runs our D&D sessions. Every good campaign needs someone deciding what's enabled and what isn't.

## Features

- **Boolean** on/off toggles
- **Percentage rollout** — slider and number input in sync
- **Blue/Green** deployment switching
- Change toggle type at any time
- Add and delete toggles
- Implements FtrIO's buffer logic — changes are staged in memory and flushed atomically to `appsettings.json` on the `FlushInterval` defined in your config, exactly as a native FtrIO provider would
- Multi-environment support — manage any number of environments from a single UI instance

## Getting Started

```bash
git clone https://github.com/TheScottBot/FtrIO.Toaster
cd FtrIO.Toaster
docker compose up -d
```

Open `http://localhost:8000`.

## Configuration

Two env vars control general behaviour:

| Variable | Default | Description |
|---|---|---|
| `APPSETTINGS_PATH` | `/data/appsettings.json` | Path to the base environment's config file inside the container |
| `APP_NAME` | *(empty)* | Display name shown in the UI header |

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

## appsettings.json Format

```json
{
  "Toggles": {
    "MyBoolToggle":       true,
    "MyPercentToggle":    "20%",
    "MyBlueGreenToggle":  "blue"
  }
}
```

All other keys in the file (`FtrIO`, `Logging`, etc.) are left untouched.
