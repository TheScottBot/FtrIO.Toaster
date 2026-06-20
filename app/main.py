import json
import os
import secrets
import tempfile
import threading
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

APP_NAME = os.environ.get("APP_NAME", "")
AUTH_USERNAME = os.environ.get("AUTH_USERNAME", "")
AUTH_PASSWORD = os.environ.get("AUTH_PASSWORD", "")
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

def _build_env_map() -> dict[str, Path]:
    """
    Build the environment → file path map from environment variables.
    APPSETTINGS_PATH       → "Base"
    APPSETTINGS_PATH_FOO   → "Foo"
    APPSETTINGS_PATH_MY_ENV → "My_Env"  (underscores preserved as-is)
    """
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

# Resolved once at startup; immutable thereafter.
ENV_MAP: dict[str, Path] = _build_env_map()
APPSETTINGS_PATH = ENV_MAP["Base"]


def _effective_file(env: str) -> Path:
    """The actual file path that will be read/written for this environment.
    Always the explicitly configured path — never derived from the env name."""
    return ENV_MAP[env]


def _dir_identity(path: Path) -> object:
    """
    Return a hashable identity for a directory that is the same even when the
    same host directory is mounted at two different container paths.
    Falls back to the resolved path string if stat is unavailable.
    """
    try:
        s = os.stat(path)
        return (s.st_dev, s.st_ino)
    except OSError:
        return str(path.resolve())


def _build_dir_warnings() -> list[str]:
    """
    Warn only when two or more environments resolve to the exact same physical
    file — writes from one would silently overwrite the other.
    Sharing a directory is valid (centralised config) and produces no warning.
    """
    from collections import defaultdict

    by_file: dict[object, list[str]] = defaultdict(list)
    for env in ENV_MAP:
        f = _effective_file(env)
        key = _dir_identity(f)  # reuse inode logic on the file itself
        by_file[key].append(env)

    warnings = []
    for file_id, envs in by_file.items():
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

_lock = threading.Lock()

# Buffer keyed by environment name ("Base" or e.g. "Staging").
# Each value is a dict of toggle name → staged value (or _DELETED sentinel).
_DELETED = object()
_buffer: dict[str, dict] = {}
_flush_timer: threading.Timer | None = None

app = FastAPI(title="FtrIO Toaster", dependencies=[Depends(require_auth)])


# ── File resolution ───────────────────────────────────────────────────────────

def _env_path(env: str) -> Path:
    if env not in ENV_MAP:
        raise KeyError(f"Unknown environment: {env}")
    return _effective_file(env)


def _discover_environments() -> list[str]:
    return list(ENV_MAP.keys())


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
    """Each environment is an independent file — read it directly, no merging."""
    return _read_file(_env_path(env)).get("Toggles", {})


# ── Buffer / flush ─────────────────────────────────────────────────────────────

def _flush_interval_seconds() -> float:
    try:
        data = _read_file(APPSETTINGS_PATH)
        return float(data.get("FtrIO", {}).get("FlushInterval", 5))
    except Exception:
        return 5.0


def _flush() -> None:
    global _buffer, _flush_timer

    with _lock:
        snapshot = {env: changes.copy() for env, changes in _buffer.items()}
        _buffer = {}

    for env, staged in snapshot.items():
        if not staged:
            continue
        path = _env_path(env)
        try:
            with _lock:
                data = _read_file(path)
            toggles = data.get("Toggles", {})
            for name, value in staged.items():
                if value is _DELETED:
                    toggles.pop(name, None)
                else:
                    toggles[name] = value
            data["Toggles"] = toggles
            _atomic_write(path, data)
        except Exception:
            with _lock:
                merged = staged.copy()
                merged.update(_buffer.get(env, {}))
                _buffer[env] = merged

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


@app.get("/api/toggles")
def list_toggles(env: str = Query(default="Base")):
    """
    Returns the merged effective toggle set for the given environment,
    with staged buffer changes applied on top.
    """
    with _lock:
        merged = _read_merged_toggles(env)
        staged = _buffer.get(env, {}).copy()

    for name, value in staged.items():
        if value is _DELETED:
            merged.pop(name, None)
        else:
            merged[name] = value
    return merged


class ToggleValue(BaseModel):
    value: bool | str | int | float


@app.put("/api/toggles/{name}")
def upsert_toggle(name: str, body: ToggleValue, env: str = Query(default="Base")):
    with _lock:
        _buffer.setdefault(env, {})[name] = body.value
    return {"ok": True}


@app.delete("/api/toggles/{name}")
def delete_toggle(name: str, env: str = Query(default="Base")):
    with _lock:
        merged = _read_merged_toggles(env)
        staged = _buffer.get(env, {})
        in_merged = name in merged
        in_buffer = name in staged and staged[name] is not _DELETED
        if not in_merged and not in_buffer:
            raise HTTPException(status_code=404, detail="Toggle not found")
        _buffer.setdefault(env, {})[name] = _DELETED
    return {"ok": True}


@app.get("/api/health")
def health():
    pending = sum(
        sum(1 for v in changes.values() if v is not _DELETED)
        for changes in _buffer.values()
    )
    return {
        "path": str(APPSETTINGS_PATH),
        "exists": APPSETTINGS_PATH.exists(),
        "app_name": APP_NAME,
        "flush_interval": _flush_interval_seconds(),
        "pending_changes": pending,
        "warnings": DIR_WARNINGS,
    }


# ── Static UI ─────────────────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

@app.get("/", response_class=HTMLResponse)
def index():
    html_path = Path(__file__).parent / "static" / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))
