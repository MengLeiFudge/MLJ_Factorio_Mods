#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import gzip
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
from urllib import error, parse, request


MOD_PORTAL_URL = "https://mods.factorio.com"
USER_AGENT = "MLJ-Factorio-Mod-Updater/1.0 (unofficial community tool)"
DEFAULT_WORKERS = 8
MAX_WORKERS = 32
MAX_CATALOG_BYTES = 64 * 1024 * 1024
MAX_INFO_JSON_BYTES = 1024 * 1024
CACHE_DIR_NAME = ".factorio-mod-updater-cache"
RETRYABLE_HTTP_STATUS = {408, 425, 429, 500, 502, 503, 504}
CORE_MODS = {"base", "elevated-rails", "quality", "recycler", "space-age"}
VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){2}$")
ARCHIVE_NAME_RE = re.compile(r"^(?P<name>.+)_(?P<version>[0-9]+(?:\.[0-9]+){2})\.zip$")
SHA1_RE = re.compile(r"^[0-9a-fA-F]{40}$")
FACTORIO_LOG_VERSION_RE = re.compile(r"\bFactorio ([0-9]+(?:\.[0-9]+){2})\b")
FACTORIO_EXE_RE = re.compile(r'^\s*\S*Program arguments:\s+"([A-Za-z]:\\[^\"]*Factorio\.exe)"', re.MULTILINE)


class UpdaterError(RuntimeError):
    pass


@dataclass(frozen=True)
class ArchiveInfo:
    name: str
    version: str
    factorio_version: str
    dependencies: tuple[str, ...]
    root: str


@dataclass(frozen=True)
class LocalArchive:
    path: Path
    info: ArchiveInfo


@dataclass(frozen=True)
class Release:
    name: str
    version: str
    factorio_version: str
    file_name: str
    download_url: str
    sha1: str


@dataclass
class Inventory:
    archives: dict[str, list[LocalArchive]] = field(default_factory=dict)
    unsafe_names: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)
    snapshot: dict[str, tuple[int, int, int]] = field(default_factory=dict)


@dataclass
class UpdatePlan:
    downloads: dict[str, Release] = field(default_factory=dict)
    cleanup_targets: dict[str, Path] = field(default_factory=dict)
    current: list[str] = field(default_factory=list)
    no_candidate: list[str] = field(default_factory=list)
    local_newer: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    unsafe: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DownloadResult:
    release: Release
    staged_path: Path | None = None
    info: ArchiveInfo | None = None
    error: str | None = None
    cached: bool = False


