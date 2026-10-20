#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib import error, parse, request

import pack_mods


ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "ModZips"
MOD_PORTAL_URL = "https://mods.factorio.com"
TOKEN_ENV = "FACTORIO_MOD_PORTAL_TOKEN"
CONFIG_TOKEN_KEY = "factorio_mod_portal_token"
DEFAULT_CONFIG_PATH = ROOT / "publish_mods.config.json"
USER_AGENT = "MLJ-Factorio-Mod-Publisher/1.0"

ACTION_SKIP = "skip"
ACTION_UPLOAD = "upload"
ACTION_CREATE = "create"
ACTION_ERROR = "error"
PUBLISH_ACTIONS = {ACTION_UPLOAD, ACTION_CREATE}


@dataclass(frozen=True)
class PublishDecision:
    project: pack_mods.ModProject
    action: str
    local_version: str
    remote_version: str | None
    reason: str
    zip_path: Path | None = None


class PortalApiError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, payload: object | None = None):
        super().__init__(message)
        self.status = status
        self.payload = payload


class ModPortalClient:
    def __init__(self, base_url: str = MOD_PORTAL_URL, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def get_mod_details(self, mod_name: str) -> dict[str, Any] | None:
        url = f"{self.base_url}/api/mods/{parse.quote(mod_name, safe='')}/full"
        req = request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
        try:
            payload = self._open_json(req)
        except PortalApiError as exc:
            if exc.status == 404 or _payload_error(exc.payload) == "UnknownMod":
                return None
            raise

        if not isinstance(payload, dict):
            raise PortalApiError(f"Unexpected details response for {mod_name}: expected JSON object", payload=payload)
        return payload

    def upload_existing_release(self, token: str, project: pack_mods.ModProject, zip_path: Path) -> dict[str, Any]:
        upload_url = self._init_upload(
            f"{self.base_url}/api/v2/mods/releases/init_upload",
            token,
            project.name,
        )
        return self._finish_upload(upload_url, zip_path)

    def publish_new_mod(
        self,
        token: str,
        project: pack_mods.ModProject,
        zip_path: Path,
        fields: Mapping[str, str],
    ) -> dict[str, Any]:
        upload_url = self._init_upload(
            f"{self.base_url}/api/v2/mods/init_publish",
            token,
            project.name,
        )
        return self._finish_upload(upload_url, zip_path, fields)

    def _init_upload(self, url: str, token: str, mod_name: str) -> str:
        body = parse.urlencode({"mod": mod_name}).encode("utf-8")
        req = request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": USER_AGENT,
            },
        )
        payload = self._open_json(req)
        if not isinstance(payload, dict):
            raise PortalApiError("Init upload response is not a JSON object", payload=payload)

        upload_url = payload.get("upload_url")
        if not isinstance(upload_url, str) or not upload_url:
            raise PortalApiError("Init upload response did not include upload_url", payload=payload)
        return upload_url

    def _finish_upload(
        self,
        upload_url: str,
        zip_path: Path,
        fields: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        content_type, body = build_multipart_form(
            fields or {},
            [("file", zip_path.name, "application/zip", zip_path.read_bytes())],
        )
        req = request.Request(
            upload_url,
            data=body,
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": content_type,
                "User-Agent": USER_AGENT,
            },
        )
        payload = self._open_json(req)
        if not isinstance(payload, dict):
            raise PortalApiError("Finish upload response is not a JSON object", payload=payload)
        return payload

    def _open_json(self, req: request.Request) -> object:
        try:
            with request.urlopen(req, timeout=self.timeout) as response:  # noqa: S310
                return _decode_json_response(response.read())
        except error.HTTPError as exc:
            body = exc.read()
            payload = _decode_json_response(body, allow_plain_text=True)
            message = _payload_message(payload) or exc.reason or "HTTP request failed"
            raise PortalApiError(f"HTTP {exc.code}: {message}", status=exc.code, payload=payload) from exc
        except error.URLError as exc:
            raise PortalApiError(f"Network error: {exc.reason}") from exc


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        token = resolve_publish_token(args.config_file)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.execute and not token:
        print(
            f"ERROR: --execute requires {TOKEN_ENV} or a non-empty "
            f"'{CONFIG_TOKEN_KEY}' in {args.config_file}.",
            file=sys.stderr,
        )
        return 1

    dirty_paths = get_git_dirty_paths(args.root)
    if args.require_clean and dirty_paths:
        print("ERROR: --require-clean was set, but the Git worktree is dirty.", file=sys.stderr)
        for path in dirty_paths[:10]:
            print(f"  {path}", file=sys.stderr)
        return 1
    if args.execute and dirty_paths:
        print("WARNING: Git worktree is dirty. Continuing because --require-clean was not set.")
        for path in dirty_paths[:10]:
            print(f"  {path}")

    try:
        projects = select_projects(pack_mods.discover_projects(args.root), args.mod)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if not projects:
        print("ERROR: No mod projects found.", file=sys.stderr)
        return 1

    client = ModPortalClient(args.mod_portal_url, timeout=args.timeout)
    decisions = collect_publish_decisions(projects, client, args.output_dir, args.create)
    print_decisions(decisions, execute=args.execute)

    errors = [decision for decision in decisions if decision.action == ACTION_ERROR]
    if errors:
        print(f"ERROR: {len(errors)} mod(s) are not ready for publishing.", file=sys.stderr)
        return 1

    publishable = [decision for decision in decisions if decision.action in PUBLISH_ACTIONS]
    if not publishable:
        print("No releases need publishing.")
        return 0

    if not args.execute:
        print("Dry run only. Re-run with --execute to upload the listed release(s).")
        return 0

    create_fields = build_create_fields(args)
    assert token is not None
    return execute_publish_actions(publishable, client, token, create_fields)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare local Factorio mod versions with Mod Portal releases and publish newer zips.",
    )
    parser.add_argument("--mod", action="append", default=[], help="Publish only this mod name or mod directory. Can be repeated.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", dest="execute", action="store_false", help="Compare versions without uploading. This is the default.")
    mode.add_argument("--execute", dest="execute", action="store_true", help="Upload releases to the Factorio Mod Portal.")
    parser.set_defaults(execute=False)
    parser.add_argument("--create", action="store_true", help="Allow first-time publishing when a mod does not exist on the portal.")
    parser.add_argument("--require-clean", action="store_true", help="Fail if the Git worktree has uncommitted changes.")
    parser.add_argument("--create-description-file", type=Path, help="Markdown description sent only with --create uploads.")
    parser.add_argument("--create-category", help="Mod Portal category sent only with --create uploads.")
    parser.add_argument("--create-license", help="Mod Portal license id sent only with --create uploads.")
    parser.add_argument("--create-source-url", help="Source URL sent only with --create uploads.")
    parser.add_argument(
        "--config-file",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Local JSON config containing '{CONFIG_TOKEN_KEY}'. {TOKEN_ENV} takes precedence.",
    )
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR, help=argparse.SUPPRESS)
    parser.add_argument("--mod-portal-url", default=MOD_PORTAL_URL, help=argparse.SUPPRESS)
    parser.add_argument("--timeout", type=float, default=60.0, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def resolve_publish_token(config_path: Path) -> str | None:
    env_token = os.environ.get(TOKEN_ENV)
    if env_token and env_token.strip():
        return env_token.strip()

    if not config_path.is_file():
        return None

    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{config_path}: invalid JSON at line {exc.lineno}, column {exc.colno}"
        ) from exc

    if not isinstance(payload, Mapping):
        raise ValueError(f"{config_path}: config root must be a JSON object")

    token = payload.get(CONFIG_TOKEN_KEY)
    if token is None:
        return None
    if not isinstance(token, str):
        raise ValueError(f"{config_path}: '{CONFIG_TOKEN_KEY}' must be a string")

    token = token.strip()
    return token or None


