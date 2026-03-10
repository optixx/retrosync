#!/usr/bin/env python3

__author__ = "Optixx"
__license__ = "MIT"
__version__ = "0.1.0"
__maintainer__ = "David Voswinkel"
__email__ = "david@optixx.org"

import subprocess
import select
import shutil
import tempfile
import toml
import logging
import copy
import concurrent.futures
import click
import json
import glob
import fnmatch
import sys
import platform
import time
import re
import threading
import base64
import urllib.parse
import urllib.request
import urllib.error
import paramiko
import Levenshtein
from lxml import etree
from pathlib import Path
from collections import defaultdict
from rich.console import Group
from rich.panel import Panel
from rich.live import Live
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)


logger = logging.getLogger()

GLOBAL_EXCLUDE_PATTERNS = [
    ".DS_Store",
    "._*",
    ".Spotlight-V100",
    ".Trashes",
    ".fseventsd",
    "Thumbs.db",
    "desktop.ini",
    "__MACOSX",
    ".git",
    ".svn",
    ".zip",
]

item_tpl = {
    "path": "",
    "label": "",
    "core_path": "DETECT",
    "core_name": "DETECT",
    "crc32": "00000000|crc",
    "db_name": "",
}

current_system_progress = Progress(
    TimeElapsedColumn(),
    TextColumn("{task.description}"),
)

step_progress = Progress(
    TextColumn("  "),
    TimeElapsedColumn(),
    TextColumn("[bold purple]{task.fields[action]}"),
    SpinnerColumn("simpleDots"),
)

system_steps_progress = Progress(
    TextColumn("[bold blue]Progress for system {task.fields[name]}: {task.percentage:.0f}%"),
    BarColumn(),
    TextColumn("({task.completed} of {task.total} steps done)"),
)

transport_status_progress = Progress(
    TextColumn("  "),
    TextColumn("[bold cyan]{task.fields[msg]}"),
)

transport_file_progress = Progress(
    TextColumn("  "),
    TextColumn("[bold green]File Upload"),
    BarColumn(),
    TextColumn("{task.completed}/{task.total}"),
)

overall_progress = Progress(TimeElapsedColumn(), BarColumn(), TextColumn("{task.description}"))

progress_group = Group(
    Panel(
        Group(
            current_system_progress,
            step_progress,
            transport_status_progress,
            transport_file_progress,
            system_steps_progress,
        )
    ),
    overall_progress,
)

transport_status_task_id = None
transport_file_task_id = None


class TransportError(Exception):
    pass


def set_transport_status(message):
    if transport_status_task_id is None:
        return
    visible = bool(message)
    transport_status_progress.update(transport_status_task_id, msg=message, visible=visible)


def begin_transport_file_progress(total):
    if transport_file_task_id is None:
        return
    transport_file_progress.update(
        transport_file_task_id, total=max(0, total), completed=0, visible=bool(total)
    )


def advance_transport_file_progress(step=1):
    if transport_file_task_id is None:
        return
    transport_file_progress.update(transport_file_task_id, advance=step)


def end_transport_file_progress():
    if transport_file_task_id is None:
        return
    transport_file_progress.update(transport_file_task_id, visible=False)


def complete_transport_file_progress():
    if transport_file_task_id is None:
        return
    task = transport_file_progress.tasks[transport_file_task_id]
    transport_file_progress.update(transport_file_task_id, completed=task.total)


def get_transport_mode(default):
    return str(default.get("transport", "filesystem")).strip().lower()


class TransportFactory:
    def __new__(cls, default, dry_run, force_transport):
        mode = get_transport_mode(default)
        normalized_force_transport = (
            str(force_transport).strip().lower() if force_transport is not None else None
        )
        if normalized_force_transport in (None, "", "false"):
            normalized_force_transport = None

        if mode == "webdav":
            return TransportWebDAV(default, dry_run)

        if normalized_force_transport:
            if normalized_force_transport == "unix":
                return TransportUnixBase.getInstance(default, dry_run)
            elif normalized_force_transport == "windows":
                return TransportWindowsBase.getInstance(default, dry_run)
            else:
                raise NotImplementedError

        current_platform = platform.system()
        if current_platform in ["Darwin", "Linux"]:
            return TransportUnixBase.getInstance(default, dry_run)
        elif current_platform in ["Windows"]:
            return TransportWindowsBase.getInstance(default, dry_run)
        else:
            print(
                f"This script only runs on macOS, Linux or Windows but you're using {current_platform}. Exiting..."
            )
            sys.exit(1)


class TransportBase:
    def is_excluded_path(self, path: Path):
        for part in path.parts:
            for pattern in GLOBAL_EXCLUDE_PATTERNS:
                if fnmatch.fnmatch(part, pattern):
                    return True
        return False

    def guess_file_count(self, src_path: Path, whitelist: list, recursive=False):
        cnt = 0
        if recursive:
            generator = src_path.rglob("*")
        else:
            generator = src_path.glob("*")
        for filename in generator:
            if self.is_excluded_path(filename.relative_to(src_path)):
                continue
            if whitelist:
                for item in whitelist:
                    if filename.suffix == item:
                        cnt += 1
                        break
            else:
                cnt += 1

        return cnt


