from __future__ import annotations

import ctypes
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .version import APP_NAME, APP_VERSION

GITHUB_REPOSITORY = "no-strand/glean"
GITHUB_REPOSITORY_URL = f"https://github.com/{GITHUB_REPOSITORY}"
GITHUB_RELEASES_URL = f"{GITHUB_REPOSITORY_URL}/releases"
GITHUB_LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"


class UpdateError(RuntimeError):
    pass


class UpdateCancelled(UpdateError):
    pass


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    download_url: str
    size: int = 0
    digest: str = ""

    @property
    def suffix(self) -> str:
        return Path(self.name).suffix.lower()


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    tag_name: str
    title: str
    html_url: str
    notes: str
    assets: tuple[ReleaseAsset, ...]


_VERSION_RE = re.compile(
    r"^\s*[vV]?(?P<numbers>\d+(?:\.\d+){0,3})(?:[-_]?)(?P<pre>(?:alpha|a|beta|b|rc|pre|preview)\d*)?",
    re.IGNORECASE,
)


def _version_key(value: str) -> tuple[tuple[int, int, int, int], int, str]:
    """Return a small semantic-version key without adding a runtime dependency."""
    match = _VERSION_RE.match(str(value or ""))
    if not match:
        return ((0, 0, 0, 0), 0, str(value or "").casefold())
    numbers = [int(part) for part in match.group("numbers").split(".")]
    numbers.extend([0] * (4 - len(numbers)))
    pre = (match.group("pre") or "").casefold()
    # Stable releases sort after prereleases sharing the same numeric version.
    stable_rank = 1 if not pre else 0
    return (tuple(numbers[:4]), stable_rank, pre)


def is_newer_version(candidate: str, current: str = APP_VERSION) -> bool:
    return _version_key(candidate) > _version_key(current)


def versions_equal(left: str, right: str) -> bool:
    return _version_key(left) == _version_key(right)


def _request(url: str, timeout: float = 15.0):
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"{APP_NAME}/{APP_VERSION}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    return urllib.request.urlopen(request, timeout=timeout)


def parse_release_payload(payload: dict) -> ReleaseInfo:
    tag_name = str(payload.get("tag_name") or "").strip()
    if not tag_name:
        raise UpdateError("release_without_version")
    version_match = _VERSION_RE.match(tag_name)
    version = version_match.group("numbers") if version_match else tag_name.lstrip("vV")

    assets: list[ReleaseAsset] = []
    raw_assets = payload.get("assets")
    if isinstance(raw_assets, list):
        for item in raw_assets:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            url = str(item.get("browser_download_url") or "").strip()
            if not name or not url:
                continue
            try:
                size = max(0, int(item.get("size") or 0))
            except (TypeError, ValueError):
                size = 0
            assets.append(
                ReleaseAsset(
                    name=name,
                    download_url=url,
                    size=size,
                    digest=str(item.get("digest") or "").strip(),
                )
            )

    return ReleaseInfo(
        version=version,
        tag_name=tag_name,
        title=str(payload.get("name") or tag_name).strip(),
        html_url=str(payload.get("html_url") or GITHUB_RELEASES_URL).strip(),
        notes=str(payload.get("body") or "").strip(),
        assets=tuple(assets),
    )