def select_projects(
    projects: list[pack_mods.ModProject],
    selected_mods: list[str],
) -> list[pack_mods.ModProject]:
    if not selected_mods:
        return projects

    selected = set(selected_mods)
    matched: set[str] = set()
    result: list[pack_mods.ModProject] = []
    for project in projects:
        names = {project.name, project.root_dir.name}
        if names & selected:
            result.append(project)
            matched.update(names & selected)

    missing = sorted(selected - matched)
    if missing:
        raise ValueError(f"Unknown mod selection: {', '.join(missing)}")
    return result


def collect_publish_decisions(
    projects: list[pack_mods.ModProject],
    client: ModPortalClient,
    output_dir: Path,
    allow_create: bool,
) -> list[PublishDecision]:
    decisions: list[PublishDecision] = []
    for project in projects:
        try:
            remote_details = client.get_mod_details(project.name)
            decision = build_publish_decision(project, remote_details, output_dir, allow_create)
            if decision.action in PUBLISH_ACTIONS and decision.zip_path is not None:
                verify_zip_package(project, decision.zip_path)
            decisions.append(decision)
        except Exception as exc:  # noqa: BLE001
            decisions.append(
                PublishDecision(
                    project=project,
                    action=ACTION_ERROR,
                    local_version=project.version,
                    remote_version=None,
                    reason=str(exc),
                )
            )
    return decisions