class TransportUnixBase(TransportBase):
    @staticmethod
    def getInstance(default, dry_run):
        if get_transport_mode(default) == "ssh":
            return TransportSSHUnix(default, dry_run)
        else:
            return TransportFileSystemUnix(default, dry_run)

    def __init__(self, default, dry_run):
        self.default = default
        self.dry_run = dry_run
        logger.debug(
            f"TransportUnixBase::__ctor__: dry_run={self.dry_run} transport={default.get('transport')}"
        )
        self.check()

    def check_executable_exists(self, executable_name):
        executable_path = shutil.which(executable_name)
        if not executable_path:
            print(f"Executable '{executable_name}' not found.")
            sys.exit(-1)

    def check(self):
        pass

    def ensure_dir_exists(self, path_directory: Path):
        pass

    def command_prefix(self):
        return ""

    def build_dest(self, path: Path):
        return f'"{path}"'

    def execute(self, cmd):
        logger.debug(f"execute: cmd={cmd}")
        if self.dry_run:
            return
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
        poll = select.poll()
        poll.register(p.stdout, select.POLLIN | select.POLLHUP)  # pyright: ignore
        poll.register(p.stderr, select.POLLIN | select.POLLHUP)  # pyright: ignore
        pollc = 2
        events = poll.poll()
        while pollc > 0 and len(events) > 0:
            for event in events:
                (rfd, event) = event
                if event & select.POLLIN:
                    if rfd == p.stdout.fileno():  # pyright: ignore
                        lines = p.stdout.readlines()  # pyright: ignore
                        for line in lines:
                            logger.debug("execute: stdout=%s", line)
                    if rfd == p.stderr.fileno():  # pyright: ignore
                        line = p.stderr.readline()  # pyright: ignore
                        logger.debug("execute: stderr=%s", line)
                if event & select.POLLHUP:
                    poll.unregister(rfd)
                    pollc = pollc - 1
                if pollc > 0:
                    events = poll.poll()
        p.wait()

    def copy_files(
        self,
        src_path: Path,
        dest_path: Path,
        whitelist: list,
        recursive: bool = False,
        callback=None,
    ):
        self.ensure_dir_exists(dest_path)
        args = "--outbuf=L --progress --verbose --human-readable --recursive --size-only --delete "
        for item in GLOBAL_EXCLUDE_PATTERNS:
            args += f'--exclude="{item}" '
        if whitelist:
            # Keep walking directories when using a whitelist, then include matching files.
            args += '--include="*/" '
            for item in whitelist:
                args += f'--include="*{item}" '
            args += '--exclude="*" '
        cmd = f'{self.command_prefix()} rsync {args} "{src_path}/" {self.build_dest(dest_path)}'
        self.execute(cmd)


class TransportFileSystemUnix(TransportUnixBase):
    def check(self):
        for command in ["rsync"]:
            self.check_executable_exists(command)

    def ensure_dir_exists(self, path_directory: Path):
        if not self.dry_run:
            if not path_directory.is_dir():
                path_directory.mkdir(parents=True)

    def copy_file(self, src_filename: Path, dest_filename: Path):
        self.ensure_dir_exists(dest_filename.parent)
        if not self.dry_run:
            shutil.copy(src_filename, dest_filename)


class TransportSSHUnix(TransportUnixBase):
    def check(self):
        for command in ["ssh", "scp", "rsync", "sshpass"]:
            self.check_executable_exists(command)

    def command_prefix(self):  # pyright: ignore
        password = self.default.get("password")
        return f'sshpass -p "{password}"'

    def build_dest(self, path):
        hostname = self.default.get("hostname")
        username = self.default.get("username")
        return f'"{username}@{hostname}:{path}"'

    def copy_file(self, src_filename: Path, dest_filename: Path):
        cmd = f'{self.command_prefix()} scp "{src_filename}" {self.build_dest(dest_filename)}'
        self.execute(cmd)

    def ensure_dir_exists(self, path_directory: Path):
        hostname = self.default.get("hostname")
        username = self.default.get("username")
        cmd = f"{self.command_prefix()} ssh {username}@{hostname} \"mkdir '{path_directory}'\""
        self.execute(cmd)


class TransportWindowsBase(TransportBase):
    @staticmethod
    def getInstance(default, dry_run):
        if get_transport_mode(default) == "ssh":
            return TransportSSHWindows(default, dry_run)
        else:
            return TransportFileSystemWindows(default, dry_run)