def fetch_latest_release(timeout: float = 15.0) -> ReleaseInfo:
    try:
        with _request(GITHUB_LATEST_RELEASE_API, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("no_releases") from exc
        if exc.code == 403:
            raise UpdateError("github_rate_limit") from exc
        raise UpdateError(f"http_{exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError("network_error") from exc

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError("invalid_response") from exc
    if not isinstance(payload, dict):
        raise UpdateError("invalid_response")
    return parse_release_payload(payload)


def _asset_score(asset: ReleaseAsset) -> tuple[int, int, int, str]:
    name = asset.name.casefold()
    suffix = asset.suffix
    if suffix not in {".exe", ".msi", ".zip"}:
        return (-1, -1, -1, name)

    kind = 0
    if suffix == ".exe":
        kind = 300
        if "setup" in name or "installer" in name:
            kind += 80
    elif suffix == ".msi":
        kind = 260
    elif suffix == ".zip":
        kind = 180

    product = 30 if "glean" in name else 0
    machine = platform.machine().casefold()
    architecture = 0
    if machine in {"amd64", "x86_64"}:
        if any(token in name for token in ("x64", "amd64", "x86_64", "win64")):
            architecture += 20
        if any(token in name for token in ("arm64", "aarch64")):
            architecture -= 80
    elif machine in {"arm64", "aarch64"}:
        if any(token in name for token in ("arm64", "aarch64")):
            architecture += 20
        if any(token in name for token in ("x64", "amd64", "x86_64")):
            architecture -= 40

    return (kind, product, architecture, name)


def select_update_asset(release: ReleaseInfo) -> ReleaseAsset | None:
    candidates = [asset for asset in release.assets if _asset_score(asset)[0] >= 0]
    if not candidates:
        return None
    return max(candidates, key=_asset_score)


def _expected_sha256(asset: ReleaseAsset) -> str:
    digest = asset.digest.strip().lower()
    if digest.startswith("sha256:"):
        value = digest.partition(":")[2].strip()
        if re.fullmatch(r"[0-9a-f]{64}", value):
            return value
    return ""


def download_release_asset(
    asset: ReleaseAsset,
    destination: str | Path,
    cancel_event: threading.Event,
    progress_callback: Callable[[int, int], None] | None = None,
    timeout: float = 30.0,
) -> Path:
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    hasher = hashlib.sha256()

    request = urllib.request.Request(
        asset.download_url,
        headers={
            "Accept": "application/octet-stream",
            "User-Agent": f"{APP_NAME}/{APP_VERSION}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, part.open("wb") as handle:
            header_total = response.headers.get("Content-Length")
            try:
                total = max(0, int(header_total or asset.size or 0))
            except (TypeError, ValueError):
                total = max(0, asset.size)
            downloaded = 0
            while True:
                if cancel_event.is_set():
                    raise UpdateCancelled("cancelled")
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                hasher.update(chunk)
                downloaded += len(chunk)
                if progress_callback is not None:
                    progress_callback(downloaded, total)
    except UpdateCancelled:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        raise UpdateError("download_failed") from exc

    expected = _expected_sha256(asset)
    if expected and hasher.hexdigest().lower() != expected:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        raise UpdateError("checksum_failed")

    try:
        os.replace(part, target)
    except OSError as exc:
        raise UpdateError("download_failed") from exc
    return target


def update_download_path(asset: ReleaseAsset) -> Path:
    root = Path(tempfile.gettempdir()) / "Glean" / "updates"
    safe_name = Path(asset.name).name or f"Glean_Update{asset.suffix}"
    return root / safe_name


def _ps_quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _write_update_script(package: Path, app_exe: Path, parent_pid: int) -> Path:
    script_dir = Path(tempfile.gettempdir()) / "Glean" / "updates"
    script_dir.mkdir(parents=True, exist_ok=True)
    script = script_dir / "apply_glean_update.ps1"
    install_dir = app_exe.parent
    local_appdata = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "Glean"
    error_log = local_appdata / "update_error.log"

    content = f'''$ErrorActionPreference = "Stop"
$package = {_ps_quote(package)}
$appExe = {_ps_quote(app_exe)}
$installDir = {_ps_quote(install_dir)}
$errorLog = {_ps_quote(error_log)}
$parentPid = {int(parent_pid)}

try {{
    while (Get-Process -Id $parentPid -ErrorAction SilentlyContinue) {{
        Start-Sleep -Milliseconds 300
    }}
    Start-Sleep -Milliseconds 500

    $extension = [System.IO.Path]::GetExtension($package).ToLowerInvariant()
    if ($extension -eq ".exe") {{
        $arguments = @(
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/CLOSEAPPLICATIONS",
            ('/DIR="' + $installDir + '"')
        )
        $process = Start-Process -FilePath $package -ArgumentList $arguments -Wait -PassThru
        if ($process.ExitCode -ne 0) {{ throw "Installer exited with code $($process.ExitCode)" }}
    }} elseif ($extension -eq ".msi") {{
        $arguments = @("/i", $package, "/qn", "/norestart", ('INSTALLDIR=' + $installDir))
        $process = Start-Process -FilePath "msiexec.exe" -ArgumentList $arguments -Wait -PassThru
        if ($process.ExitCode -ne 0) {{ throw "MSI exited with code $($process.ExitCode)" }}
    }} elseif ($extension -eq ".zip") {{
        $stage = Join-Path $env:TEMP ("GleanUpdate_" + [guid]::NewGuid().ToString("N"))
        New-Item -ItemType Directory -Path $stage -Force | Out-Null
        Expand-Archive -LiteralPath $package -DestinationPath $stage -Force
        $candidate = Get-ChildItem -LiteralPath $stage -Filter "Glean.exe" -File -Recurse | Select-Object -First 1
        if (-not $candidate) {{ throw "Glean.exe was not found in the update package." }}
        $payload = $candidate.Directory.FullName
        Copy-Item -Path (Join-Path $payload "*") -Destination $installDir -Recurse -Force
        Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
    }} else {{
        throw "Unsupported update package: $extension"
    }}

    Remove-Item -LiteralPath $errorLog -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $appExe) {{
        Start-Process -FilePath $appExe
    }}
    Remove-Item -LiteralPath $package -Force -ErrorAction SilentlyContinue
}} catch {{
    New-Item -ItemType Directory -Path ([System.IO.Path]::GetDirectoryName($errorLog)) -Force | Out-Null
    $_ | Out-String | Set-Content -LiteralPath $errorLog -Encoding UTF8
    if (Test-Path -LiteralPath $appExe) {{
        Start-Process -FilePath $appExe
    }}
}} finally {{
    Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
}}
'''
    script.write_text(content, encoding="utf-8-sig")
    return script


def schedule_update_install(package_path: str | Path) -> None:
    if sys.platform != "win32":
        raise UpdateError("automatic_update_windows_only")
    if not getattr(sys, "frozen", False):
        raise UpdateError("automatic_update_frozen_only")

    package = Path(package_path).resolve()
    if package.suffix.lower() not in {".exe", ".msi", ".zip"} or not package.is_file():
        raise UpdateError("unsupported_package")

    app_exe = Path(sys.executable).resolve()
    script = _write_update_script(package, app_exe, os.getpid())

    params = f'-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{script}"'
    result = ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
        None,
        "runas",
        "powershell.exe",
        params,
        str(script.parent),
        0,
    )
    if int(result) <= 32:
        raise UpdateError("elevation_cancelled")
