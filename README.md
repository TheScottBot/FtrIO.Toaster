![FtrIO Toaster](app/static/ftrio-banner.png)

# FtrIO Toaster

A lightweight Docker-hosted web UI for managing [FtrIO](https://github.com/TheScottBot/FtrIO) feature toggles. View, edit, add, and delete toggles in your `appsettings.json` without touching a file.

## Why Toaster?

Toast is binary — it's either toasted or it's not. Much like a feature toggle.

It's also a nod to the Dungeon Master who runs our D&D sessions. Every good campaign needs someone deciding what's enabled and what isn't.

## Features

- **Boolean** on/off toggles
- **Percentage rollout** — slider and number input in sync
- **Blue/Green** deployment switching
- Change toggle type at any time
- Add and delete toggles
- Reads and writes directly to your `appsettings.json`, preserving all other config keys

## Getting Started

```bash
git clone https://github.com/TheScottBot/FtrIO.Toaster
cd FtrIO.Toaster
docker compose up -d
```

Open `http://localhost:8000`.

## Configuration

The location of `appsettings.json` is set via an environment variable and a Docker volume mount in `docker-compose.yml`:

```yaml
environment:
  APPSETTINGS_PATH: /data/appsettings.json
volumes:
  - "/path/to/your/app:/data"
```

If `appsettings.json` lives on a different machine, mount it via a network share before pointing the volume at it. The file will be created automatically if it doesn't exist yet.

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