class TransportWebDAV(TransportBase):
    DEFAULT_MAX_WORKERS = 4

    def __init__(self, default, dry_run):
        self.default = default
        self.dry_run = dry_run
        self.host = str(self.default.get("host", "")).strip()
        self.username = str(self.default.get("username", "")).strip()
        self.password = str(self.default.get("password", ""))
        self.base_url = self._normalize_base_url(self.host)
        self._auth_header = self._build_auth_header()
        self._thread_local = threading.local()
        self._dir_lock = threading.Lock()
        self._known_dirs = {"/"}
        try:
            self.max_workers = max(
                1, int(self.default.get("webdav_max_workers", self.DEFAULT_MAX_WORKERS))
            )
        except (TypeError, ValueError):
            self.max_workers = self.DEFAULT_MAX_WORKERS
        logger.debug(
            "TransportWebDAV::__ctor__: dry_run=%s host=%s username=%s max_workers=%s",
            self.dry_run,
            self.base_url,
            self.username,
            self.max_workers,
        )
        if not self.base_url:
            print("WebDAV transport requires [webdav].host in the config.")
            sys.exit(-1)

    def _normalize_base_url(self, host):
        if not host:
            return ""
        if not host.startswith(("http://", "https://")):
            host = f"http://{host}"
        return host.rstrip("/")

    def _build_auth_header(self):
        if not self.username and not self.password:
            return None
        token = base64.b64encode(f"{self.username}:{self.password}".encode()).decode("ascii")
        return f"Basic {token}"

    def _build_opener(self):
        if not self.username and not self.password:
            return urllib.request.build_opener()
        password_mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        password_mgr.add_password(None, self.base_url, self.username, self.password)
        handlers = [
            urllib.request.HTTPBasicAuthHandler(password_mgr),
            urllib.request.HTTPDigestAuthHandler(password_mgr),
        ]
        return urllib.request.build_opener(*handlers)

    def _get_thread_opener(self):
        opener = getattr(self._thread_local, "opener", None)
        if opener is None:
            opener = self._build_opener()
            self._thread_local.opener = opener
        return opener

    def _status(self, message):
        set_transport_status(f"WebDAV: {message}")

    def _remote_path(self, path_value: Path):
        path = path_value.as_posix()
        home = Path.home().as_posix()

        # Configs often use local-style paths (e.g. ~/Sync/RetroArch) that expand
        # to /Users/<name>/...; map those to WebDAV-rooted paths.
        if path.startswith(f"{home}/"):
            path = path[len(home) + 1 :]
        elif path == home:
            path = ""

        # Normalize and force WebDAV-root relative path.
        path = path.lstrip("/")
        return f"/{path}" if path else "/"

    def _request_once(self, method, path, body=None, headers=None, ok_codes=(200, 201, 204, 207)):
        request_headers = dict(headers or {})
        encoded_path = urllib.parse.quote(path, safe="/")
        url = f"{self.base_url}{encoded_path}"
        request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
        logger.debug("TransportWebDAV::_request: method=%s path=%s", method, path)
        with self._get_thread_opener().open(request, timeout=30) as response:
            if response.status not in ok_codes:
                raise RuntimeError(f"WebDAV {method} {path} failed with HTTP {response.status}")
            logger.debug(
                "TransportWebDAV::_request: method=%s path=%s status=%s",
                method,
                path,
                response.status,
            )

    def _request(self, method, path, body=None, headers=None, ok_codes=(200, 201, 204, 207)):
        try:
            self._request_once(method, path, body=body, headers=headers, ok_codes=ok_codes)
            return
        except urllib.error.HTTPError as exc:
            if exc.code in ok_codes:
                return
            # Some servers advertise digest but fail in challenge flow; retry once
            # with explicit Basic auth if credentials exist.
            if exc.code == 401 and self._auth_header:
                retry_headers = dict(headers or {})
                retry_headers["Authorization"] = self._auth_header
                logger.debug(
                    "TransportWebDAV::_request: 401 for %s %s, retrying with preemptive Basic auth",
                    method,
                    path,
                )
                try:
                    self._request_once(
                        method, path, body=body, headers=retry_headers, ok_codes=ok_codes
                    )
                    return
                except urllib.error.HTTPError as retry_exc:
                    if retry_exc.code in ok_codes:
                        return
                    raise RuntimeError(
                        f"WebDAV {method} {path} failed with HTTP 401 (Unauthorized). "
                        "Check [webdav] username/password and target path permissions."
                    ) from retry_exc
            if exc.code == 401:
                raise RuntimeError(
                    f"WebDAV {method} {path} failed with HTTP 401 (Unauthorized). "
                    "Check [webdav] username/password and target path permissions."
                ) from exc
            raise RuntimeError(f"WebDAV {method} {path} failed with HTTP {exc.code}") from exc
        except (urllib.error.URLError, ConnectionResetError, TimeoutError, OSError) as exc:
            raise TransportError(
                "WebDAV connection failed during transfer. "
                "The target may be offline or unreachable."
            ) from exc

    def _mkcol(self, path):
        try:
            self._request("MKCOL", path, ok_codes=(201, 301, 405))
            return
        except RuntimeError as exc:
            if self._path_exists(path):
                logger.debug(
                    "TransportWebDAV::_mkcol: MKCOL failed but path already exists, continuing path=%s error=%s",
                    path,
                    str(exc),
                )
                return
            raise

    def _path_exists(self, path):
        headers = {"Depth": "0"}
        try:
            self._request("PROPFIND", path, headers=headers, ok_codes=(200, 207))
            return True
        except Exception:
            return False

    def ensure_dir_exists(self, path_directory: Path):
        if self.dry_run:
            return
        remote = self._remote_path(path_directory)
        parts = [part for part in remote.split("/") if part]
        current = ""
        for part in parts:
            current = f"{current}/{part}"
            with self._dir_lock:
                known = current in self._known_dirs
            if known:
                logger.debug("TransportWebDAV::ensure_dir_exists: cache hit %s", current)
                continue
            if self._path_exists(current):
                logger.debug("TransportWebDAV::ensure_dir_exists: exists on server %s", current)
                with self._dir_lock:
                    self._known_dirs.add(current)
                continue
            logger.debug("TransportWebDAV::ensure_dir_exists: creating %s", current)
            self._mkcol(current)
            with self._dir_lock:
                self._known_dirs.add(current)

    def copy_file(self, src_filename: Path, dest_filename: Path, *, ensure_parent=True):
        if self.dry_run:
            logger.debug(
                "TransportWebDAV::copy_file: dry-run %s -> %s", src_filename, dest_filename
            )
            return
        if ensure_parent:
            self.ensure_dir_exists(dest_filename.parent)
        file_size = src_filename.stat().st_size
        with open(src_filename, "rb") as fd:
            data = fd.read()
        remote = self._remote_path(dest_filename)
        logger.debug(
            "TransportWebDAV::copy_file: upload start src=%s dest=%s bytes=%s",
            src_filename,
            remote,
            file_size,
        )
        started = time.monotonic()
        self._request(
            "PUT", remote, body=data, headers={"Content-Type": "application/octet-stream"}
        )
        elapsed = time.monotonic() - started
        logger.debug(
            "TransportWebDAV::copy_file: upload done src=%s dest=%s bytes=%s elapsed=%.2fs",
            src_filename,
            remote,
            file_size,
            elapsed,
        )

    def copy_files(
        self,
        src_path: Path,
        dest_path: Path,
        whitelist: list,
        recursive: bool = False,
        callback=None,
    ):
        if self.dry_run:
            logger.debug("TransportWebDAV::copy_files: dry-run %s -> %s", src_path, dest_path)
            return

        if recursive:
            generator = src_path.rglob("*")
        else:
            generator = src_path.glob("*")

        files = []
        for src_filename in generator:
            if src_filename.is_dir():
                continue
            rel = src_filename.relative_to(src_path)
            if self.is_excluded_path(rel):
                continue
            if whitelist and src_filename.suffix not in whitelist:
                continue
            files.append((src_filename, dest_path / rel))

        total = len(files)
        if total == 0:
            return

        # Precreate all parent directories once to avoid repeated MKCOL calls per file.
        unique_parents = sorted({dest.parent for _, dest in files}, key=lambda p: len(p.parts))
        for parent in unique_parents:
            self.ensure_dir_exists(parent)

        if self.max_workers <= 1 or total == 1:
            for idx, (src_filename, dest_filename) in enumerate(files, start=1):
                logger.debug(
                    "TransportWebDAV::copy_files: [%s/%s] %s -> %s",
                    idx,
                    total,
                    src_filename,
                    dest_filename,
                )
                self.copy_file(src_filename, dest_filename, ensure_parent=False)
                if callback:
                    callback()
            return

        logger.debug(
            "TransportWebDAV::copy_files: parallel upload workers=%s files=%s",
            self.max_workers,
            total,
        )

        def upload_one(src_filename, dest_filename):
            self.copy_file(src_filename, dest_filename, ensure_parent=False)

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers)
        interrupted = False
        future_to_index = {}
        try:
            for idx, (src_filename, dest_filename) in enumerate(files, start=1):
                logger.debug(
                    "TransportWebDAV::copy_files: queue [%s/%s] %s -> %s",
                    idx,
                    total,
                    src_filename,
                    dest_filename,
                )
                future = executor.submit(upload_one, src_filename, dest_filename)
                future_to_index[future] = idx

            for future in concurrent.futures.as_completed(future_to_index):
                idx = future_to_index[future]
                future.result()
                logger.debug("TransportWebDAV::copy_files: completed [%s/%s]", idx, total)
                if callback:
                    callback()
        except KeyboardInterrupt as exc:
            interrupted = True
            logger.debug("TransportWebDAV::copy_files: interrupted, cancelling worker futures")
            for future in future_to_index:
                future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            raise TransportError("Transfer interrupted by user.") from exc
        finally:
            if not interrupted:
                executor.shutdown(wait=True, cancel_futures=False)