@dataclass(frozen=True)
class Dependency:
    name: str
    operator: str | None
    version: str | None
    required: bool


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return run(args)
    except KeyboardInterrupt:
        print("\n已取消；尚未完成的暂存下载会被清理。", file=sys.stderr)
        return 130
    except UpdaterError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="无需启动 Factorio，并行更新本机已安装模组并清理同名旧版 zip。",
    )
    parser.add_argument("--dry-run", action="store_true", help="只显示计划，不下载、安装或删除文件。")
    parser.add_argument(
        "--workers",
        type=worker_count,
        default=DEFAULT_WORKERS,
        help=f"并行下载数，默认 {DEFAULT_WORKERS}，最大 {MAX_WORKERS}。",
    )
    parser.add_argument("--user-data-dir", type=Path, help="Factorio 用户数据目录；默认自动发现。")
    parser.add_argument("--factorio-version", help="目标 Factorio 版本，例如 2.1.14；默认自动检测。")
    parser.add_argument("--timeout", type=positive_float, default=60.0, help="单次网络请求超时秒数，默认 60。")
    parser.add_argument("--portal-url", default=MOD_PORTAL_URL, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def worker_count(value: str) -> int:
    try:
        result = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("并行下载数必须是整数") from exc
    if not 1 <= result <= MAX_WORKERS:
        raise argparse.ArgumentTypeError(f"并行下载数必须在 1 到 {MAX_WORKERS} 之间")
    return result


def positive_float(value: str) -> float:
    try:
        result = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("超时必须是数字") from exc
    if result <= 0:
        raise argparse.ArgumentTypeError("超时必须大于 0")
    return result


def run(args: argparse.Namespace) -> int:
    user_data_dir = discover_user_data_dir(args.user_data_dir)
    mods_dir = user_data_dir / "mods"
    if not mods_dir.is_dir():
        raise UpdaterError(f"模组目录不存在：{mods_dir}")

    ensure_factorio_stopped()
    game_version = normalize_full_version(args.factorio_version) if args.factorio_version else detect_factorio_version(user_data_dir)
    game_series = factorio_series(game_version)
    inventory = scan_inventory(mods_dir)
    if not inventory.archives:
        raise UpdaterError(f"没有在 {mods_dir} 找到可管理的模组 zip")

    installed_names = set(inventory.archives)
    print(f"Factorio 版本：{game_version}")
    print(f"模组目录：{mods_dir}")
    print(f"本地模组：{len(installed_names)} 个，标准 zip：{sum(len(items) for items in inventory.archives.values())} 个")
    for warning in inventory.warnings:
        print(f"警告：{warning}")

    releases, catalog_warnings = fetch_compatible_releases(
        installed_names,
        game_version,
        args.portal_url,
        args.timeout,
    )
    for warning in catalog_warnings:
        print(f"警告：{warning}")

    plan = build_update_plan(inventory, releases, game_series)
    print_plan(plan, inventory)
    if args.dry_run:
        print("Dry run 完成；未下载、安装或删除任何文件。")
        return 0

    if not plan.downloads and not cleanup_paths(plan, inventory):
        print("无需修改本地文件。")
        return 1 if plan.conflicts or inventory.warnings or catalog_warnings else 0

    username = token = None
    if plan.downloads:
        username, token = read_download_credentials(user_data_dir)

    download_failures: dict[str, str] = {}
    blocked: dict[str, str] = {}
    installed: list[str] = []
    deleted: list[Path] = []
    delete_errors: list[str] = []

    ready: dict[str, DownloadResult] = {}
    staging_dir: Path | None = None
    if plan.downloads:
        assert username is not None and token is not None
        staging_dir = prepare_staging_dir(user_data_dir)
        results = download_releases(
            plan.downloads,
            staging_dir,
            username,
            token,
            args.portal_url,
            args.timeout,
            args.workers,
            game_series,
        )
        download_failures = {
            name: result.error or "未知下载错误"
            for name, result in results.items()
            if result.error is not None
        }
        ready = {
            name: result
            for name, result in results.items()
            if result.error is None and result.staged_path is not None and result.info is not None
        }
        blocked = validate_dependencies(
            ready,
            inventory,
            game_version,
            game_series,
            load_mod_list_names(mods_dir / "mod-list.json"),
        )

    for name, reason in sorted(blocked.items()):
        print(f"跳过安装：{name}：{reason}")

    ready = {name: result for name, result in ready.items() if name not in blocked}
    targets = dict(plan.cleanup_targets)
    if ready or cleanup_paths(plan, inventory):
        ensure_factorio_stopped()
        assert_snapshot_unchanged(mods_dir, inventory.snapshot)
        installed, new_targets = install_downloads(ready, mods_dir)
        targets.update(new_targets)
        deleted, delete_errors = remove_old_archives(targets, inventory)

    if staging_dir is not None and not download_failures and not blocked and not delete_errors:
        try:
            shutil.rmtree(staging_dir)
        except OSError as exc:
            delete_errors.append(f"暂存缓存 {staging_dir}：{exc}")

    print_result(
        installed,
        deleted,
        download_failures,
        blocked,
        delete_errors,
        plan,
    )
    has_failures = bool(download_failures or blocked or delete_errors or plan.conflicts or inventory.warnings or catalog_warnings)
    return 1 if has_failures else 0


def discover_user_data_dir(explicit: Path | None) -> Path:
    if explicit is not None:
        path = explicit.expanduser().resolve()
        if not path.is_dir():
            raise UpdaterError(f"指定的用户数据目录不存在：{path}")
        return path

    candidates: list[Path] = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "Factorio")
    candidates.extend(
        [
            Path.home() / ".factorio",
            Path.home() / "Library" / "Application Support" / "factorio",
        ]
    )
    wsl_users = Path("/mnt/c/Users")
    if wsl_users.is_dir():
        candidates.extend(sorted(wsl_users.glob("*/AppData/Roaming/Factorio")))

    found: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if (resolved / "mods").is_dir() and (resolved / "player-data.json").is_file():
            found.append(resolved)

    if not found:
        raise UpdaterError("无法自动发现 Factorio 用户数据目录；请使用 --user-data-dir 指定")
    if len(found) > 1:
        paths = "、".join(str(path) for path in found)
        raise UpdaterError(f"发现多个 Factorio 用户数据目录（{paths}）；请使用 --user-data-dir 指定")
    return found[0]


def ensure_factorio_stopped() -> None:
    state = factorio_running()
    if state is True:
        raise UpdaterError("Factorio.exe 正在运行；请关闭游戏后重试")
    if state is None:
        print("警告：无法确认 Factorio 进程状态；继续前请确保游戏已关闭。")


