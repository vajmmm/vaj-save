"""百度网盘授权与文件 API。此模块不依赖 AppState 或 Qt。"""

from __future__ import annotations

import hashlib
import json
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Callable, Optional


OAUTH_BASE = "https://openapi.baidu.com"
API_BASE = "https://pan.baidu.com"
PCS_BASE = "https://d.pcs.baidu.com"
KEYRING_SERVICE = "vaj-save-baidu-netdisk"
KEYRING_USERNAME = "default"
UPLOAD_CHUNK_SIZE = 4 * 1024 * 1024
MAX_UPLOAD_PARTS = 10_000
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
REQUEST_TIMEOUT = 30
TOKEN_EXPIRED_ERRNOS = {-6, 110, 111, 20016, 20017, 31045}


class BaiduSyncError(RuntimeError):
    """百度网盘功能的可展示错误，不包含请求 URL 或认证信息。"""


class BaiduApiError(BaiduSyncError):
    def __init__(self, errno: int, request_id: str = "") -> None:
        self.errno = errno
        self.request_id = request_id
        suffix = f"，请求编号 {request_id}" if request_id else ""
        super().__init__(f"百度网盘接口错误 errno={errno}{suffix}。")


@dataclass(frozen=True)
class BaiduCredentials:
    app_key: str
    app_secret: str
    app_name: str
    access_token: str = ""
    refresh_token: str = ""
    access_token_expires_at: float = 0

    @property
    def connected(self) -> bool:
        return bool(self.refresh_token)


@dataclass(frozen=True)
class DeviceAuthorization:
    device_code: str
    user_code: str
    verification_url: str
    qrcode_url: str
    expires_in: int
    interval: int


@dataclass(frozen=True)
class DeviceTokenPoll:
    status: str
    interval_increased: bool = False


@dataclass(frozen=True)
class UploadResult:
    path: str
    size: int


class BaiduCredentialStore:
    """把百度应用密钥和 OAuth 令牌保存在操作系统凭据库中。"""

    def __init__(
        self, keyring_api: Any = None, *, clock: Callable[[], float] = time.time
    ) -> None:
        self._keyring_api = keyring_api
        self._clock = clock

    def _keyring(self):
        api = self._keyring_api
        if api is None:
            try:
                import keyring as api  # type: ignore[no-redef]
            except ImportError as exc:
                raise BaiduSyncError("系统凭据库组件不可用，无法安全保存百度网盘授权。") from exc
        try:
            backend = api.get_keyring()
            if getattr(backend, "priority", 1) <= 0:
                raise BaiduSyncError("系统凭据库不可用，未保存百度网盘密钥。")
        except BaiduSyncError:
            raise
        except Exception as exc:  # noqa: BLE001 - keyring backend errors vary by OS
            raise BaiduSyncError("无法访问系统凭据库，未保存百度网盘密钥。") from exc
        return api

    def load(self) -> Optional[BaiduCredentials]:
        api = self._keyring()
        try:
            raw = api.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
        except Exception as exc:  # noqa: BLE001 - keyring backend errors vary by OS
            raise BaiduSyncError("无法读取系统凭据库中的百度网盘授权。") from exc
        if raw is None:
            return None
        try:
            data = json.loads(raw)
            credentials = BaiduCredentials(
                app_key=str(data["app_key"]),
                app_secret=str(data["app_secret"]),
                app_name=str(data["app_name"]),
                access_token=str(data.get("access_token") or ""),
                refresh_token=str(data.get("refresh_token") or ""),
                access_token_expires_at=float(data.get("access_token_expires_at") or 0),
            )
            validate_app_credentials(
                credentials.app_key, credentials.app_secret, credentials.app_name
            )
            return credentials
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise BaiduSyncError("系统凭据库中的百度网盘配置无法读取，请重新连接。") from exc

    def save_app(self, app_key: str, app_secret: str, app_name: str) -> BaiduCredentials:
        app_key, app_secret, app_name = validate_app_credentials(
            app_key, app_secret, app_name
        )
        previous = self.load()
        if previous and (
            previous.app_key,
            previous.app_secret,
            previous.app_name,
        ) == (app_key, app_secret, app_name):
            credentials = previous
        else:
            credentials = BaiduCredentials(app_key, app_secret, app_name)
        self._save(credentials)
        return credentials

    def save_tokens(
        self, *, access_token: str, refresh_token: str, expires_in: int
    ) -> BaiduCredentials:
        credentials = self.load()
        if credentials is None:
            raise BaiduSyncError("请先填写百度开放平台应用信息。")
        if not access_token or not refresh_token or expires_in <= 0:
            raise BaiduSyncError("百度网盘未返回完整授权信息，请重新连接。")
        updated = replace(
            credentials,
            access_token=access_token,
            refresh_token=refresh_token,
            access_token_expires_at=self._clock() + expires_in,
        )
        self._save(updated)
        return updated

    def clear(self) -> None:
        api = self._keyring()
        try:
            if api.get_password(KEYRING_SERVICE, KEYRING_USERNAME) is None:
                return
            api.delete_password(KEYRING_SERVICE, KEYRING_USERNAME)
        except Exception as exc:  # noqa: BLE001 - keyring backend errors vary by OS
            raise BaiduSyncError("无法清除系统凭据库中的百度网盘授权。") from exc

    def _save(self, credentials: BaiduCredentials) -> None:
        api = self._keyring()
        try:
            api.set_password(
                KEYRING_SERVICE,
                KEYRING_USERNAME,
                json.dumps(asdict(credentials), ensure_ascii=False),
            )
        except Exception as exc:  # noqa: BLE001 - keyring backend errors vary by OS
            raise BaiduSyncError("无法写入系统凭据库，未保存百度网盘密钥。") from exc