class TransportFileSystemWindows(TransportWindowsBase):
    def __init__(self, default, dry_run):
        self.default = default
        self.dry_run = dry_run
        logger.debug(f"TransportFileSystemWindows::__ctor__: dry_run={self.dry_run}")

    def check(self):
        pass

    def ensure_dir_exists(self, path_directory: Path):
        if not self.dry_run:
            if not path_directory.is_dir():
                path_directory.mkdir(parents=True)

    def copy_file(self, src_filename: Path, dest_filename: Path):
        self.ensure_dir_exists(dest_filename.parent)
        if not self.dry_run:
            shutil.copy(src_filename, dest_filename)

    def copy_files(
        self,
        src_path: Path,
        dest_path: Path,
        whitelist: list,
        recursive: bool = False,
        callback=None,
    ):
        guessed_len = self.guess_file_count(src_path, whitelist, recursive)
        logger.debug(f"TransportFileSystemWindows::copy_files: {src_path} -> {dest_path}")
        self.ensure_dir_exists(dest_path)
        cnt = 1
        for item in src_path.iterdir():
            logger.debug(f"TransportLocaleWindows::copy_files: [{cnt}/{guessed_len}] {item.name}")
            if callback:
                callback()
            cnt += 1
            s = item
            if self.is_excluded_path(s.relative_to(src_path)):
                logger.debug(f"TransportFileSystemWindows::copy_files: excluded {s}")
                continue
            d = dest_path / item.name
            if s.is_dir():
                self.copy_files(s, d, whitelist, recursive, callback)
            else:
                if not self.dry_run:
                    shutil.copy2(s, d)


class TransportSSHWindows(TransportWindowsBase):
    def __init__(self, default, dry_run):
        self.default = default
        self.dry_run = dry_run
        self.ssh = paramiko.SSHClient()
        self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.sftp = None
        self.connected = False
        logger.debug(f"TransportSSHWindows::__ctor__: dry_run={self.dry_run}")

    def connect(self):
        if self.dry_run:
            return
        if self.connected:
            return
        logger.debug("TransportSSHWindows::connect start")
        self.ssh.connect(
            self.default.get("hostname"),
            username=self.default.get("username"),
            password=self.default.get("password"),
        )
        logger.debug("TransportSSHWindows::connect connected")
        self.sftp = self.ssh.open_sftp()
        logger.debug("TransportSSHWindows::connect sftp opened")
        self.connected = True

    def copy_file(self, src_filename: Path, dest_filename: Path):
        self.connect()
        if self.dry_run:
            logger.debug(
                f"TransportSSHWindows::copy_file: dry-run {src_filename} to {dest_filename}"
            )
            return

        try:
            dest_file_attr = self.sftp.stat(str(dest_filename))
            src_file_attr = src_filename.stat()
            if (
                int(src_file_attr.st_mtime) > int(dest_file_attr.st_mtime)  # type: ignore
                or src_file_attr.st_size != dest_file_attr.st_size
            ):
                self.sftp.put(str(src_filename), str(dest_filename))
                logger.debug(
                    f"TransportSSHWindows::copy_file: newer {src_filename} to {dest_filename}"
                )
        except FileNotFoundError:
            self.sftp.put(str(src_filename), str(dest_filename))
            logger.debug(
                f"TransportSSHWindows::copy_file: created {src_filename} to {dest_filename}"
            )

    def ensure_dir_exists(self, dest_directory: Path):
        logger.debug(f"TransportSSHWindows::ensure_dir_exists: check {dest_directory}")
        if self.dry_run:
            logger.debug(f"TransportSSHWindows::ensure_dir_exists: created {dest_directory}")
            return

        try:
            self.sftp.stat(str(dest_directory))
        except FileNotFoundError:
            self.sftp.mkdir(str(dest_directory))
            logger.debug(f"TransportSSHWindows::ensure_dir_exists: created {dest_directory}")

    def copy_files(
        self,
        src_path: Path,
        dest_path: Path,
        whitelist: list,
        recursive: bool = False,
        callback=None,
    ):
        guessed_len = self.guess_file_count(src_path, whitelist, recursive)
        logger.debug(f"TransportSSHWindows::copy_files: {src_path} -> {dest_path}")
        self.connect()
        self.ensure_dir_exists(dest_path)
        cnt = 1

        for _, src_filename in enumerate(src_path.iterdir()):
            logger.debug(
                f"TransportSSHWindows::copy_files: [{cnt}/{guessed_len}] {src_filename.name}"
            )
            if callback:
                callback()
            if self.is_excluded_path(src_filename.relative_to(src_path)):
                logger.debug(f"TransportSSHWindows::copy_files: excluded {src_filename}")
                continue
            dest_filename = dest_path / src_filename.name

            if src_filename.is_dir():
                if recursive:
                    self.copy_files(
                        src_filename, dest_path / src_filename.name, whitelist, recursive, callback
                    )
                else:
                    continue
            else:
                cnt += 1
                if whitelist:
                    match = False
                    for item in whitelist:
                        if src_filename.suffix == item:
                            match = True
                            break
                    if not match:
                        logger.debug(
                            f"TransportSSHWindows::copy_files: not whitelist match {src_filename}"
                        )
                        continue

                if self.dry_run:
                    logger.debug(
                        f"TransportSSHWindows::copy_files: dry-run {src_filename} to {dest_filename}"
                    )
                    continue

                try:
                    dest_file_attr = self.sftp.stat(str(dest_filename))
                    src_file_attr = src_filename.stat()
                    if (
                        int(src_file_attr.st_mtime) > int(dest_file_attr.st_mtime)
                        or src_file_attr.st_size != dest_file_attr.st_size
                    ):
                        self.sftp.put(str(src_filename), str(dest_filename))
                        logger.debug(
                            f"TransportSSHWindows::copy_files: newer/size {src_filename} to {dest_filename}"
                        )
                except FileNotFoundError:
                    self.sftp.put(str(src_filename), str(dest_filename))
                    logger.debug(
                        f"TransportSSHWindows::copy_files: create {src_filename} to {dest_filename}"
                    )


class JobBase:
    pass


