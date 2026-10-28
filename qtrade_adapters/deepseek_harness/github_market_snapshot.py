"""Transfer a verified portal and research snapshot as one bounded archive.

The archive contains only immutable market generations and their pointers. It
never contains paper accounts, credentials, model files, or local caches.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
import zipfile
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

from . import portal_refresh, snapshot_pipeline


MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_UNPACKED_BYTES = 256 * 1024 * 1024
PORTAL_FILES = ("bars_incr.db", "metadata.json", "manifest.json")
PIPELINE_FILES = ("portal_ref.json", "factors.json", "decision.json", "sync.json", "manifest.json")


class MarketSnapshotError(RuntimeError):
    """The source bundle cannot be trusted or installed."""


def _paths(user_data_dir: str | Path):
    user_data = Path(user_data_dir)
    state = user_data / "state"
    portal = portal_refresh.portal_refresh_paths(state, user_data_dir=user_data)
    pipeline = snapshot_pipeline._paths(state, user_data)
    return state, portal, pipeline


def _verified_pair(user_data_dir: str | Path):
    state, portal_paths, pipeline_paths = _paths(user_data_dir)
    portal = portal_refresh.read_current_snapshot(state, user_data_dir=user_data_dir)
    pipeline = snapshot_pipeline.read_current_pipeline(state, user_data_dir=user_data_dir)
    if portal is None or pipeline is None:
        raise MarketSnapshotError("snapshot_unavailable")
    if pipeline.manifest["portal_generation"] != portal.manifest["generation"] or pipeline.manifest["target_date"] != portal.manifest["target_date"]:
        raise MarketSnapshotError("snapshot_pair_mismatch")
    return portal_paths, pipeline_paths, portal, pipeline


def export_bundle(user_data_dir: str | Path, output: str | Path) -> dict[str, object]:
    """Write a complete, verified bundle and return its date and SHA-256."""

    portal_paths, pipeline_paths, portal, pipeline = _verified_pair(user_data_dir)
    portal_gen = portal.manifest["generation"]
    pipeline_gen = pipeline.manifest["generation"]
    members = {
        "portal/current.json": portal_paths.current,
        "pipeline/current.json": pipeline_paths.current,
        **{f"portal/generations/{portal_gen}/{name}": portal_paths.generation_dir(portal_gen) / name for name in PORTAL_FILES},
        **{f"pipeline/generations/{pipeline_gen}/{name}": pipeline_paths.generation(pipeline_gen) / name for name in PIPELINE_FILES},
    }
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    staging = output_path.with_name(f".{output_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with zipfile.ZipFile(staging, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
            for name, path in members.items():
                archive.write(path, name)
        if staging.stat().st_size > MAX_ARCHIVE_BYTES:
            raise MarketSnapshotError("bundle_too_large")
        # Refuse a concurrent pointer switch while writing the archive.
        _, _, current_portal, current_pipeline = _verified_pair(user_data_dir)
        if (current_portal.manifest["generation"], current_pipeline.manifest["generation"]) != (portal_gen, pipeline_gen):
            raise MarketSnapshotError("snapshot_changed")
        os.replace(staging, output_path)
        with output_path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        return {"target_date": portal.manifest["target_date"], "sha256": digest, "bytes": output_path.stat().st_size}
    finally:
        staging.unlink(missing_ok=True)


def _extract_verified_bundle(bundle: Path, temporary_user_data: Path):
    if not bundle.is_file() or bundle.stat().st_size > MAX_ARCHIVE_BYTES:
        raise MarketSnapshotError("bundle_missing_or_large")
    state, _, _ = _paths(temporary_user_data)
    try:
        with zipfile.ZipFile(bundle) as archive:
            names = archive.namelist()
            if len(names) != 10 or len(set(names)) != len(names):
                raise MarketSnapshotError("bundle_layout_invalid")
            if any(item.file_size > portal_refresh.MAX_POINTER_BYTES for item in archive.infolist() if item.filename.endswith("current.json")):
                raise MarketSnapshotError("bundle_layout_invalid")
            portal_pointer = json.loads(archive.read("portal/current.json"))
            pipeline_pointer = json.loads(archive.read("pipeline/current.json"))
            portal_gen = portal_refresh._validate_token(portal_pointer["generation"])
            pipeline_gen = snapshot_pipeline._token(pipeline_pointer["generation"])
            expected = {
                "portal/current.json", "pipeline/current.json",
                *(f"portal/generations/{portal_gen}/{name}" for name in PORTAL_FILES),
                *(f"pipeline/generations/{pipeline_gen}/{name}" for name in PIPELINE_FILES),
            }
            if set(names) != expected or sum(item.file_size for item in archive.infolist()) > MAX_UNPACKED_BYTES:
                raise MarketSnapshotError("bundle_layout_invalid")
            for item in archive.infolist():
                if item.is_dir() or item.flag_bits & 1 or item.file_size > MAX_UNPACKED_BYTES:
                    raise MarketSnapshotError("bundle_layout_invalid")
                destination = state / ("portal_refresh" if item.filename.startswith("portal/") else "snapshot_pipeline") / item.filename.split("/", 1)[1]
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(item) as source, destination.open("xb") as sink:
                    shutil.copyfileobj(source, sink, length=1024 * 1024)
    except (KeyError, ValueError, TypeError, zipfile.BadZipFile, OSError, portal_refresh.PortalRefreshError, snapshot_pipeline.SnapshotPipelineError) as exc:
        raise MarketSnapshotError("bundle_invalid") from exc
    portal = portal_refresh.read_current_snapshot(state, user_data_dir=temporary_user_data)
    pipeline = snapshot_pipeline.read_current_pipeline(state, user_data_dir=temporary_user_data)
    if portal is None or pipeline is None or pipeline.manifest["portal_generation"] != portal.manifest["generation"] or pipeline.manifest["target_date"] != portal.manifest["target_date"]:
        raise MarketSnapshotError("bundle_verification_failed")
    return portal, pipeline


def import_bundle(
    user_data_dir: str | Path,
    bundle: str | Path,
    *,
    expected_sha256: str | None = None,
    expected_target_date: str | None = None,
    prepare_fn=None,
    activate_fn=None,
    commit_fn=None,
    stop_event=None,
) -> str:
    """Install a newer verified pair; leave the current pair on any failure."""

    bundle_path = Path(bundle)
    if expected_sha256 is not None:
        with bundle_path.open("rb") as source:
            actual_digest = hashlib.file_digest(source, "sha256").hexdigest()
        if re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256) is None or actual_digest != expected_sha256.lower():
            raise MarketSnapshotError("bundle_digest_mismatch")
    state, portal_paths, pipeline_paths = _paths(user_data_dir)
    current = snapshot_pipeline.read_current_pipeline(state, user_data_dir=user_data_dir)
    with tempfile.TemporaryDirectory(prefix="qtrade-market-") as temporary:
        source_data = Path(temporary) / "user-data"
        portal, pipeline = _extract_verified_bundle(bundle_path, source_data)
        date = portal.manifest["target_date"]
        if expected_target_date is not None and date != expected_target_date:
            raise MarketSnapshotError("bundle_date_mismatch")
        if current is not None and date <= current.manifest["target_date"]:
            return "already_current"
        if prepare_fn is not None:
            try:
                prepared = prepare_fn(pipeline)
            except Exception as exc:
                raise MarketSnapshotError("bundle_preflight_failed") from exc
            if prepared is not True:
                raise MarketSnapshotError("bundle_preflight_failed")
        old_portal_pointer = portal_paths.current.read_bytes() if portal_paths.current.is_file() else None
        old_pipeline_pointer = pipeline_paths.current.read_bytes() if pipeline_paths.current.is_file() else None
        source_state, source_portal, source_pipeline = _paths(source_data)
        del source_state
        for destination_paths, source_paths, generation in (
            (portal_paths, source_portal, portal.manifest["generation"]),
            (pipeline_paths, source_pipeline, pipeline.manifest["generation"]),
        ):
            destination_paths.generations.mkdir(parents=True, exist_ok=True)
            destination = destination_paths.generations / generation
            if destination.exists():
                continue
            staging = destination_paths.generations / f".staging-{uuid.uuid4().hex}"
            try:
                shutil.copytree(source_paths.generations / generation, staging)
                os.replace(staging, destination)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
        # Revalidate after copying, including any preexisting generation names.
        if portal_refresh.read_generation_snapshot(state, user_data_dir=user_data_dir, generation=portal.manifest["generation"]) is None or snapshot_pipeline.read_pipeline_generation(state, user_data_dir=user_data_dir, generation=pipeline.manifest["generation"]) is None:
            raise MarketSnapshotError("installed_generation_invalid")
        if stop_event is not None and stop_event.is_set():
            raise MarketSnapshotError("aborted")
        try:
            portal_paths.root.mkdir(parents=True, exist_ok=True)
            pipeline_paths.root.mkdir(parents=True, exist_ok=True)
            portal_refresh._atomic_json(portal_paths.current, json.loads(source_portal.current.read_text(encoding="utf-8")), ignore_post_fsync_error=True)
            portal_refresh._atomic_json(pipeline_paths.current, json.loads(source_pipeline.current.read_text(encoding="utf-8")), ignore_post_fsync_error=True)
            _, _, _, active_pipeline = _verified_pair(user_data_dir)
            if stop_event is not None and stop_event.is_set():
                raise MarketSnapshotError("aborted")
            if activate_fn is not None and activate_fn(active_pipeline) is not True:
                raise MarketSnapshotError("reload_failed")
            if commit_fn is not None and commit_fn(active_pipeline) is not True:
                raise MarketSnapshotError("reload_failed")
        except Exception as exc:
            snapshot_pipeline._restore_pointer(pipeline_paths, old_pipeline_pointer)
            snapshot_pipeline._restore_portal_pointer(portal_paths.current, old_portal_pointer)
            if commit_fn is not None:
                try:
                    commit_fn(current)
                except Exception:
                    pass
            raise MarketSnapshotError("bundle_activation_failed") from exc
        return date


def download_latest_bundle(repository: str, token: str, output: str | Path) -> dict[str, object]:
    """Download the newest private Release asset with an authenticated API call."""

    import requests

    parts = repository.split("/")
    if (len(parts) != 2 or any(part in {".", ".."} for part in parts)
            or re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is None or not token):
        raise MarketSnapshotError("github_configuration_invalid")
    api = f"https://api.github.com/repos/{repository}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    try:
        release_response = requests.get(f"{api}/releases/latest", headers=headers, timeout=30)
        release_response.raise_for_status()
        release = release_response.json()
        assets = [item for item in release.get("assets", ()) if item.get("name") == "qtrade-market.zip" and item.get("state") == "uploaded"]
        if len(assets) != 1:
            raise MarketSnapshotError("release_asset_missing")
        asset = assets[0]
        digest = asset.get("digest", "")
        if not isinstance(digest, str) or re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest) is None or not isinstance(asset.get("id"), int):
            raise MarketSnapshotError("release_digest_missing")
        if not isinstance(asset.get("size"), int) or not 0 < asset["size"] <= MAX_ARCHIVE_BYTES:
            raise MarketSnapshotError("release_asset_too_large")
        asset_response = requests.get(
            f"{api}/releases/assets/{asset['id']}",
            headers={**headers, "Accept": "application/octet-stream"},
            timeout=30,
            allow_redirects=False,
            stream=True,
        )
        if asset_response.status_code in {301, 302, 303, 307, 308}:
            location = asset_response.headers.get("Location", "")
            host = urlparse(location)
            if host.scheme != "https" or host.hostname not in {"objects.githubusercontent.com", "release-assets.githubusercontent.com", "github.com"}:
                raise MarketSnapshotError("asset_redirect_invalid")
            asset_response.close()
            asset_response = requests.get(location, timeout=(30, 120), stream=True)
        asset_response.raise_for_status()
        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        staging = output_path.with_name(f".{output_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            actual = hashlib.sha256()
            total = 0
            with staging.open("xb") as target:
                for chunk in asset_response.iter_content(1024 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_ARCHIVE_BYTES:
                        raise MarketSnapshotError("release_asset_too_large")
                    target.write(chunk)
                    actual.update(chunk)
            if total != asset["size"] or actual.hexdigest() != digest[7:].lower():
                raise MarketSnapshotError("release_digest_mismatch")
            os.replace(staging, output_path)
        finally:
            asset_response.close()
            staging.unlink(missing_ok=True)
        return {"target_date": release.get("tag_name"), "sha256": actual.hexdigest(), "bytes": total}
    except requests.RequestException as exc:
        raise MarketSnapshotError("github_download_failed") from exc


def run_cloud_first(base_dir, target_date, **kwargs) -> int:
    """Let the existing update button consume a matching private Release first."""

    environment = kwargs.get("environment") or os.environ
    repository = environment.get("QTRADE_DATA_GITHUB_REPO", "")
    token = environment.get("QTRADE_DATA_GITHUB_TOKEN", "")
    if not repository or not token:
        return snapshot_pipeline.run_snapshot_pipeline(base_dir, target_date, **kwargs)
    target = target_date.isoformat() if isinstance(target_date, date) else str(target_date)
    status_path = Path(kwargs["status_file"])
    user_data = Path(kwargs["user_data_dir"])
    manual_lease = None
    if kwargs.get("lease_token") is not None:
        if kwargs.get("lease_path") is None:
            return 1
        manual_lease = snapshot_pipeline._PipelineLease(Path(kwargs["lease_path"]), kwargs["lease_token"])
        if not manual_lease.acquire():
            return 1
    try:
        lease = snapshot_pipeline._PipelineLease(status_path.with_name("daily_update_1830.lock"))
        if not lease.acquire():
            return 1
        try:
            with tempfile.TemporaryDirectory(prefix="qtrade-github-") as temporary:
                bundle = Path(temporary) / "qtrade-market.zip"
                release = download_latest_bundle(repository, token, bundle)
                if release["target_date"] == f"data-{target}":
                    result = import_bundle(
                        user_data, bundle, expected_sha256=release["sha256"],
                        expected_target_date=target,
                        prepare_fn=kwargs.get("prepare_fn"),
                        activate_fn=kwargs.get("activate_fn"),
                        commit_fn=kwargs.get("commit_fn"), stop_event=kwargs.get("stop_event"),
                    )
                    active = snapshot_pipeline.read_current_pipeline(user_data / "state", user_data_dir=user_data)
                    if result in {target, "already_current"} and active is not None and active.manifest["target_date"] == target:
                        now = datetime.now().isoformat(timespec="seconds")
                        total = active.manifest["total"]
                        payload = snapshot_pipeline._status_payload(
                            "success", "completed", target, now,
                            step="sync", finished_at=now, job_id=kwargs.get("job_id"),
                            outputs={key: True for key in ("portal", "factors", "decision", "sync")},
                            progress={"completed": 4, "total": 4, "current": None},
                            stock_progress={"completed": total, "total": total, "failed": 0, "pending": 0},
                            current_complete_date=target, current_portal_date=target,
                            output_meta={"pipeline": {"generation": active.manifest["generation"], "target_date": target, "total": total}},
                        )
                        snapshot_pipeline._write_status(status_path, payload)
                        return 0
        except (MarketSnapshotError, OSError, ValueError):
            # The local provider remains available if GitHub has not published a
            # matching verified day or is temporarily unreachable.
            pass
        finally:
            lease.release()
    finally:
        if manual_lease is not None:
            manual_lease.release()
    return snapshot_pipeline.run_snapshot_pipeline(base_dir, target_date, **kwargs)
