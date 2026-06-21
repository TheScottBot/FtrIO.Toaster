import json
import os
import secrets
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

APP_NAME = os.environ.get("APP_NAME", "")
AUTH_USERNAME = os.environ.get("AUTH_USERNAME", "")
AUTH_PASSWORD = os.environ.get("AUTH_PASSWORD", "")
CHANGES_LOG_PATH = Path(os.environ.get("CHANGES_LOG_PATH", "/log/changes.log"))
_auth_enabled = bool(AUTH_USERNAME and AUTH_PASSWORD)

_security = HTTPBasic(realm="FtrIO Toaster", auto_error=False)


def require_auth(credentials: HTTPBasicCredentials | None = Depends(_security)):
    if not _auth_enabled:
        return
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": 'Basic realm="FtrIO Toaster"'},
        )
    valid_user = secrets.compare_digest(credentials.username.encode(), AUTH_USERNAME.encode())
    valid_pass = secrets.compare_digest(credentials.password.encode(), AUTH_PASSWORD.encode())
    if not (valid_user and valid_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": 'Basic realm="FtrIO Toaster"'},
        )


def _extract_user(request: Request, credentials: HTTPBasicCredentials | None) -> str:
    """Resolve the acting user from Basic Auth, OAuth2 Proxy headers, or anonymous."""
    if _auth_enabled and credentials and credentials.username:
        return credentials.username
    for header in ("x-forwarded-user", "x-auth-request-user", "x-auth-request-email"):
        val = request.headers.get(header)
        if val:
            return val
    return "anonymous"


def _build_env_map() -> dict[str, Path]:
    env_map: dict[str, Path] = {}
    prefix = "APPSETTINGS_PATH"
    for key, val in os.environ.items():
        if key == prefix:
            env_map["Base"] = Path(val)
        elif key.startswith(prefix + "_"):
            name = key[len(prefix) + 1:].title().replace("_", " ").strip()
            env_map[name] = Path(val)
    if "Base" not in env_map:
        env_map["Base"] = Path("/data/appsettings.json")
    return env_map


def _build_label_map() -> dict[str, str]:
    label_map: dict[str, str] = {}
    prefix = "APPSETTINGS_LABEL"
    for key, val in os.environ.items():
        if key == prefix:
            label_map["Base"] = val
        elif key.startswith(prefix + "_"):
            name = key[len(prefix) + 1:].title().replace("_", " ").strip()
            label_map[name] = val
    return label_map


ENV_MAP: dict[str, Path] = _build_env_map()
LABEL_MAP: dict[str, str] = _build_label_map()
APPSETTINGS_PATH = ENV_MAP["Base"]

_lock = threading.Lock()

_DELETED = object()

# Toggle buffer: env → toggleKey → {value, user, timestamp}
_buffer: dict[str, dict[str, dict]] = {}

# Override buffer: env → toggleKey → userId → {value, user, timestamp}
_override_buffer: dict[str, dict[str, dict[str, dict]]] = {}

_flush_timer: threading.Timer | None = None

app = FastAPI(title="FtrIO Toaster", dependencies=[Depends(require_auth)])


# ── File resolution ───────────────────────────────────────────────────────────

def _effective_file(env: str) -> Path:
    return ENV_MAP[env]


def _env_path(env: str) -> Path:
    if env not in ENV_MAP:
        raise KeyError(f"Unknown environment: {env}")
    return _effective_file(env)


def _discover_environments() -> list[str]:
    return list(ENV_MAP.keys())


def _dir_identity(path: Path) -> object:
    try:
        s = os.stat(path)
        return (s.st_dev, s.st_ino)
    except OSError:
        return str(path.resolve())


def _build_dir_warnings() -> list[str]:
    from collections import defaultdict
    by_file: dict[object, list[str]] = defaultdict(list)
    for env in ENV_MAP:
        f = _effective_file(env)
        by_file[_dir_identity(f)].append(env)
    warnings = []
    for _, envs in by_file.items():
        if len(envs) < 2:
            continue
        f = _effective_file(envs[0])
        names = ", ".join(f'"{e}"' for e in envs)
        warnings.append(
            f"Environments {names} all point to the same file ({f.name}). "
            f"Writes from any of these environments will overwrite each other."
        )
    return warnings


DIR_WARNINGS: list[str] = _build_dir_warnings()


# ── File I/O ──────────────────────────────────────────────────────────────────