class GlobalJob(JobBase):
    def __init__(self, default, playlists, transport):
        self.default = default
        self.playlists = playlists
        self.transport = transport
        self.size = 1
        self.setup()

    def setup(self):
        pass


class BiosSync(GlobalJob):
    name = "BIOS"

    def setup(self):
        self.src = Path(self.default.get("src_bios"))
        self.dst = Path(self.default.get("dest_bios"))
        self.size = self.transport.guess_file_count(self.src, [], True)

    def do(self, callback=None):
        self.transport.copy_files(
            self.src,
            self.dst,
            whitelist=[],
            recursive=True,
            callback=callback,
        )


class ThumbnailsSync(BiosSync):
    name = "Thumbnails"

    def setup(self):
        self.src = Path(self.default.get("src_thumbnails"))
        self.dst = Path(self.default.get("dest_thumbnails"))
        self.size = self.transport.guess_file_count(self.src, [], True)


class FavoritesSync(BiosSync):
    name = "Favorites"

    def setup(self):
        self.src = Path(self.default.get("src_config")) / "content_favorites.lpl"
        self.dst = Path(self.default.get("dest_config")) / "content_favorites.lpl"
        self.size = 1

    def do(self, callback=None):
        with tempfile.NamedTemporaryFile() as temp_file:
            self.migrate(
                self.src,
                temp_file,
            )
            self.transport.copy_file(
                Path(temp_file.name),
                self.dst,
            )
            if callback:
                callback()

    def migrate(self, favorites_file, temp_file):
        def find_playlist(playlists, src_core_name):
            for p in playlists:
                if p.get("src_core_name") == src_core_name:
                    return p
            print(f"Can not find core {src_core_name}")
            raise AssertionError()

        logger.debug(f"migrate: filename={favorites_file}")
        with open(favorites_file) as file:
            data = json.load(file)

        items = []
        src_items = data["items"]
        src_items_len = len(src_items)
        for idx, item in enumerate(src_items):
            new_item = copy.copy(item)
            playlist = find_playlist(self.playlists, new_item["core_name"])
            dest_rom_dir = Path(self.default.get("target_roms")) / playlist.get("dest_folder")
            src_path = new_item["path"].split("#")[0]
            src_name = Path(src_path).name
            new_path = dest_rom_dir / src_name
            new_item["path"] = str(new_path)
            core_path = (
                new_item["core_path"]
                .replace(
                    self.default.get("src_cores_suffix"), self.default.get("target_cores_suffix")
                )
                .replace(self.default.get("src_cores"), self.default.get("target_cores"))
            )
            new_item["core_path"] = core_path
            logger.debug(f"migrate: Convert [{idx + 1}/{src_items_len}] path={src_name}")
            items.append(new_item)

        data["items"] = items
        doc = json.dumps(data)
        logger.debug(json.dumps(data, indent=2))
        temp_file.write(doc.encode("utf-8"))
        temp_file.flush()
        temp_file.seek(0)


class SystemJob(JobBase):
    def __init__(self, default, transport):
        self.default = default
        self.transport = transport
        self.size = 1

    def get_src_rom_roots(self):
        src_roms = self.default.get("src_roms")
        if isinstance(src_roms, list):
            roots = [Path(item) for item in src_roms]
        else:
            roots = [Path(src_roms)]
        return roots

    def get_primary_src_rom_root(self):
        roots = self.get_src_rom_roots()
        if not roots:
            raise AssertionError("No source ROM directories configured")
        return roots[0]


class RomSyncJob(SystemJob):
    name = "Sync ROMs"

    def setup(self, playlist):
        self.playlist = playlist
        self.src = self.get_primary_src_rom_root() / self.playlist.get("src_folder")
        self.dst = Path(self.default.get("dest_roms")) / self.playlist.get("dest_folder")
        self.size = self.transport.guess_file_count(self.src, [], True)

    def do(self, callback):
        self.transport.copy_files(
            self.src, self.dst, whitelist=[], recursive=True, callback=callback
        )


class PlaylistSyncJob(SystemJob):
    name = "Sync Playlist"

    def setup(self, playlist):
        self.playlist = playlist
        self.size = 1

    def migrate_playlist(self, temp_file):
        name = self.playlist.get("name")
        logger.debug(f"migrate_playlist: name={name}")
        local = Path(self.default.get("src_playlists")) / name
        with open(local) as file:
            data = json.load(file)

        core_path = (
            data["default_core_path"]
            .replace(self.default.get("src_cores_suffix"), self.default.get("target_cores_suffix"))
            .replace(self.default.get("src_cores"), self.default.get("target_cores"))
        )
        src_rom_dirs = [root / self.playlist.get("src_folder") for root in self.get_src_rom_roots()]
        target_rom_dir = Path(self.default.get("target_roms")) / self.playlist.get("dest_folder")
        data["default_core_path"] = core_path
        data["scan_content_dir"] = str(target_rom_dir)
        data["scan_dat_file_path"] = ""

        items = []
        src_items = data["items"]
        src_items_len = len(src_items)
        for idx, item in enumerate(src_items):
            new_item = copy.copy(item)
            new_item["core_name"] = "DETECT"
            new_item["core_path"] = "DETECT"
            src_path = new_item["path"].split("#")[0]
            src_name = Path(src_path).name
            logger.debug(f"migrate_playlist: Convert [{idx + 1}/{src_items_len}] path={src_name}")
            new_path = src_path
            for src_rom_dir in src_rom_dirs:
                new_path = new_path.replace(str(src_rom_dir), str(target_rom_dir))
            new_item["path"] = new_path
            items.append(new_item)

        data["items"] = items
        doc = json.dumps(data)
        logger.debug(json.dumps(data, indent=2))
        temp_file.write(doc.encode("utf-8"))
        temp_file.flush()
        temp_file.seek(0)

    def do(self, _callback):
        name = self.playlist.get("name")
        with tempfile.NamedTemporaryFile() as temp_file:
            self.migrate_playlist(temp_file)
            self.transport.copy_file(
                Path(temp_file.name), Path(self.default.get("dest_playlists")) / name
            )