def build_publish_decision(
    project: pack_mods.ModProject,
    remote_details: Mapping[str, Any] | None,
    output_dir: Path,
    allow_create: bool,
) -> PublishDecision:
    zip_path = output_dir / f"{project.package_name}.zip"
    if remote_details is None:
        if allow_create:
            return PublishDecision(
                project=project,
                action=ACTION_CREATE,
                local_version=project.version,
                remote_version=None,
                reason="mod is not on the portal; --create allows first publish",
                zip_path=zip_path,
            )
        return PublishDecision(
            project=project,
            action=ACTION_ERROR,
            local_version=project.version,
            remote_version=None,
            reason="mod is not on the portal; pass --create for first publish",
        )

    remote_version = latest_release_version(remote_details)
    if remote_version is None:
        return PublishDecision(
            project=project,
            action=ACTION_UPLOAD,
            local_version=project.version,
            remote_version=None,
            reason="portal mod exists but has no releases",
            zip_path=zip_path,
        )

    comparison = compare_versions(project.version, remote_version)
    if comparison > 0:
        return PublishDecision(
            project=project,
            action=ACTION_UPLOAD,
            local_version=project.version,
            remote_version=remote_version,
            reason="local version is newer than portal version",
            zip_path=zip_path,
        )
    if comparison == 0:
        return PublishDecision(
            project=project,
            action=ACTION_SKIP,
            local_version=project.version,
            remote_version=remote_version,
            reason="local version already exists on portal",
        )
    return PublishDecision(
        project=project,
        action=ACTION_SKIP,
        local_version=project.version,
        remote_version=remote_version,
        reason="portal version is newer than local version",
    )


def latest_release_version(remote_details: Mapping[str, Any]) -> str | None:
    releases = remote_details.get("releases")
    if not isinstance(releases, list) or not releases:
        return None

    versions: list[str] = []
    for release in releases:
        if not isinstance(release, Mapping):
            continue
        version = release.get("version")
        if isinstance(version, str) and version:
            parse_version(version)
            versions.append(version)

    if not versions:
        return None
    return max(versions, key=parse_version)


def verify_zip_package(project: pack_mods.ModProject, zip_path: Path) -> None:
    if not zip_path.is_file():
        raise FileNotFoundError(f"missing package zip: {zip_path}")

    info_member = f"{project.package_name}/info.json"
    with zipfile.ZipFile(zip_path, "r") as archive:
        names = set(archive.namelist())
        if info_member not in names:
            raise ValueError(f"{zip_path.name}: expected archive member {info_member}")
        info = json.loads(archive.read(info_member).decode("utf-8"))

    if not isinstance(info, dict):
        raise ValueError(f"{zip_path.name}: packaged info.json must be an object")
    if info.get("name") != project.name or info.get("version") != project.version:
        raise ValueError(
            f"{zip_path.name}: packaged info.json is {info.get('name')}_{info.get('version')}, "
            f"expected {project.package_name}"
        )


def execute_publish_actions(
    decisions: list[PublishDecision],
    client: ModPortalClient,
    token: str,
    create_fields: Mapping[str, str],
) -> int:
    failures = 0
    for decision in decisions:
        assert decision.zip_path is not None
        try:
            if decision.action == ACTION_CREATE:
                payload = client.publish_new_mod(token, decision.project, decision.zip_path, create_fields)
            else:
                payload = client.upload_existing_release(token, decision.project, decision.zip_path)

            if payload.get("success") is not True:
                raise PortalApiError("Upload response did not report success", payload=payload)
            print(f"OK: published {decision.project.package_name} ({summarize_payload(payload)})")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAILED: {decision.project.package_name}: {exc}", file=sys.stderr)

    return 0 if failures == 0 else 1


