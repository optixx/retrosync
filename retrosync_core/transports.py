import base64
import concurrent.futures
import fnmatch
import logging
import platform
import select
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import paramiko

from .paths import normalize_webdav_remote_path

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


class TransportError(Exception):
    pass


@dataclass(frozen=True)
class TransportCapabilities:
    per_file_callback: bool = False
    preserves_mtime: bool = False
    size_aware_skip: bool = False
    atomic_upload: bool = False
    parallel_upload: bool = False
    server_side_mkdir_cacheable: bool = False


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
            raise TransportError(
                "This script only runs on macOS, Linux or Windows "
                f"but you're using {current_platform}."
            )


class TransportBase:
    capabilities = TransportCapabilities()

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
            if not filename.is_file():
                continue
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

    def guess_total_size(self, src_path: Path, whitelist: list, recursive=False):
        total = 0
        if recursive:
            generator = src_path.rglob("*")
        else:
            generator = src_path.glob("*")
        for filename in generator:
            if not filename.is_file():
                continue
            if self.is_excluded_path(filename.relative_to(src_path)):
                continue
            if whitelist and filename.suffix not in whitelist:
                continue
            try:
                total += filename.stat().st_size
            except OSError:
                continue

        return total


class TransportUnixBase(TransportBase):
    capabilities = TransportCapabilities(
        per_file_callback=False,
        preserves_mtime=True,
        size_aware_skip=True,
        atomic_upload=False,
        parallel_upload=False,
        server_side_mkdir_cacheable=False,
    )

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
            raise TransportError(f"Executable '{executable_name}' not found.")

    def check(self):
        pass

    def ensure_dir_exists(self, path_directory: Path):
        pass

    def command_prefix(self):
        return ""

    def build_dest(self, path: Path):
        return f'"{path}"'

    def execute(self, cmd, cancel_check=None):
        logger.debug(f"execute: cmd={cmd}")
        if self.dry_run:
            return
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
        poll = select.poll()
        if p.stdout:
            poll.register(p.stdout, select.POLLIN | select.POLLHUP)  # pyright: ignore
        if p.stderr:
            poll.register(p.stderr, select.POLLIN | select.POLLHUP)  # pyright: ignore
        while p.poll() is None:
            if cancel_check and cancel_check():
                logger.debug("execute: cancellation requested, terminating process")
                p.terminate()
                try:
                    p.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    p.kill()
                raise TransportError("Transfer interrupted by user.")

            events = poll.poll(200)
            for event in events:
                (rfd, event) = event
                if event & select.POLLIN:
                    if p.stdout and rfd == p.stdout.fileno():  # pyright: ignore
                        line = p.stdout.readline()  # pyright: ignore
                        logger.debug("execute: stdout=%s", line)
                    if p.stderr and rfd == p.stderr.fileno():  # pyright: ignore
                        line = p.stderr.readline()  # pyright: ignore
                        logger.debug("execute: stderr=%s", line)
                if event & select.POLLHUP:
                    poll.unregister(rfd)
        p.wait()

    def copy_files(
        self,
        src_path: Path,
        dest_path: Path,
        whitelist: list,
        recursive: bool = False,
        callback=None,
        cancel_check=None,
    ):
        self.ensure_dir_exists(dest_path)
        args = "--outbuf=L --progress --verbose --human-readable --recursive --size-only --delete "
        for item in GLOBAL_EXCLUDE_PATTERNS:
            args += f'--exclude="{item}" '
        if whitelist:
            args += '--include="*/" '
            for item in whitelist:
                args += f'--include="*{item}" '
            args += '--exclude="*" '
        cmd = f'{self.command_prefix()} rsync {args} "{src_path}/" {self.build_dest(dest_path)}'
        self.execute(cmd, cancel_check=cancel_check)