class PlaylistUpdatecJob(SystemJob):
    name = "Update Playlist"

    def setup(self, playlist):
        self.playlist = playlist
        self.size = 1

    def backup_file(self, file_path):
        original_file = Path(file_path)
        backup_file = original_file.with_suffix(original_file.suffix + ".backup")
        backup_file.write_bytes(original_file.read_bytes())
        logger.debug(f"backup_file: created {backup_file}")
        return str(backup_file)

    def make_item(self, local, file):
        stem = str(Path(file).stem)
        new_item = copy.copy(item_tpl)
        new_item["path"] = file
        new_item["label"] = self.name_map.get(stem, stem)
        new_item["db_name"] = local.name
        return new_item

    def create_m3u(self, src_rom_dir):
        logger.debug("create_m3u: Create m3u files")
        m3u_pattern = self.playlist.get("src_m3u_pattern")
        m3u_whitelist = self.playlist.get("src_m3u_whitelist")
        files = defaultdict(list)
        all_files = Path(src_rom_dir)
        for filename in all_files.iterdir():
            if re.compile(m3u_whitelist).search(str(filename)):
                e = re.compile(m3u_pattern)
                m = e.match(str(filename))
                if m:
                    base_name = m.groups()[0].strip()
                else:
                    base_name = filename.stem
                files[base_name].append(filename)
        for base_name, list_files in files.items():
            m3u_file = Path(src_rom_dir) / f"{base_name}.m3u"
            if not self.transport.dry_run:
                with open(m3u_file, "w") as f:
                    logger.debug(f"create_m3u: Create  {str(m3u_file)}")
                    for filename in sorted(list_files):
                        f.write(f"{filename.name}\n")

    def build_file_map(self, src_rom_dir, dat_file):
        name_map = {}
        if not dat_file:
            return name_map
        dat_file = src_rom_dir / dat_file
        with open(dat_file) as fd:
            data = fd.read()
        root = etree.fromstring(data)
        for game in root.xpath("//game"):
            description = game.findtext("description")
            if description:
                name_map[game.attrib["name"]] = description
                continue
            if game.attrib.get("parent"):
                continue
            identity = game.findall("identity")
            title = identity[0].findtext("title")
            if title:
                name_map[game.attrib["name"]] = title
        return name_map

    def do(self, _callback):
        name = self.playlist.get("name")
        logger.debug(f"migrate_playlist: name={name}")
        local = Path(self.default.get("src_playlists")) / name
        if not self.transport.dry_run:
            self.backup_file(local)

        with open(local) as file:
            data = json.load(file)

        src_rom_dir = self.get_primary_src_rom_root() / self.playlist.get("src_folder")

        core_path = Path(self.default.get("src_cores")) / self.playlist.get("src_core_path")
        core_path = core_path.with_suffix(self.default.get("src_cores_suffix"))
        data["default_core_path"] = str(core_path)
        data["default_core_name"] = self.playlist.get("src_core_name")
        data["scan_content_dir"] = str(src_rom_dir)
        data["scan_dat_file_path"] = str(src_rom_dir)

        if self.playlist.get("src_create_m3u"):
            self.create_m3u(src_rom_dir)

        whitelist = self.playlist.get("src_whitelist", False)
        blacklist = self.playlist.get("src_blacklist", False)
        self.name_map = self.build_file_map(src_rom_dir, self.playlist.get("src_dat_file", ""))
        items = []
        files = glob.glob(str(src_rom_dir / "*"))
        files.sort()
        files_len = len(files)

        file_list = []
        # First Pass
        for idx, file in enumerate(files):
            logger.debug(
                f"update_playlist: Update first pass [{idx + 1}/{files_len}] path={Path(file).name}"
            )
            if Path(file).is_dir():
                subs = glob.glob(str(Path(file) / "*"))
                for sub in subs:
                    file_list.append(sub)
            else:
                file_list.append(file)

        # Second Pass
        files_len = len(file_list)
        for idx, file in enumerate(file_list):
            logger.debug(
                f"update_playlist: Update second pass [{idx + 1}/{files_len}] path={Path(file).name}"
            )

            if blacklist:
                if re.compile(blacklist).search(file):
                    logger.debug(f"update_playlist: Skip {Path(file).name} is blacklisted")
                    continue

            if whitelist:
                if re.compile(whitelist).search(file):
                    logger.debug(f"update_playlist: Add {Path(file).name} is whitelisted")
                    items.append(self.make_item(local, file))
            else:
                items.append(self.make_item(local, file))

        data["items"] = items
        doc = json.dumps(data, indent=2)
        logger.debug(json.dumps(data, indent=2))
        if not self.transport.dry_run:
            with open(str(local), "w") as new_file:
                new_file.write(doc)


def rank_system_matches(system_name, playlists, limit=8):
    if not system_name:
        return []

    def normalize(value):
        if value is None:
            return ""
        value = value.lower().replace(".lpl", "")
        value = re.sub(r"[^a-z0-9]+", " ", value)
        return re.sub(r"\s+", " ", value).strip()

    def acronym(value):
        tokens = normalize(value).split()
        parts = []
        for token in tokens:
            if token.isdigit():
                parts.append(token)
            else:
                parts.append(token[0])
        return "".join(parts)

    needle = normalize(system_name)
    candidates = []
    for playlist in playlists:
        if playlist.get("disabled", False):
            continue
        playlist_name = playlist.get("name")
        n1 = normalize(playlist_name)
        n2 = normalize(playlist.get("dest_folder"))

        d1 = Levenshtein.distance(n1, needle, weights=(1, 1, 2))
        d2 = Levenshtein.distance(n2, needle, weights=(1, 1, 2))
        best_dist = min(d1, d2)
        best_len = min(max(len(n1), len(needle)), max(len(n2), len(needle)))
        ratio = best_dist / max(best_len, 1)

        rank = 99
        if needle == n1 or needle == n2:
            rank = 0
        elif needle in n1 or needle in n2:
            rank = 1
        else:
            needle_parts = needle.split()
            if needle_parts and all(part in n1 or part in n2 for part in needle_parts):
                rank = 2
            else:
                a1 = acronym(n1)
                a2 = acronym(n2)
                if needle in a1 or needle in a2:
                    rank = 3
                elif ratio <= 0.45:
                    rank = 4

        logger.debug(
            f"system: needle='{needle}' name='{n1}' dest='{n2}' rank={rank} d={best_dist} ratio={ratio:.2f}"
        )
        # Always keep fuzzy candidates so interactive selection can still offer options.
        if rank < 99:
            candidates.append((rank, ratio, best_dist, playlist_name))
        else:
            candidates.append((5, ratio, best_dist, playlist_name))

    candidates.sort(key=lambda x: (x[0], x[1], x[2], x[3]))

    # Deduplicate by playlist name while preserving best score.
    selected = []
    seen = set()
    for item in candidates:
        name = item[3]
        if name not in seen:
            selected.append(name)
            seen.add(name)
        if len(selected) >= limit:
            break
    return selected