def validate_app_credentials(
    app_key: str, app_secret: str, app_name: str
) -> tuple[str, str, str]:
    key = str(app_key or "").strip()
    secret = str(app_secret or "").strip()
    name = str(app_name or "").strip()
    if not key or not secret or not name:
        raise BaiduSyncError("请填写百度开放平台的 App Key、Secret Key 和应用名称。")
    if len(key) > 256 or len(secret) > 512 or len(name) > 128:
        raise BaiduSyncError("百度开放平台应用信息超出长度限制。")
    if any(unicodedata.category(ch) == "Cc" for ch in key + secret):
        raise BaiduSyncError("百度开放平台密钥不能包含控制字符。")
    if name in {".", ".."} or any(ch in name for ch in ("/", "\\")) or any(
        unicodedata.category(ch) == "Cc" for ch in name
    ):
        raise BaiduSyncError("百度应用名称不能包含路径分隔符或控制字符。")
    return key, secret, name


class BaiduNetdiskClient:
    """百度网盘 OAuth、目录清单和分片上传的薄 HTTP 客户端。"""

    def __init__(
        self,
        credentials: BaiduCredentialStore,
        *,
        opener: Optional[Callable[..., Any]] = None,
        clock: Callable[[], float] = time.time,
        timeout: int = REQUEST_TIMEOUT,
        chunk_size: int = UPLOAD_CHUNK_SIZE,
    ) -> None:
        self.credentials = credentials
        self._opener = opener or urllib.request.urlopen
        self._clock = clock
        self._timeout = timeout
        self._chunk_size = chunk_size

    def request_device_code(self, app_key: str) -> DeviceAuthorization:
        key = str(app_key or "").strip()
        if not key:
            raise BaiduSyncError("请填写百度开放平台 App Key。")
        data = self._request_json(
            f"{OAUTH_BASE}/oauth/2.0/device/code",
            query={
                "response_type": "device_code",
                "client_id": key,
                "scope": "basic,netdisk",
                "openapi": "xpansdk",
            },
        )
        self._raise_oauth_error(data)
        authorization = DeviceAuthorization(
            device_code=str(data.get("device_code") or ""),
            user_code=str(data.get("user_code") or ""),
            verification_url=str(
                data.get("verification_url") or data.get("verification_uri") or ""
            ),
            qrcode_url=str(data.get("qrcode_url") or ""),
            expires_in=_positive_int(data.get("expires_in"), 600),
            interval=_positive_int(data.get("interval"), 5),
        )
        if not authorization.device_code or not authorization.user_code:
            raise BaiduSyncError("百度网盘没有返回有效的设备授权码。")
        if not _is_baidu_https_url(authorization.verification_url):
            raise BaiduSyncError("百度网盘返回了无效的授权地址。")
        return authorization

    def poll_device_token(self, device_code: str) -> DeviceTokenPoll:
        credentials = self.credentials.load()
        if credentials is None:
            raise BaiduSyncError("请先填写百度开放平台应用信息。")
        data = self._request_json(
            f"{OAUTH_BASE}/oauth/2.0/token",
            query={
                "grant_type": "device_token",
                "code": device_code,
                "client_id": credentials.app_key,
                "client_secret": credentials.app_secret,
            },
        )
        error = str(data.get("error") or "")
        if error == "authorization_pending":
            return DeviceTokenPoll("pending")
        if error == "slow_down":
            return DeviceTokenPoll("pending", interval_increased=True)
        if error:
            raise BaiduSyncError(_oauth_error_text(error))
        try:
            self.credentials.save_tokens(
                access_token=str(data.get("access_token") or ""),
                refresh_token=str(data.get("refresh_token") or ""),
                expires_in=int(data.get("expires_in") or 0),
            )
        except (TypeError, ValueError) as exc:
            raise BaiduSyncError("百度网盘返回的授权信息无效，请重新连接。") from exc
        return DeviceTokenPoll("authorized")

    def list_app_files(self, app_name: str) -> dict[str, dict[str, Any]]:
        _, _, name = validate_app_credentials("key", "secret", app_name)
        remote_dir = f"/apps/{name}"
        start = 0
        page_size = 1000
        files: dict[str, dict[str, Any]] = {}
        while True:
            try:
                data = self._api_request(
                    "/rest/2.0/xpan/file",
                    query={
                        "method": "list",
                        "dir": remote_dir,
                        "start": start,
                        "limit": page_size,
                        "order": "name",
                        "desc": 0,
                    },
                )
            except BaiduApiError as exc:
                if exc.errno == -9:
                    return files
                raise
            listing = data.get("list")
            if listing is None:
                listing = []
            if not isinstance(listing, list):
                raise BaiduSyncError("百度网盘返回了无法读取的文件清单。")
            for item in listing:
                if not isinstance(item, dict):
                    continue
                filename = str(item.get("server_filename") or "")
                if filename:
                    files[filename] = item
            if len(listing) < page_size:
                break
            start += len(listing)
        return files

    def upload_file(
        self,
        local_path: Path,
        remote_path: str,
        *,
        cancel_check: Optional[Callable[[], None]] = None,
        chunk_progress: Optional[Callable[[int, int], None]] = None,
    ) -> UploadResult:
        source = Path(local_path)
        try:
            size = source.stat().st_size
        except OSError as exc:
            raise BaiduSyncError(f"待同步文件无法读取: {source.name}") from exc
        if size <= 0:
            raise BaiduSyncError(f"百度网盘不接受空文件: {source.name}")
        count = (size + self._chunk_size - 1) // self._chunk_size
        if count > MAX_UPLOAD_PARTS:
            raise BaiduSyncError(f"文件分片数超过百度网盘限制: {source.name}")

        block_md5s = self._hash_blocks(source, count, cancel_check)
        precreated = self._api_request(
            "/rest/2.0/xpan/file",
            query={"method": "precreate"},
            form={
                "path": remote_path,
                "size": size,
                "isdir": 0,
                "autoinit": 1,
                "rtype": 0,
                "block_list": json.dumps(block_md5s, separators=(",", ":")),
            },
        )
        if _positive_int(precreated.get("return_type"), 0) == 2:
            return UploadResult(remote_path, size)
        upload_id = str(precreated.get("uploadid") or "")
        if not upload_id:
            raise BaiduSyncError("百度网盘预上传没有返回上传编号。")
        missing = precreated.get("block_list")
        if missing is None:
            missing = list(range(count))
        if not isinstance(missing, list):
            raise BaiduSyncError("百度网盘返回了无效的分片清单。")
        try:
            missing_parts = sorted({int(index) for index in missing})
        except (TypeError, ValueError) as exc:
            raise BaiduSyncError("百度网盘返回了无效的分片序号。") from exc
        if any(index < 0 or index >= count for index in missing_parts):
            raise BaiduSyncError("百度网盘返回了越界的分片序号。")

        for completed, part_index in enumerate(missing_parts, 1):
            if cancel_check is not None:
                cancel_check()
            offset = part_index * self._chunk_size
            length = min(self._chunk_size, size - offset)
            with source.open("rb") as handle:
                handle.seek(offset)
                chunk = handle.read(length)
            if len(chunk) != length:
                raise BaiduSyncError(f"待同步文件在上传中发生变化: {source.name}")
            returned_md5 = self._upload_part(
                remote_path, upload_id, part_index, chunk
            )
            if returned_md5 and returned_md5.lower() != block_md5s[part_index]:
                raise BaiduSyncError(f"百度网盘分片校验失败: {source.name}")
            if chunk_progress is not None:
                chunk_progress(completed, len(missing_parts))

        if cancel_check is not None:
            cancel_check()
        self._api_request(
            "/rest/2.0/xpan/file",
            query={"method": "create"},
            form={
                "path": remote_path,
                "size": size,
                "isdir": 0,
                "uploadid": upload_id,
                "rtype": 0,
                "block_list": json.dumps(block_md5s, separators=(",", ":")),
            },
        )
        return UploadResult(remote_path, size)

    def _hash_blocks(
        self,
        source: Path,
        count: int,
        cancel_check: Optional[Callable[[], None]],
    ) -> list[str]:
        result: list[str] = []
        try:
            with source.open("rb") as handle:
                for _ in range(count):
                    if cancel_check is not None:
                        cancel_check()
                    block = handle.read(self._chunk_size)
                    if not block:
                        raise BaiduSyncError(f"待同步文件在校验中发生变化: {source.name}")
                    result.append(hashlib.md5(block).hexdigest())
                if handle.read(1):
                    raise BaiduSyncError(f"待同步文件在校验中发生变化: {source.name}")
        except OSError as exc:
            raise BaiduSyncError(f"待同步文件无法读取: {source.name}") from exc
        return result

    def _upload_part(
        self, remote_path: str, upload_id: str, part_index: int, chunk: bytes
    ) -> str:
        boundary, body = _multipart_file_body(chunk)
        data = self._authorized_request_json(
            f"{PCS_BASE}/rest/2.0/pcs/superfile2",
            query={
                "method": "upload",
                "type": "tmpfile",
                "path": remote_path,
                "uploadid": upload_id,
                "partseq": part_index,
            },
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        self._raise_api_error(data)
        return str(data.get("md5") or "")

    def _api_request(
        self,
        path: str,
        *,
        query: Optional[dict[str, Any]] = None,
        form: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        return self._authorized_request_json(
            f"{API_BASE}{path}", query=query or {}, form=form
        )

    def _authorized_request_json(
        self,
        url: str,
        *,
        query: dict[str, Any],
        form: Optional[dict[str, Any]] = None,
        data: Optional[bytes] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        for attempt in range(2):
            token = self._valid_access_token(force_refresh=bool(attempt))
            params = dict(query)
            params["access_token"] = token
            response = self._request_json(
                url,
                query=params,
                form=form,
                data=data,
                headers=headers,
            )
            errno = _as_int(response.get("errno"))
            if errno in TOKEN_EXPIRED_ERRNOS and attempt == 0:
                continue
            self._raise_api_error(response)
            return response
        raise BaiduSyncError("百度网盘授权已失效，请重新连接。")

    def _valid_access_token(self, *, force_refresh: bool = False) -> str:
        current = self.credentials.load()
        if current is None or not current.refresh_token:
            raise BaiduSyncError("请先在设置中连接百度网盘。")
        if (
            not force_refresh
            and current.access_token
            and current.access_token_expires_at > self._clock() + 60
        ):
            return current.access_token
        response = self._request_json(
            f"{OAUTH_BASE}/oauth/2.0/token",
            query={
                "grant_type": "refresh_token",
                "refresh_token": current.refresh_token,
                "client_id": current.app_key,
                "client_secret": current.app_secret,
            },
        )
        self._raise_oauth_error(response)
        try:
            updated = self.credentials.save_tokens(
                access_token=str(response.get("access_token") or ""),
                refresh_token=str(response.get("refresh_token") or current.refresh_token),
                expires_in=int(response.get("expires_in") or 0),
            )
        except (TypeError, ValueError) as exc:
            raise BaiduSyncError("百度网盘刷新令牌失败，请重新连接。") from exc
        return updated.access_token

    def _request_json(
        self,
        url: str,
        *,
        query: Optional[dict[str, Any]] = None,
        form: Optional[dict[str, Any]] = None,
        data: Optional[bytes] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        if query:
            url = f"{url}?{urllib.parse.urlencode(query, doseq=True)}"
        request_headers = {"Accept": "application/json", "User-Agent": "vaj-save"}
        if headers:
            request_headers.update(headers)
        request_data = data
        if form is not None:
            request_data = urllib.parse.urlencode(form).encode("utf-8")
            request_headers["Content-Type"] = "application/x-www-form-urlencoded"
        request = urllib.request.Request(url, data=request_data, headers=request_headers)
        try:
            response = self._opener(request, timeout=self._timeout)
            with response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            try:
                raw = exc.read(MAX_RESPONSE_BYTES + 1)
            finally:
                exc.close()
            parsed = _decode_json(raw)
            if parsed is not None:
                return parsed
            raise BaiduSyncError(f"百度网盘请求失败 (HTTP {exc.code})。") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise BaiduSyncError("无法连接百度网盘，请检查网络后重试。") from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise BaiduSyncError("百度网盘返回的数据超出大小限制。")
        parsed = _decode_json(raw)
        if parsed is None:
            raise BaiduSyncError("百度网盘返回了无法解析的数据。")
        return parsed

    @staticmethod
    def _raise_api_error(data: dict[str, Any]) -> None:
        BaiduNetdiskClient._raise_oauth_error(data)
        errno = _as_int(data.get("errno"))
        if errno not in (None, 0):
            request_id = str(data.get("request_id") or "").strip()
            raise BaiduApiError(errno, request_id)

    @staticmethod
    def _raise_oauth_error(data: dict[str, Any]) -> None:
        error = str(data.get("error") or "")
        if error:
            raise BaiduSyncError(_oauth_error_text(error))


def _decode_json(raw: bytes) -> Optional[dict[str, Any]]:
    if len(raw) > MAX_RESPONSE_BYTES:
        raise BaiduSyncError("百度网盘返回的数据超出大小限制。")
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _multipart_file_body(chunk: bytes) -> tuple[str, bytes]:
    boundary = "vajsave-" + hashlib.sha256(chunk[:128] + str(len(chunk)).encode()).hexdigest()[:24]
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="chunk.bin"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode("ascii")
    body += chunk + f"\r\n--{boundary}--\r\n".encode("ascii")
    return boundary, body


def _positive_int(value: Any, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def _as_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _oauth_error_text(error: str) -> str:
    labels = {
        "authorization_pending": "请先在浏览器中完成百度网盘授权。",
        "slow_down": "授权服务要求降低轮询频率，请稍后重试。",
        "expired_token": "百度网盘授权码已过期，请重新连接。",
        "access_denied": "百度网盘授权已被拒绝。",
        "invalid_grant": "百度网盘刷新令牌已失效，请重新连接。",
        "invalid_client": "百度开放平台 App Key 或 Secret Key 无效。",
    }
    return labels.get(error, "百度网盘授权失败，请检查应用配置后重试。")


def _is_baidu_https_url(value: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(value)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (host == "baidu.com" or host.endswith(".baidu.com"))


__all__ = [
    "BaiduApiError",
    "BaiduCredentialStore",
    "BaiduCredentials",
    "BaiduNetdiskClient",
    "BaiduSyncError",
    "DeviceAuthorization",
    "DeviceTokenPoll",
    "UploadResult",
    "validate_app_credentials",
]