def print_decisions(decisions: list[PublishDecision], execute: bool) -> None:
    mode = "execute" if execute else "dry-run"
    print(f"Mode: {mode}")
    for index, decision in enumerate(decisions, start=1):
        label = action_label(decision.action, execute)
        remote = decision.remote_version if decision.remote_version is not None else "-"
        print(
            f"[{index}/{len(decisions)}] {label} {decision.project.name}: "
            f"local={decision.local_version} remote={remote} - {decision.reason}"
        )
        if decision.zip_path is not None and decision.action in PUBLISH_ACTIONS:
            print(f"    zip: {decision.zip_path}")


def action_label(action: str, execute: bool) -> str:
    if action == ACTION_UPLOAD:
        return "UPLOAD" if execute else "WOULD_UPLOAD"
    if action == ACTION_CREATE:
        return "CREATE" if execute else "WOULD_CREATE"
    if action == ACTION_SKIP:
        return "SKIP"
    return "ERROR"


def build_create_fields(args: argparse.Namespace) -> dict[str, str]:
    fields: dict[str, str] = {}
    if args.create_description_file:
        fields["description"] = args.create_description_file.read_text(encoding="utf-8")
    if args.create_category:
        fields["category"] = args.create_category
    if args.create_license:
        fields["license"] = args.create_license
    if args.create_source_url:
        fields["source_url"] = args.create_source_url
    return fields


def get_git_dirty_paths(root: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return []

    if result.returncode != 0:
        return []
    return [line[3:] if len(line) > 3 else line for line in result.stdout.splitlines() if line.strip()]


def compare_versions(left: str, right: str) -> int:
    left_version = parse_version(left)
    right_version = parse_version(right)
    if left_version > right_version:
        return 1
    if left_version < right_version:
        return -1
    return 0


def parse_version(value: str) -> tuple[int, int, int]:
    parts = value.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise ValueError(f"version must use major.minor.patch format: {value}")
    return int(parts[0]), int(parts[1]), int(parts[2])


def build_multipart_form(
    fields: Mapping[str, str],
    files: list[tuple[str, str, str, bytes]],
) -> tuple[str, bytes]:
    boundary = f"----mlj-factorio-{uuid.uuid4().hex}"
    chunks: list[bytes] = []

    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode("ascii"))
        chunks.append(
            f'Content-Disposition: form-data; name="{escape_multipart_header(name)}"\r\n\r\n'.encode("ascii")
        )
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")

    for field_name, filename, content_type, content in files:
        chunks.append(f"--{boundary}\r\n".encode("ascii"))
        disposition = (
            f'Content-Disposition: form-data; name="{escape_multipart_header(field_name)}"; '
            f'filename="{escape_multipart_header(filename)}"\r\n'
        )
        chunks.append(disposition.encode("ascii"))
        chunks.append(f"Content-Type: {content_type}\r\n\r\n".encode("ascii"))
        chunks.append(content)
        chunks.append(b"\r\n")

    chunks.append(f"--{boundary}--\r\n".encode("ascii"))
    return f"multipart/form-data; boundary={boundary}", b"".join(chunks)


def escape_multipart_header(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def summarize_payload(payload: Mapping[str, Any]) -> str:
    if isinstance(payload.get("url"), str):
        return f"url={payload['url']}"
    return "success=true"


def _decode_json_response(body: bytes, allow_plain_text: bool = False) -> object:
    text = body.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        if allow_plain_text:
            return text
        raise PortalApiError(f"Response was not valid JSON: {text[:200]}")


def _payload_error(payload: object | None) -> str | None:
    if isinstance(payload, Mapping) and isinstance(payload.get("error"), str):
        return payload["error"]
    return None


def _payload_message(payload: object | None) -> str | None:
    if isinstance(payload, Mapping):
        if isinstance(payload.get("message"), str):
            return payload["message"]
        if isinstance(payload.get("error"), str):
            return payload["error"]
    if isinstance(payload, str):
        return payload[:200]
    return None


if __name__ == "__main__":
    raise SystemExit(main())