def expand_config(default):
    default = dict(default)

    def apply_core_flavor_defaults():
        src_flavor = str(default.get("src_flavor", "")).strip().lower()
        target_flavor = str(default.get("target_flavor", "")).strip().lower()

        src_suffix_by_flavor = {
            "apple": ".dylib",
            "linux": ".so",
        }
        target_suffix_by_flavor = {
            "apple": ".framework",
            "linux": ".so",
        }

        if default.get("src_cores_suffix") is None and src_flavor in src_suffix_by_flavor:
            default["src_cores_suffix"] = src_suffix_by_flavor[src_flavor]

        if default.get("target_cores_suffix") is None and target_flavor in target_suffix_by_flavor:
            default["target_cores_suffix"] = target_suffix_by_flavor[target_flavor]

    def ensure_retroarch_paths(prefix):
        base_key = f"{prefix}_retroarch_base"
        base = default.get(base_key)
        if not base:
            return

        base_path = Path(base)
        derived = {
            f"{prefix}_playlists": base_path / "playlists",
            f"{prefix}_bios": base_path / "system",
            f"{prefix}_config": base_path / "config",
            f"{prefix}_cores": base_path / "cores",
            f"{prefix}_thumbnails": base_path / "thumbnails",
        }

        for key, value in derived.items():
            if default.get(key) is None:
                default[key] = str(value)

    ensure_retroarch_paths("src")
    ensure_retroarch_paths("dest")
    apply_core_flavor_defaults()

    src_roms = default.get("src_roms")
    if isinstance(src_roms, list):
        expanded_src_roms = [str(Path(item).expanduser()) for item in src_roms]
    else:
        expanded_src_roms = [str(Path(src_roms).expanduser())]
    default["src_roms"] = expanded_src_roms

    for item in [
        "src_playlists",
        "src_bios",
        "src_config",
        "src_cores",
        "src_thumbnails",
        "dest_playlists",
        "dest_bios",
        "dest_config",
        "dest_roms",
        "dest_thumbnails",
    ]:
        if default.get(item) is not None:
            default[item] = str(Path(default.get(item)).expanduser())
    return default


def normalize_transport_config(config, transport_override=None):
    def normalize_mode(value):
        mode = str(value).strip().lower()
        if mode not in {"filesystem", "ssh", "webdav"}:
            raise ValueError(
                f"Unsupported transport mode '{value}'. Use filesystem, ssh, or webdav."
            )
        return mode

    default = config.get("default", {})
    selected_mode = transport_override or default.get("transport", "filesystem")
    default["transport"] = normalize_mode(selected_mode)

    # Preferred split config sections:
    #   [ssh] for hostname/username/password
    #   [webdav] for host/username/password
    # Keep [remote] as backward-compatible fallback for SSH.
    ssh = config.get("ssh", {})
    webdav = config.get("webdav", {})
    remote = config.get("remote", {})

    section_map = {
        "hostname": ssh,
        "username": ssh,
        "password": ssh,
    }
    for item, section in section_map.items():
        if item in section:
            default[item] = section.get(item)
        elif item in remote:
            default[item] = remote.get(item)
        elif item not in default:
            default[item] = ""

    if default["transport"] == "webdav":
        default["host"] = webdav.get("host", "")
        default["username"] = webdav.get("username", "")
        default["password"] = webdav.get("password", "")

    return default


def normalize_playlists(playlists):
    for playlist in playlists:
        if playlist.get("dest_folder") is None:
            playlist["dest_folder"] = playlist.get("src_folder")
    return playlists