def factorio_running() -> bool | None:
    if os.name == "nt" or os.environ.get("WSL_DISTRO_NAME") or Path("/proc/sys/fs/binfmt_misc/WSLInterop").exists():
        executable = "tasklist" if os.name == "nt" else "tasklist.exe"
        command = [executable, "/FI", "IMAGENAME eq Factorio.exe", "/FO", "CSV", "/NH"]
        try:
            result = subprocess.run(command, check=False, capture_output=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return None
        output = result.stdout.decode(errors="replace").lower()
        return '"factorio.exe"' in output or output.lstrip().startswith("factorio.exe")

    try:
        result = subprocess.run(["pgrep", "-x", "factorio"], check=False, capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.returncode == 0


def detect_factorio_version(user_data_dir: Path) -> str:
    log_path = user_data_dir / "factorio-current.log"
    log_text = ""
    try:
        log_text = log_path.read_text(encoding="utf-8", errors="replace")[:65536]
    except OSError:
        pass

    executable = factorio_executable_from_log(log_text)
    if executable is not None and executable.is_file():
        try:
            result = subprocess.run(
                [str(executable), "--version"],
                check=False,
                capture_output=True,
                timeout=15,
            )
            output = (result.stdout + result.stderr).decode(errors="replace")
            match = re.search(r"^Version:\s*([0-9]+(?:\.[0-9]+){2})\b", output, re.MULTILINE)
            if result.returncode == 0 and match:
                return normalize_full_version(match.group(1))
        except (OSError, subprocess.SubprocessError):
            pass

    match = FACTORIO_LOG_VERSION_RE.search(log_text)
    if match:
        return normalize_full_version(match.group(1))

    player_data = read_json_file(user_data_dir / "player-data.json", "player-data.json")
    last_played = player_data.get("last-played-version")
    if isinstance(last_played, Mapping):
        game_version = last_played.get("game_version")
        if isinstance(game_version, str):
            return normalize_full_version(game_version)

    raise UpdaterError("无法自动检测 Factorio 版本；请使用 --factorio-version 指定")


def factorio_executable_from_log(log_text: str) -> Path | None:
    match = FACTORIO_EXE_RE.search(log_text)
    if not match:
        return None
    raw_path = match.group(1)
    if os.name == "nt":
        return Path(raw_path)
    drive_match = re.match(r"^([A-Za-z]):\\(.*)$", raw_path)
    if not drive_match:
        return None
    drive = drive_match.group(1).lower()
    remainder = drive_match.group(2).replace("\\", "/")
    return Path(f"/mnt/{drive}/{remainder}")


def normalize_full_version(value: str | None) -> str:
    if value is None or not VERSION_RE.fullmatch(value.strip()):
        raise UpdaterError(f"Factorio 版本必须使用 major.minor.patch 格式：{value!r}")
    return value.strip()


def factorio_series(version: str) -> str:
    major, minor, _patch = parse_version(version)
    return f"{major}.{minor}"


def scan_inventory(mods_dir: Path) -> Inventory:
    before = snapshot_zip_files(mods_dir)
    inventory = Inventory(snapshot=before)
    for file_name in sorted(before, key=str.casefold):
        path = mods_dir / file_name
        mode = before[file_name][2]
        guessed = ARCHIVE_NAME_RE.fullmatch(file_name)
        guessed_name = guessed.group("name") if guessed else None
        if not stat.S_ISREG(mode):
            inventory.warnings.append(f"保留非普通文件：{file_name}")
            if guessed_name:
                inventory.unsafe_names.add(guessed_name)
            continue
        try:
            info = read_archive_info(path)
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            inventory.warnings.append(f"无法验证并保留 {file_name}：{exc}")
            if guessed_name:
                inventory.unsafe_names.add(guessed_name)
            continue

        expected_name = f"{info.name}_{info.version}.zip"
        if file_name != expected_name:
            inventory.warnings.append(
                f"保留非标准包 {file_name}（info.json 为 {info.name}_{info.version}）"
            )
            inventory.unsafe_names.add(info.name)
            continue
        inventory.archives.setdefault(info.name, []).append(LocalArchive(path, info))

    after = snapshot_zip_files(mods_dir)
    if after != before:
        raise UpdaterError("扫描期间 mods 目录发生变化；请确认游戏及其他更新程序已关闭后重试")
    return inventory


def snapshot_zip_files(mods_dir: Path) -> dict[str, tuple[int, int, int]]:
    result: dict[str, tuple[int, int, int]] = {}
    try:
        paths = list(mods_dir.glob("*.zip"))
    except OSError as exc:
        raise UpdaterError(f"无法读取模组目录：{exc}") from exc
    for path in paths:
        try:
            file_stat = path.lstat()
        except OSError as exc:
            raise UpdaterError(f"无法读取 {path.name} 的状态：{exc}") from exc
        result[path.name] = (file_stat.st_size, file_stat.st_mtime_ns, file_stat.st_mode)
    return result


def assert_snapshot_unchanged(mods_dir: Path, expected: dict[str, tuple[int, int, int]]) -> None:
    actual = snapshot_zip_files(mods_dir)
    if actual == expected:
        return
    added = sorted(set(actual) - set(expected))
    removed = sorted(set(expected) - set(actual))
    changed = sorted(name for name in set(actual) & set(expected) if actual[name] != expected[name])
    details: list[str] = []
    if added:
        details.append(f"新增 {', '.join(added[:5])}")
    if removed:
        details.append(f"移除 {', '.join(removed[:5])}")
    if changed:
        details.append(f"变化 {', '.join(changed[:5])}")
    raise UpdaterError(f"下载期间 mods 目录发生变化（{'; '.join(details)}）；未安装或删除文件")


def read_archive_info(path: Path) -> ArchiveInfo:
    with zipfile.ZipFile(path, "r") as archive:
        candidates = []
        for member in archive.infolist():
            parts = PurePosixPath(member.filename).parts
            if len(parts) == 2 and parts[1] == "info.json" and not member.is_dir():
                candidates.append(member)
        if len(candidates) != 1:
            raise ValueError(f"包内应有且仅有一个 <name_version>/info.json，实际为 {len(candidates)} 个")
        member = candidates[0]
        if member.file_size > MAX_INFO_JSON_BYTES:
            raise ValueError("info.json 过大")
        try:
            payload = json.loads(archive.read(member).decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"info.json 无效：{exc}") from exc

    if not isinstance(payload, Mapping):
        raise ValueError("info.json 必须是 JSON 对象")
    name = payload.get("name")
    version = payload.get("version")
    factorio_version = payload.get("factorio_version")
    if not isinstance(name, str) or not name or "/" in name or "\\" in name:
        raise ValueError("info.json.name 无效")
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        raise ValueError("info.json.version 必须使用 major.minor.patch 格式")
    if not isinstance(factorio_version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+", factorio_version):
        raise ValueError("info.json.factorio_version 无效")

    raw_dependencies = payload.get("dependencies", ["base"])
    if not isinstance(raw_dependencies, list) or not all(isinstance(item, str) for item in raw_dependencies):
        raise ValueError("info.json.dependencies 必须是字符串数组")
    root = PurePosixPath(member.filename).parts[0]
    return ArchiveInfo(name, version, factorio_version, tuple(raw_dependencies), root)


def fetch_compatible_releases(
    installed_names: set[str],
    game_version: str,
    portal_url: str,
    timeout: float,
) -> tuple[dict[str, Release], list[str]]:
    query = parse.urlencode({"version": game_version, "page_size": "max"})
    url = f"{portal_url.rstrip('/')}/api/mods?{query}"
    payload = fetch_json(url, timeout)
    if not isinstance(payload, Mapping) or not isinstance(payload.get("results"), list):
        raise UpdaterError("Mod Portal 返回了无效的模组目录")

    releases: dict[str, Release] = {}
    warnings: list[str] = []
    for raw_mod in payload["results"]:
        if not isinstance(raw_mod, Mapping):
            continue
        name = raw_mod.get("name")
        if not isinstance(name, str) or name not in installed_names:
            continue
        raw_release = raw_mod.get("latest_release")
        if not isinstance(raw_release, Mapping):
            continue
        try:
            releases[name] = parse_release(name, raw_release)
        except ValueError as exc:
            warnings.append(f"忽略官网 {name} 的无效 release：{exc}")
    return releases, warnings


def parse_release(name: str, payload: Mapping[str, Any]) -> Release:
    version = payload.get("version")
    file_name = payload.get("file_name")
    download_url = payload.get("download_url")
    sha1 = payload.get("sha1")
    info_json = payload.get("info_json")
    factorio_version = info_json.get("factorio_version") if isinstance(info_json, Mapping) else None

    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        raise ValueError("version 无效")
    expected_file_name = f"{name}_{version}.zip"
    if file_name != expected_file_name:
        raise ValueError(f"file_name 应为 {expected_file_name}")
    if not isinstance(download_url, str) or not download_url.startswith("/download/"):
        raise ValueError("download_url 无效")
    split_download = parse.urlsplit(download_url)
    if split_download.scheme or split_download.netloc:
        raise ValueError("download_url 必须是 Mod Portal 相对路径")
    if not isinstance(sha1, str) or not SHA1_RE.fullmatch(sha1):
        raise ValueError("sha1 无效")
    if not isinstance(factorio_version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+", factorio_version):
        raise ValueError("factorio_version 无效")
    return Release(name, version, factorio_version, file_name, download_url, sha1.lower())


def fetch_json(url: str, timeout: float) -> object:
    req = request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "User-Agent": USER_AGENT,
        },
    )
    body, content_encoding = fetch_bytes(req, timeout, max_bytes=MAX_CATALOG_BYTES)
    if content_encoding.lower() == "gzip":
        try:
            body = gzip.decompress(body)
        except OSError as exc:
            raise UpdaterError(f"Mod Portal gzip 响应无效：{exc}") from exc
    if len(body) > MAX_CATALOG_BYTES:
        raise UpdaterError("Mod Portal 模组目录响应过大")
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdaterError(f"Mod Portal 返回了无效 JSON：{exc}") from exc


def fetch_bytes(req: request.Request, timeout: float, max_bytes: int) -> tuple[bytes, str]:
    for attempt in range(4):
        try:
            with request.urlopen(req, timeout=timeout) as response:  # noqa: S310
                body = response.read(max_bytes + 1)
                if len(body) > max_bytes:
                    raise UpdaterError("Mod Portal 响应超过安全大小限制")
                return body, response.headers.get("Content-Encoding", "")
        except error.HTTPError as exc:
            status = exc.code
            retry_after = retry_after_seconds(exc.headers.get("Retry-After"))
            exc.close()
            if status not in RETRYABLE_HTTP_STATUS or attempt == 3:
                raise UpdaterError(f"Mod Portal HTTP {status}：{exc.reason}") from exc
            time.sleep(retry_after if retry_after is not None else 2**attempt)
        except error.URLError as exc:
            if attempt == 3:
                raise UpdaterError(f"无法连接 Mod Portal：{exc.reason}") from exc
            time.sleep(2**attempt)
        except OSError as exc:
            if attempt == 3:
                raise UpdaterError(f"连接 Mod Portal 时网络中断：{exc}") from exc
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


def build_update_plan(inventory: Inventory, releases: Mapping[str, Release], game_series: str) -> UpdatePlan:
    plan = UpdatePlan()
    for name in sorted(inventory.archives, key=str.casefold):
        if name in inventory.unsafe_names:
            plan.unsafe.append(name)
            continue
        release = releases.get(name)
        if release is None or release.factorio_version != game_series:
            plan.no_candidate.append(name)
            compatible = [item for item in inventory.archives[name] if item.info.factorio_version == game_series]
            if compatible:
                newest = max(compatible, key=lambda item: parse_version(item.info.version))
                plan.cleanup_targets[name] = newest.path
            continue

        archives = inventory.archives[name]
        target_path = archives[0].path.parent / release.file_name
        target_archive = next((item for item in archives if item.path == target_path), None)
        if target_archive is not None:
            plan.cleanup_targets[name] = target_path
            if sha1_file(target_path) == release.sha1:
                plan.current.append(name)
            else:
                plan.conflicts.append(name)
            continue

        compatible = [item for item in archives if item.info.factorio_version == game_series]
        if compatible:
            newest = max(compatible, key=lambda item: parse_version(item.info.version))
            if parse_version(newest.info.version) > parse_version(release.version):
                plan.local_newer.append(name)
                plan.cleanup_targets[name] = newest.path
                continue
        plan.downloads[name] = release
    return plan


def print_plan(plan: UpdatePlan, inventory: Inventory) -> None:
    old_count = len(cleanup_paths(plan, inventory))
    print(
        "计划："
        f"下载 {len(plan.downloads)} 个，已是最新版 {len(plan.current)} 个，"
        f"待清理旧版 zip {old_count} 个，官网无兼容版本 {len(plan.no_candidate)} 个。"
    )
    if plan.downloads:
        print("待下载：" + "、".join(f"{name} {release.version}" for name, release in plan.downloads.items()))
    if plan.local_newer:
        print("保留本地较新版本：" + "、".join(plan.local_newer))
    if plan.conflicts:
        print("保留同版本本地改包：" + "、".join(plan.conflicts))
    if plan.unsafe:
        print("保留含非标准/无法验证包的模组：" + "、".join(plan.unsafe))


def cleanup_paths(plan: UpdatePlan, inventory: Inventory) -> list[Path]:
    paths: list[Path] = []
    for name, target in plan.cleanup_targets.items():
        paths.extend(item.path for item in inventory.archives[name] if item.path != target)
    return paths


def prepare_staging_dir(user_data_dir: Path) -> Path:
    path = user_data_dir / CACHE_DIR_NAME
    if path.is_symlink():
        raise UpdaterError(f"暂存缓存不能是符号链接：{path}")
    if path.exists() and not path.is_dir():
        raise UpdaterError(f"暂存缓存路径不是目录：{path}")
    try:
        path.mkdir(mode=0o700, exist_ok=True)
    except OSError as exc:
        raise UpdaterError(f"无法创建暂存缓存 {path}：{exc}") from exc
    return path


def read_download_credentials(user_data_dir: Path) -> tuple[str, str]:
    payload = read_json_file(user_data_dir / "player-data.json", "player-data.json")
    username = os.environ.get("FACTORIO_USERNAME") or payload.get("service-username")
    token = os.environ.get("FACTORIO_TOKEN") or payload.get("service-token")
    if not isinstance(username, str) or not username or not isinstance(token, str) or not token:
        raise UpdaterError(
            "没有找到 Mod Portal 下载凭据；请先在 Factorio 登录，或设置 FACTORIO_USERNAME 与 FACTORIO_TOKEN"
        )
    return username, token


def read_json_file(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise UpdaterError(f"无法读取 {label}：{exc}") from exc
    except json.JSONDecodeError as exc:
        raise UpdaterError(f"{label} 不是有效 JSON：{exc}") from exc
    if not isinstance(payload, dict):
        raise UpdaterError(f"{label} 必须是 JSON 对象")
    return payload


def download_releases(
    releases: Mapping[str, Release],
    staging_dir: Path,
    username: str,
    token: str,
    portal_url: str,
    timeout: float,
    workers: int,
    game_series: str,
) -> dict[str, DownloadResult]:
    results: dict[str, DownloadResult] = {}
    total = len(releases)
    print(f"开始并行下载：{total} 个模组，{workers} 路并发。")
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(workers, total), thread_name_prefix="mod-download") as executor:
        future_to_name = {
            executor.submit(
                download_release,
                release,
                staging_dir,
                username,
                token,
                portal_url,
                timeout,
                game_series,
            ): name
            for name, release in releases.items()
        }
        completed = 0
        for future in concurrent.futures.as_completed(future_to_name):
            name = future_to_name[future]
            completed += 1
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001
                result = DownloadResult(releases[name], error=f"内部错误：{exc}")
            results[name] = result
            if result.error:
                print(f"[{completed}/{total}] 失败 {name}：{result.error}")
            elif result.cached:
                print(f"[{completed}/{total}] 复用缓存 {name} {result.release.version}")
            else:
                print(f"[{completed}/{total}] 完成 {name} {result.release.version}")
    return results


def download_release(
    release: Release,
    staging_dir: Path,
    username: str,
    token: str,
    portal_url: str,
    timeout: float,
    game_series: str,
) -> DownloadResult:
    ready_path = staging_dir / release.file_name
    part_path = staging_dir / f"{release.file_name}.part"
    cached = reuse_cached_release(release, ready_path, game_series)
    if cached is not None:
        return cached
    if part_path.is_symlink() or (part_path.exists() and not part_path.is_file()):
        return DownloadResult(release, error=f"暂存分片不是普通文件：{part_path.name}")

    url = authenticated_download_url(portal_url, release.download_url, username, token)
    for attempt in range(4):
        try:
            digest = hashlib.sha1()  # noqa: S324 - the portal publishes SHA-1 for release integrity
            offset = part_path.stat().st_size if part_path.exists() else 0
            if offset:
                with part_path.open("rb") as existing:
                    while True:
                        chunk = existing.read(1024 * 1024)
                        if not chunk:
                            break
                        digest.update(chunk)

            headers = {"Accept": "application/zip", "User-Agent": USER_AGENT}
            if offset:
                headers["Range"] = f"bytes={offset}-"
            req = request.Request(url, headers=headers)
            with request.urlopen(req, timeout=timeout) as response:  # noqa: S310
                status = response.getcode()
                expected_total: int | None = None
                if offset and status == 206:
                    content_range = response.headers.get("Content-Range", "")
                    range_match = re.fullmatch(r"bytes ([0-9]+)-([0-9]+)/([0-9]+|\*)", content_range)
                    if range_match is None or int(range_match.group(1)) != offset:
                        part_path.unlink(missing_ok=True)
                        raise OSError(f"服务器返回了无效续传范围：{content_range!r}")
                    if range_match.group(3) != "*":
                        expected_total = int(range_match.group(3))
                    mode = "ab"
                else:
                    offset = 0
                    digest = hashlib.sha1()  # noqa: S324
                    mode = "wb"

                raw_length = response.headers.get("Content-Length")
                expected_response = int(raw_length) if raw_length and raw_length.isdigit() else None
                received = 0
                with part_path.open(mode) as output:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                        digest.update(chunk)
                        received += len(chunk)

            if expected_response is not None and received != expected_response:
                raise OSError(f"下载长度不符（预期 {expected_response}，实际 {received}）")
            final_size = offset + received
            if expected_total is not None and final_size != expected_total:
                raise OSError(f"续传后长度不符（预期 {expected_total}，实际 {final_size}）")
            actual_sha1 = digest.hexdigest()
            if actual_sha1 != release.sha1:
                raise ValueError(f"SHA-1 校验失败（预期 {release.sha1}，实际 {actual_sha1}）")
            os.replace(part_path, ready_path)
            info = read_archive_info(ready_path)
            validate_downloaded_archive(release, info, game_series)
            return DownloadResult(release, ready_path, info)
        except error.HTTPError as exc:
            status = exc.code
            retry_after = retry_after_seconds(exc.headers.get("Retry-After"))
            reason = str(exc.reason)
            exc.close()
            if status == 416 and part_path.exists() and attempt < 3:
                part_path.unlink(missing_ok=True)
                continue
            if status not in RETRYABLE_HTTP_STATUS or attempt == 3:
                return DownloadResult(release, error=f"HTTP {status}：{reason}")
            time.sleep(retry_after if retry_after is not None else 2**attempt)
        except (ValueError, zipfile.BadZipFile) as exc:
            part_path.unlink(missing_ok=True)
            ready_path.unlink(missing_ok=True)
            return DownloadResult(release, error=str(exc))
        except (error.URLError, OSError) as exc:
            if attempt == 3:
                return DownloadResult(release, error=str(exc))
            time.sleep(2**attempt)
    return DownloadResult(release, error="下载重试耗尽")


def reuse_cached_release(release: Release, ready_path: Path, game_series: str) -> DownloadResult | None:
    if ready_path.is_symlink():
        return DownloadResult(release, error=f"暂存缓存不能是符号链接：{ready_path.name}")
    if not ready_path.exists():
        return None
    if not ready_path.is_file():
        return DownloadResult(release, error=f"暂存缓存不是普通文件：{ready_path.name}")
    try:
        if sha1_file(ready_path) != release.sha1:
            ready_path.unlink()
            return None
        info = read_archive_info(ready_path)
        validate_downloaded_archive(release, info, game_series)
        return DownloadResult(release, ready_path, info, cached=True)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        return DownloadResult(release, error=f"缓存校验失败：{exc}")


def authenticated_download_url(portal_url: str, download_url: str, username: str, token: str) -> str:
    base = f"{portal_url.rstrip('/')}{download_url}"
    split_url = parse.urlsplit(base)
    query = parse.parse_qsl(split_url.query, keep_blank_values=True)
    query.extend([("username", username), ("token", token)])
    return parse.urlunsplit((split_url.scheme, split_url.netloc, split_url.path, parse.urlencode(query), split_url.fragment))


def validate_downloaded_archive(release: Release, info: ArchiveInfo, game_series: str) -> None:
    if info.name != release.name or info.version != release.version:
        raise ValueError(
            f"包内 info.json 为 {info.name}_{info.version}，预期 {release.name}_{release.version}"
        )
    if info.factorio_version != game_series:
        raise ValueError(f"包要求 Factorio {info.factorio_version}，当前系列为 {game_series}")


def retry_after_seconds(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except ValueError:
        return None
    return min(max(result, 0.0), 60.0)


def load_mod_list_names(path: Path) -> set[str]:
    try:
        payload = read_json_file(path, "mod-list.json")
    except UpdaterError:
        return {"base"}
    raw_mods = payload.get("mods")
    if not isinstance(raw_mods, list):
        return {"base"}
    return {
        item["name"]
        for item in raw_mods
        if isinstance(item, Mapping) and isinstance(item.get("name"), str)
    } | {"base"}


def validate_dependencies(
    ready: Mapping[str, DownloadResult],
    inventory: Inventory,
    game_version: str,
    game_series: str,
    mod_list_names: set[str],
) -> dict[str, str]:
    local_versions: dict[str, str] = {}
    for name, archives in inventory.archives.items():
        compatible = [item.info.version for item in archives if item.info.factorio_version == game_series]
        if compatible:
            local_versions[name] = max(compatible, key=parse_version)
    for core_name in CORE_MODS & mod_list_names:
        local_versions[core_name] = game_version

    blocked: dict[str, str] = {}
    while True:
        effective = dict(local_versions)
        for name, result in ready.items():
            if name not in blocked:
                effective[name] = result.release.version

        newly_blocked: dict[str, str] = {}
        for name, result in ready.items():
            if name in blocked:
                continue
            assert result.info is not None
            for raw_dependency in result.info.dependencies:
                try:
                    dependency = parse_dependency(raw_dependency)
                except ValueError as exc:
                    newly_blocked[name] = f"无法解析依赖 {raw_dependency!r}：{exc}"
                    break
                if not dependency.required:
                    continue
                available = effective.get(dependency.name)
                if available is None:
                    newly_blocked[name] = f"缺少必需依赖 {dependency.name}"
                    break
                if not dependency_satisfied(available, dependency.operator, dependency.version):
                    requirement = f" {dependency.operator} {dependency.version}" if dependency.operator else ""
                    newly_blocked[name] = f"依赖 {dependency.name}{requirement}，可用版本为 {available}"
                    break
        if not newly_blocked:
            return blocked
        blocked.update(newly_blocked)


def parse_dependency(value: str) -> Dependency:
    text = value.strip()
    marker = ""
    for candidate in ("(?)", "?", "+", "!", "~"):
        if text.startswith(candidate):
            marker = candidate
            text = text[len(candidate) :].strip()
            break
    match = re.fullmatch(r"(.+?)(?:\s*(>=|<=|=|>|<)\s*([0-9]+(?:\.[0-9]+){1,2}))?", text)
    if not match:
        raise ValueError("格式无效")
    name = match.group(1).strip()
    operator = match.group(2)
    version = match.group(3)
    if not name:
        raise ValueError("缺少模组名")
    if version is not None:
        parse_dependency_version(version)
    return Dependency(name, operator, version, marker in ("", "~"))


def dependency_satisfied(actual: str, operator: str | None, required: str | None) -> bool:
    if operator is None or required is None:
        return True
    left = parse_dependency_version(actual)
    right = parse_dependency_version(required)
    comparisons = {
        "=": left == right,
        ">": left > right,
        ">=": left >= right,
        "<": left < right,
        "<=": left <= right,
    }
    return comparisons[operator]


def parse_dependency_version(value: str) -> tuple[int, int, int]:
    parts = value.split(".")
    if len(parts) not in (2, 3) or not all(part.isdigit() for part in parts):
        raise ValueError(f"版本格式无效：{value}")
    numbers = [int(part) for part in parts]
    while len(numbers) < 3:
        numbers.append(0)
    return numbers[0], numbers[1], numbers[2]


def install_downloads(
    ready: Mapping[str, DownloadResult],
    mods_dir: Path,
) -> tuple[list[str], dict[str, Path]]:
    installed_paths: list[Path] = []
    installed_names: list[str] = []
    targets: dict[str, Path] = {}
    try:
        for name in sorted(ready, key=str.casefold):
            result = ready[name]
            assert result.staged_path is not None
            destination = mods_dir / result.release.file_name
            if destination.exists():
                raise UpdaterError(f"安装前目标文件已出现：{destination.name}")
            os.replace(result.staged_path, destination)
            installed_paths.append(destination)
            installed_names.append(f"{name} {result.release.version}")
            targets[name] = destination
    except (OSError, UpdaterError) as exc:
        rollback_errors = []
        for path in reversed(installed_paths):
            try:
                path.unlink(missing_ok=True)
            except OSError as rollback_exc:
                rollback_errors.append(f"{path.name}: {rollback_exc}")
        detail = f"；回滚失败：{'; '.join(rollback_errors)}" if rollback_errors else ""
        raise UpdaterError(f"安装新包失败：{exc}{detail}") from exc
    return installed_names, targets


def remove_old_archives(
    targets: Mapping[str, Path],
    inventory: Inventory,
) -> tuple[list[Path], list[str]]:
    deleted: list[Path] = []
    errors: list[str] = []
    for name in sorted(targets, key=str.casefold):
        target = targets[name]
        for archive in inventory.archives.get(name, []):
            if archive.path == target:
                continue
            try:
                archive.path.unlink()
                deleted.append(archive.path)
            except OSError as exc:
                errors.append(f"{archive.path.name}：{exc}")
    return deleted, errors


def print_result(
    installed: list[str],
    deleted: list[Path],
    download_failures: Mapping[str, str],
    blocked: Mapping[str, str],
    delete_errors: list[str],
    plan: UpdatePlan,
) -> None:
    print(
        f"完成：安装 {len(installed)} 个新包，删除 {len(deleted)} 个旧版 zip，"
        f"下载失败 {len(download_failures)} 个，依赖阻止 {len(blocked)} 个。"
    )
    if installed:
        print("已安装：" + "、".join(installed))
    if deleted:
        print("已删除：" + "、".join(path.name for path in deleted))
    for name, reason in sorted(download_failures.items()):
        print(f"下载失败：{name}：{reason}")
    for failure in delete_errors:
        print(f"删除失败：{failure}")
    if plan.no_candidate:
        print(f"保留官网无当前兼容版本的本地模组：{len(plan.no_candidate)} 个。")
    if plan.conflicts:
        print(f"保留同版本本地改包：{len(plan.conflicts)} 个。")


def sha1_file(path: Path) -> str:
    digest = hashlib.sha1()  # noqa: S324 - compared with the Mod Portal release checksum
    with path.open("rb") as source:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def parse_version(value: str) -> tuple[int, int, int]:
    if not VERSION_RE.fullmatch(value):
        raise ValueError(f"版本必须使用 major.minor.patch 格式：{value}")
    major, minor, patch = value.split(".")
    return int(major), int(minor), int(patch)


if __name__ == "__main__":
    raise SystemExit(main())