class TransportFileSystemUnix(TransportUnixBase):
    def check(self):
        for command in ["rsync"]:
            self.check_executable_exists(command)

    def ensure_dir_exists(self, path_directory: Path):
        if not self.dry_run:
            if not path_directory.is_dir():
                path_directory.mkdir(parents=True)

    def copy_file(self, src_filename: Path, dest_filename: Path, cancel_check=None):
        self.ensure_dir_exists(dest_filename.parent)
        if cancel_check and cancel_check():
            raise TransportError("Transfer interrupted by user.")
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

    def copy_file(self, src_filename: Path, dest_filename: Path, cancel_check=None):
        if cancel_check and cancel_check():
            raise TransportError("Transfer interrupted by user.")
        cmd = f'{self.command_prefix()} scp "{src_filename}" {self.build_dest(dest_filename)}'
        self.execute(cmd, cancel_check=cancel_check)

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
    capabilities = TransportCapabilities(
        per_file_callback=True,
        preserves_mtime=False,
        size_aware_skip=False,
        atomic_upload=False,
        parallel_upload=True,
        server_side_mkdir_cacheable=True,
    )

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
            raise TransportError("WebDAV transport requires [webdav].host in the config.")

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

    def _remote_path(self, path_value: Path):
        return normalize_webdav_remote_path(path_value)

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
        def rewind_body():
            if body is None or not hasattr(body, "seek"):
                return
            try:
                body.seek(0)
            except (OSError, ValueError):
                pass

        try:
            rewind_body()
            self._request_once(method, path, body=body, headers=headers, ok_codes=ok_codes)
            return
        except urllib.error.HTTPError as exc:
            if exc.code in ok_codes:
                return
            if exc.code == 401 and self._auth_header:
                retry_headers = dict(headers or {})
                retry_headers["Authorization"] = self._auth_header
                logger.debug(
                    "TransportWebDAV::_request: 401 for %s %s, retrying with preemptive Basic auth",
                    method,
                    path,
                )
                try:
                    rewind_body()
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
            self._request("PROPFIND", path, headers=headers, ok_codes=(200, 207, 301))
            return True
        except RuntimeError as exc:
            if "HTTP 404" in str(exc):
                return False
            raise

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

    def copy_file(
        self, src_filename: Path, dest_filename: Path, *, ensure_parent=True, cancel_check=None
    ):
        if self.dry_run:
            logger.debug(
                "TransportWebDAV::copy_file: dry-run %s -> %s", src_filename, dest_filename
            )
            return
        if cancel_check and cancel_check():
            raise TransportError("Transfer interrupted by user.")
        if ensure_parent:
            self.ensure_dir_exists(dest_filename.parent)
        file_size = src_filename.stat().st_size
        remote = self._remote_path(dest_filename)
        logger.debug(
            "TransportWebDAV::copy_file: upload start src=%s dest=%s bytes=%s",
            src_filename,
            remote,
            file_size,
        )
        started = time.monotonic()
        with open(src_filename, "rb") as fd:
            if cancel_check and cancel_check():
                raise TransportError("Transfer interrupted by user.")
            self._request(
                "PUT", remote, body=fd, headers={"Content-Type": "application/octet-stream"}
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
        cancel_check=None,
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
            if cancel_check and cancel_check():
                raise TransportError("Transfer interrupted by user.")
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

        unique_parents = sorted({dest.parent for _, dest in files}, key=lambda p: len(p.parts))
        for parent in unique_parents:
            if cancel_check and cancel_check():
                raise TransportError("Transfer interrupted by user.")
            self.ensure_dir_exists(parent)

        if self.max_workers <= 1 or total == 1:
            for idx, (src_filename, dest_filename) in enumerate(files, start=1):
                if cancel_check and cancel_check():
                    raise TransportError("Transfer interrupted by user.")
                logger.debug(
                    "TransportWebDAV::copy_files: [%s/%s] %s -> %s",
                    idx,
                    total,
                    src_filename,
                    dest_filename,
                )
                self.copy_file(
                    src_filename,
                    dest_filename,
                    ensure_parent=False,
                    cancel_check=cancel_check,
                )
                if callback:
                    callback()
            return

        logger.debug(
            "TransportWebDAV::copy_files: parallel upload workers=%s files=%s",
            self.max_workers,
            total,
        )

        def upload_one(src_filename, dest_filename):
            if cancel_check and cancel_check():
                raise TransportError("Transfer interrupted by user.")
            self.copy_file(
                src_filename, dest_filename, ensure_parent=False, cancel_check=cancel_check
            )

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers)
        interrupted = False
        future_to_index = {}
        try:
            for idx, (src_filename, dest_filename) in enumerate(files, start=1):
                if cancel_check and cancel_check():
                    raise TransportError("Transfer interrupted by user.")
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
                if cancel_check and cancel_check():
                    raise TransportError("Transfer interrupted by user.")
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
    capabilities = TransportCapabilities(
        per_file_callback=True,
        preserves_mtime=True,
        size_aware_skip=False,
        atomic_upload=True,
        parallel_upload=False,
        server_side_mkdir_cacheable=False,
    )

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

    def copy_file(self, src_filename: Path, dest_filename: Path, cancel_check=None):
        if cancel_check and cancel_check():
            raise TransportError("Transfer interrupted by user.")
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
        cancel_check=None,
    ):
        guessed_len = self.guess_file_count(src_path, whitelist, recursive)
        logger.debug(f"TransportFileSystemWindows::copy_files: {src_path} -> {dest_path}")
        self.ensure_dir_exists(dest_path)
        cnt = 1
        for item in src_path.iterdir():
            if cancel_check and cancel_check():
                raise TransportError("Transfer interrupted by user.")
            logger.debug(
                f"TransportFileSystemWindows::copy_files: [{cnt}/{guessed_len}] {item.name}"
            )
            cnt += 1
            s = item
            if self.is_excluded_path(s.relative_to(src_path)):
                logger.debug(f"TransportFileSystemWindows::copy_files: excluded {s}")
                continue
            d = dest_path / item.name
            if s.is_dir():
                if recursive:
                    self.copy_files(s, d, whitelist, recursive, callback, cancel_check=cancel_check)
                continue
            else:
                if whitelist and s.suffix not in whitelist:
                    logger.debug(f"TransportFileSystemWindows::copy_files: not whitelist match {s}")
                    continue
                if callback:
                    callback()
                if not self.dry_run:
                    shutil.copy2(s, d)