@click.command()
@click.option(
    "--all",
    "-a",
    "do_all",
    is_flag=True,
    help="Sync all files (ROMs, playlists,favorites, BIOS files and thumbnails)",
)
@click.option(
    "--sync-playlists",
    "-p",
    "do_sync_playlists",
    is_flag=True,
    help="Sync playlist files",
)
@click.option(
    "--sync-bios", "-b", "do_sync_bios", is_flag=True, help="Sync BIOS files to target system"
)
@click.option(
    "--sync-favorites",
    "-f",
    "do_sync_favorites",
    is_flag=True,
    help="Sync sync favorites files  to target system",
)
@click.option(
    "--sync-thumbnails",
    "-t",
    "do_sync_thumbails",
    is_flag=True,
    help="Sync thumbnails files",
)
@click.option(
    "--sync-roms", "-r", "do_sync_roms", is_flag=True, help="Sync ROMs files to target system"
)
@click.option(
    "--update-playlists",
    "-u",
    "do_update_playlists",
    is_flag=True,
    help="Update local playlist files by scanning the ROM directories for results",
)
@click.option(
    "--name",
    "-n",
    "system_name",
    default=None,
    help="Filter and process only one specific system",
)
@click.option(
    "--config-file",
    "-c",
    "config_file",
    default="steamdeck.conf",
    help="Use config file",
)
@click.option(
    "--transport",
    "transport_override",
    type=click.Choice(["filesystem", "ssh", "webdav"], case_sensitive=False),
    default=None,
    help="Override transport mode from config (filesystem, ssh, webdav)",
)
@click.option("--dry-run", "-D", is_flag=True, help="Dry run, don't sync or create anything")
@click.option(
    "--debug",
    "-d",
    "do_debug",
    is_flag=True,
    help="Enable debug logging to debug.log logfile",
)
@click.option(
    "--transport-unix",
    "force_transport",
    flag_value="unix",
    default=False,
    help="Compel usage of scp and rsync command-line utilities (faster)",
)
@click.option(
    "--transport-windows",
    "force_transport",
    flag_value="windows",
    default=False,
    help="Utilize Python's implementation of the SSH transport (slower)",
)
@click.option("--yes", is_flag=True, help="Skip prompt inputs by saying yes to everything")
def main(
    do_all,
    do_sync_playlists,
    do_sync_bios,
    do_sync_favorites,
    do_sync_thumbails,
    do_sync_roms,
    do_update_playlists,
    system_name,
    config_file,
    transport_override,
    dry_run,
    do_debug,
    force_transport,
    yes,
):
    global logger
    global transport_status_task_id
    global transport_file_task_id
    if do_debug:
        logging.basicConfig(
            filename="debug.log",
            filemode="a",
            level=logging.DEBUG,
            format="%(asctime)s - %(levelname)s - %(message)s",
        )
        logger = logging.getLogger()
    else:
        logging.basicConfig(
            level=logging.WARN,
            format="%(asctime)s - %(levelname)s - %(message)s",
        )
        logger = logging.getLogger()
        logger.disabled = True

    if do_all:
        do_sync_playlists = do_sync_roms = do_sync_bios = do_sync_favorites = do_sync_thumbails = (
            do_update_playlists
        ) = True

    if not any(
        [
            do_sync_playlists,
            do_sync_roms,
            do_sync_bios,
            do_sync_favorites,
            do_sync_thumbails,
            do_update_playlists,
        ]
    ):
        click.echo(click.get_current_context().get_help())
        sys.exit(0)

    config = toml.load(config_file)
    normalized_transport_override = (
        str(transport_override).strip().lower() if transport_override is not None else None
    )
    default = expand_config(
        normalize_transport_config(config, transport_override=normalized_transport_override)
    )
    playlists = normalize_playlists(config.get("playlists", []))
    if system_name:
        matches = rank_system_matches(system_name, playlists)
        if not matches:
            print(f"No playlist match found for '{system_name}'.")
            sys.exit(-1)
        if yes:
            system_name = matches[0]
        else:
            print(f"Select a playlist match for '{system_name}':")
            for idx, match in enumerate(matches, start=1):
                print(f"{idx}. {match}")
            print("0. Cancel")
            selected = click.prompt(
                "Enter selection number",
                type=click.IntRange(0, len(matches)),
            )
            if selected == 0:
                sys.exit(-1)
            system_name = matches[selected - 1]

    jobs = []
    transport = TransportFactory(default, dry_run, force_transport)
    if do_sync_bios:
        jobs.append(BiosSync(default, playlists, transport))

    if do_sync_favorites:
        jobs.append(FavoritesSync(default, playlists, transport))

    if do_sync_thumbails:
        jobs.append(ThumbnailsSync(default, playlists, transport))

    system_jobs = []
    if do_update_playlists:
        system_jobs.append(PlaylistUpdatecJob(default, transport))

    if do_sync_playlists:
        system_jobs.append(PlaylistSyncJob(default, transport))

    if do_sync_roms:
        system_jobs.append(RomSyncJob(default, transport))

    if system_name:
        playlists = [p for p in playlists if p.get("name") == system_name]

    systems = {}
    for _, playlist in enumerate(playlists):
        name = Path(playlist.get("name")).stem
        if not playlist.get("disabled", False):
            systems[name] = {"name": name, "playlist": playlist}

    overall_total = len(jobs) + (len(systems) if system_jobs else 0)
    overall_task_id = overall_progress.add_task("", total=overall_total)
    with Live(progress_group):
        transport_status_task_id = transport_status_progress.add_task("", msg="", visible=False)
        transport_file_task_id = transport_file_progress.add_task("", total=0, visible=False)
        for (
            idx,
            job,
        ) in enumerate(jobs):
            top_descr = f"[bold #AAAAAA]({idx} out of {len(jobs)} jobs done)"
            overall_progress.update(overall_task_id, description=top_descr)
            current_task_id = current_system_progress.add_task(f"Run job {job.name}")
            system_steps_task_id = system_steps_progress.add_task("", total=2, name=job.name)
            system_steps_progress.update(system_steps_task_id, advance=1)
            begin_transport_file_progress(job.size)
            try:
                job.do(callback=lambda: advance_transport_file_progress(1))
                complete_transport_file_progress()
            except TransportError as exc:
                end_transport_file_progress()
                set_transport_status("")
                print(f"Transfer aborted: {exc}")
                sys.exit(-1)
            finally:
                end_transport_file_progress()
            if dry_run:
                time.sleep(0.2)
            system_steps_progress.update(system_steps_task_id, advance=1)
            system_steps_progress.update(system_steps_task_id, visible=False)
            current_system_progress.stop_task(current_task_id)
            current_system_progress.update(
                current_task_id, description=f"[bold green]{job.name} synced!"
            )
            overall_progress.update(overall_task_id, advance=1)

        overall_progress.update(
            overall_task_id,
            description=f"[bold green]{len(jobs)} jobs processed, done!",
        )

        if do_update_playlists or do_sync_playlists or do_sync_roms:
            for (
                idx,
                key,
            ) in enumerate(systems.keys()):
                name = systems[key]["name"]
                playlist = systems[key]["playlist"]
                logger.info("main: Process %s", playlist.get("name"))
                top_descr = f"[bold #AAAAAA]({idx} out of {len(systems)} systems synced)"
                overall_progress.update(overall_task_id, description=top_descr)
                current_task_id = current_system_progress.add_task(f"Syncing system {name}")

                system_jobs_size = 0
                for job in system_jobs:
                    job.setup(playlist)
                    system_jobs_size += job.size

                system_steps_task_id = system_steps_progress.add_task(
                    "", total=system_jobs_size, name=name
                )
                for job in system_jobs:
                    step_task_id = step_progress.add_task("", action=job.name, name=name)
                    if do_debug:
                        time.sleep(1)

                    job.setup(playlist)
                    begin_transport_file_progress(job.size)
                    try:
                        job.do(
                            lambda system_steps_task_id=system_steps_task_id: (
                                system_steps_progress.update(system_steps_task_id, advance=1),
                                advance_transport_file_progress(1),
                            )
                        )
                        complete_transport_file_progress()
                    except TransportError as exc:
                        end_transport_file_progress()
                        set_transport_status("")
                        print(f"Transfer aborted: {exc}")
                        sys.exit(-1)
                    finally:
                        end_transport_file_progress()

                    step_progress.update(step_task_id, advance=1)
                    step_progress.stop_task(step_task_id)
                    step_progress.update(step_task_id, visible=False)
                    system_steps_progress.update(system_steps_task_id, advance=1)

                if dry_run:
                    time.sleep(0.2)
                system_steps_progress.update(system_steps_task_id, visible=False)
                current_system_progress.stop_task(current_task_id)
                current_system_progress.update(
                    current_task_id, description=f"[bold green]{name} synced!"
                )
                overall_progress.update(overall_task_id, advance=1)

            overall_progress.update(
                overall_task_id,
                description=f"[bold green]{len(systems)} systems processed, done!",
            )
        transport_status_progress.update(transport_status_task_id, visible=False)
        transport_file_progress.update(transport_file_task_id, visible=False)


if __name__ == "__main__":
    main()