def _read_file(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".appsettings_tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_merged_toggles(env: str) -> dict:
    return _read_file(_env_path(env)).get("Toggles", {})


def _read_stored_overrides(env: str) -> dict:
    return _read_file(_env_path(env)).get("TogglesOverrides", {})


def _read_effective_overrides(env: str) -> dict:
    """Read TogglesOverrides with staged buffer changes applied."""
    with _lock:
        stored = _read_stored_overrides(env)
        staged = {k: dict(v) for k, v in _override_buffer.get(env, {}).items()}

    result: dict[str, dict] = {k: dict(v) for k, v in stored.items()}
    for toggle_key, user_changes in staged.items():
        result.setdefault(toggle_key, {})
        for user_id, change in user_changes.items():
            if change["value"] is _DELETED:
                result[toggle_key].pop(user_id, None)
            else:
                result[toggle_key][user_id] = change["value"]
        if not result[toggle_key]:
            del result[toggle_key]
    return result


# ── Audit log ─────────────────────────────────────────────────────────────────

def _append_log_entries(entries: list[dict]) -> None:
    if not entries:
        return
    try:
        CHANGES_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CHANGES_LOG_PATH, "a", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")
    except Exception:
        pass  # log failures must never block a write


def _read_log(limit: int = 200) -> list[dict]:
    if not CHANGES_LOG_PATH.exists():
        return []
    try:
        lines = CHANGES_LOG_PATH.read_text(encoding="utf-8").splitlines()
        entries = [json.loads(l) for l in lines if l.strip()]
        return list(reversed(entries[-limit:]))
    except Exception:
        return []


# ── Buffer / flush ─────────────────────────────────────────────────────────────

def _flush_interval_seconds() -> float:
    try:
        data = _read_file(APPSETTINGS_PATH)
        return float(data.get("FtrIO", {}).get("FlushInterval", 5))
    except Exception:
        return 5.0


def _flush() -> None:
    global _buffer, _override_buffer, _flush_timer

    with _lock:
        toggle_snapshot = {env: changes.copy() for env, changes in _buffer.items()}
        override_snapshot = {
            env: {k: dict(v) for k, v in user_map.items()}
            for env, user_map in _override_buffer.items()
        }
        _buffer = {}
        _override_buffer = {}

    all_envs = set(toggle_snapshot) | set(override_snapshot)

    for env in all_envs:
        staged_toggles = toggle_snapshot.get(env, {})
        staged_overrides = override_snapshot.get(env, {})
        if not staged_toggles and not staged_overrides:
            continue

        path = _env_path(env)
        try:
            with _lock:
                data = _read_file(path)

            log_entries: list[dict] = []

            # ── Apply toggle changes ──
            old_toggles = data.get("Toggles", {})
            new_toggles = old_toggles.copy()
            for name, change in staged_toggles.items():
                value = change["value"]
                old_val = old_toggles.get(name)
                if value is _DELETED:
                    new_toggles.pop(name, None)
                    new_val = None
                else:
                    new_toggles[name] = value
                    new_val = value
                log_entries.append({
                    "timestamp": change["timestamp"],
                    "environment": env,
                    "type": "toggle",
                    "key": name,
                    "old": old_val,
                    "new": new_val,
                    "user": change["user"],
                })
            data["Toggles"] = new_toggles

            # ── Apply override changes ──
            old_overrides = data.get("TogglesOverrides", {})
            new_overrides: dict[str, dict] = {k: dict(v) for k, v in old_overrides.items()}
            for toggle_key, user_changes in staged_overrides.items():
                new_overrides.setdefault(toggle_key, {})
                for user_id, change in user_changes.items():
                    value = change["value"]
                    old_val = old_overrides.get(toggle_key, {}).get(user_id)
                    if value is _DELETED:
                        new_overrides[toggle_key].pop(user_id, None)
                        new_val = None
                    else:
                        new_overrides[toggle_key][user_id] = value
                        new_val = value
                    log_entries.append({
                        "timestamp": change["timestamp"],
                        "environment": env,
                        "type": "override",
                        "key": toggle_key,
                        "userId": user_id,
                        "old": old_val,
                        "new": new_val,
                        "user": change["user"],
                    })
                if not new_overrides[toggle_key]:
                    del new_overrides[toggle_key]

            if new_overrides:
                data["TogglesOverrides"] = new_overrides
            elif "TogglesOverrides" in data:
                del data["TogglesOverrides"]

            _atomic_write(path, data)
            _append_log_entries(log_entries)

        except Exception:
            with _lock:
                if staged_toggles:
                    merged = staged_toggles.copy()
                    merged.update(_buffer.get(env, {}))
                    _buffer[env] = merged
                if staged_overrides:
                    for toggle_key, user_changes in staged_overrides.items():
                        existing = _override_buffer.setdefault(env, {}).setdefault(toggle_key, {})
                        merged_ov = user_changes.copy()
                        merged_ov.update(existing)
                        _override_buffer[env][toggle_key] = merged_ov

    _schedule_flush()


def _schedule_flush() -> None:
    global _flush_timer
    interval = _flush_interval_seconds()
    _flush_timer = threading.Timer(interval, _flush)
    _flush_timer.daemon = True
    _flush_timer.start()


@app.on_event("startup")
def startup() -> None:
    _schedule_flush()


@app.on_event("shutdown")
def shutdown() -> None:
    if _flush_timer:
        _flush_timer.cancel()
    _flush()


# ── API ───────────────────────────────────────────────────────────────────────

@app.get("/api/environments")
def list_environments():
    return _discover_environments()


@app.get("/api/environments/paths")
def environment_paths():
    return {
        env: LABEL_MAP.get(env, str(_effective_file(env)))
        for env in ENV_MAP
    }


@app.get("/api/toggles")
def list_toggles(env: str = Query(default="Base")):
    with _lock:
        merged = _read_merged_toggles(env)
        staged = _buffer.get(env, {}).copy()
    for name, change in staged.items():
        if change["value"] is _DELETED:
            merged.pop(name, None)
        else:
            merged[name] = change["value"]
    return merged


class ToggleValue(BaseModel):
    value: bool | str | int | float


@app.put("/api/toggles/{name}")
def upsert_toggle(
    name: str,
    body: ToggleValue,
    request: Request,
    env: str = Query(default="Base"),
    credentials: HTTPBasicCredentials | None = Depends(_security),
):
    user = _extract_user(request, credentials)
    with _lock:
        _buffer.setdefault(env, {})[name] = {
            "value": body.value,
            "user": user,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    return {"ok": True}


@app.delete("/api/toggles/{name}")
def delete_toggle(
    name: str,
    request: Request,
    env: str = Query(default="Base"),
    credentials: HTTPBasicCredentials | None = Depends(_security),
):
    user = _extract_user(request, credentials)
    with _lock:
        merged = _read_merged_toggles(env)
        staged = _buffer.get(env, {})
        in_merged = name in merged
        in_buffer = name in staged and staged[name]["value"] is not _DELETED
        if not in_merged and not in_buffer:
            raise HTTPException(status_code=404, detail="Toggle not found")
        _buffer.setdefault(env, {})[name] = {
            "value": _DELETED,
            "user": user,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    return {"ok": True}


# ── Overrides API ─────────────────────────────────────────────────────────────

@app.get("/api/overrides")
def get_overrides(env: str = Query(default="Base")):
    return _read_effective_overrides(env)


class OverrideValue(BaseModel):
    value: bool


@app.put("/api/overrides/{toggle_name}/{user_id}")
def set_override(
    toggle_name: str,
    user_id: str,
    body: OverrideValue,
    request: Request,
    env: str = Query(default="Base"),
    credentials: HTTPBasicCredentials | None = Depends(_security),
):
    user = _extract_user(request, credentials)
    with _lock:
        _override_buffer.setdefault(env, {}).setdefault(toggle_name, {})[user_id] = {
            "value": body.value,
            "user": user,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    return {"ok": True}


@app.delete("/api/overrides/{toggle_name}/{user_id}")
def delete_override(
    toggle_name: str,
    user_id: str,
    request: Request,
    env: str = Query(default="Base"),
    credentials: HTTPBasicCredentials | None = Depends(_security),
):
    user = _extract_user(request, credentials)
    with _lock:
        stored = _read_stored_overrides(env)
        staged = _override_buffer.get(env, {}).get(toggle_name, {})
        in_stored = toggle_name in stored and user_id in stored[toggle_name]
        in_buffer = user_id in staged and staged[user_id]["value"] is not _DELETED
        if not in_stored and not in_buffer:
            raise HTTPException(status_code=404, detail="Override not found")
        _override_buffer.setdefault(env, {}).setdefault(toggle_name, {})[user_id] = {
            "value": _DELETED,
            "user": user,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    return {"ok": True}


# ── Log + Health ──────────────────────────────────────────────────────────────

@app.get("/api/log")
def get_log(limit: int = Query(default=200, le=1000)):
    return _read_log(limit)


@app.get("/api/health")
def health():
    pending_toggles = sum(
        sum(1 for c in changes.values() if c["value"] is not _DELETED)
        for changes in _buffer.values()
    )
    pending_overrides = sum(
        sum(
            sum(1 for c in user_map.values() if c["value"] is not _DELETED)
            for user_map in toggle_map.values()
        )
        for toggle_map in _override_buffer.values()
    )
    return {
        "path": str(APPSETTINGS_PATH),
        "exists": APPSETTINGS_PATH.exists(),
        "app_name": APP_NAME,
        "flush_interval": _flush_interval_seconds(),
        "pending_changes": pending_toggles + pending_overrides,
        "warnings": DIR_WARNINGS,
    }


# ── Static UI ─────────────────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

@app.get("/", response_class=HTMLResponse)
def index():
    html_path = Path(__file__).parent / "static" / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))