class TransportSSHWindows(TransportWindowsBase):
    capabilities = TransportCapabilities(
        per_file_callback=True,
        preserves_mtime=False,
        size_aware_skip=True,
        atomic_upload=False,
        parallel_upload=False,
        server_side_mkdir_cacheable=False,
    )

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

    def copy_file(self, src_filename: Path, dest_filename: Path, cancel_check=None):
        if cancel_check and cancel_check():
            raise TransportError("Transfer interrupted by user.")
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
        cancel_check=None,
    ):
        guessed_len = self.guess_file_count(src_path, whitelist, recursive)
        logger.debug(f"TransportSSHWindows::copy_files: {src_path} -> {dest_path}")
        self.connect()
        self.ensure_dir_exists(dest_path)
        cnt = 1

        for _, src_filename in enumerate(src_path.iterdir()):
            if cancel_check and cancel_check():
                raise TransportError("Transfer interrupted by user.")
            logger.debug(
                f"TransportSSHWindows::copy_files: [{cnt}/{guessed_len}] {src_filename.name}"
            )
            if self.is_excluded_path(src_filename.relative_to(src_path)):
                logger.debug(f"TransportSSHWindows::copy_files: excluded {src_filename}")
                continue
            dest_filename = dest_path / src_filename.name

            if src_filename.is_dir():
                if recursive:
                    self.copy_files(
                        src_filename,
                        dest_path / src_filename.name,
                        whitelist,
                        recursive,
                        callback,
                        cancel_check=cancel_check,
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
                if callback:
                    callback()
