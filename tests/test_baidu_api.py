import hashlib
import json
import urllib.parse
import urllib.request

import pytest

from vajsave.baidu_api import (
    BaiduCredentialStore,
    BaiduCredentials,
    BaiduNetdiskClient,
    BaiduSyncError,
    DeviceTokenPoll,
)
from vajsave.jobs import JobCancelled


class _Backend:
    priority = 1


class _MemoryKeyring:
    def __init__(self):
        self.passwords = {}

    def get_keyring(self):
        return _Backend()

    def get_password(self, service, username):
        return self.passwords.get((service, username))

    def set_password(self, service, username, password):
        self.passwords[(service, username)] = password

    def delete_password(self, service, username):
        del self.passwords[(service, username)]


class _Response:
    def __init__(self, data):
        self.data = json.dumps(data).encode("utf-8")

    def read(self, limit):
        return self.data[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _QueueOpener:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout):
        assert timeout > 0
        self.requests.append(request)
        return _Response(self.responses.pop(0))


def test_credential_store_uses_injected_secure_keyring_and_clock():
    keyring = _MemoryKeyring()
    store = BaiduCredentialStore(keyring, clock=lambda: 1000)

    store.save_app("app-key", "secret-key", "vaj-save")
    saved = store.save_tokens(
        access_token="access", refresh_token="refresh", expires_in=60
    )

    assert saved.access_token_expires_at == 1060
    assert store.load() == saved
    assert len(keyring.passwords) == 1
    assert "secret-key" in next(iter(keyring.passwords.values()))
    store.clear()
    assert store.load() is None


def test_device_authorization_polls_pending_then_saves_tokens():
    keyring = _MemoryKeyring()
    store = BaiduCredentialStore(keyring, clock=lambda: 100)
    store.save_app("app-key", "secret-key", "vaj-save")
    opener = _QueueOpener(
        {
            "device_code": "device-code",
            "user_code": "ABCD-EFGH",
            "verification_url": "https://openapi.baidu.com/device",
            "expires_in": 600,
            "interval": 5,
        },
        {"error": "authorization_pending"},
        {"access_token": "access", "refresh_token": "refresh", "expires_in": 300},
    )
    client = BaiduNetdiskClient(store, opener=opener)

    device = client.request_device_code("app-key")
    pending = client.poll_device_token(device.device_code)
    authorized = client.poll_device_token(device.device_code)

    assert device.user_code == "ABCD-EFGH"
    assert pending == DeviceTokenPoll("pending")
    assert authorized.status == "authorized"
    assert store.load().access_token_expires_at == 400
    assert urllib.parse.urlparse(opener.requests[0].full_url).path.endswith("/device/code")
    assert "client_secret=secret-key" in opener.requests[2].full_url


def test_upload_streams_chunks_and_honors_cancellation(tmp_path):
    keyring = _MemoryKeyring()
    store = BaiduCredentialStore(keyring, clock=lambda: 1)
    store.save_app("app-key", "secret", "vaj-save")
    store.save_tokens(access_token="access", refresh_token="refresh", expires_in=600)
    payload = b"abcdefg"
    source = tmp_path / "snapshot.zip"
    source.write_bytes(payload)
    chunks = [payload[:3], payload[3:6], payload[6:]]
    opener = _QueueOpener(
        {"errno": 0, "uploadid": "upload-id", "block_list": [0, 1, 2]},
        *({"md5": hashlib.md5(chunk).hexdigest()} for chunk in chunks),
        {"errno": 0},
    )
    client = BaiduNetdiskClient(store, opener=opener, clock=lambda: 2, chunk_size=3)
    progress = []

    result = client.upload_file(
        source,
        "/apps/vaj-save/snapshot.zip",
        chunk_progress=lambda done, total: progress.append((done, total)),
    )

    assert result.size == len(payload)
    assert progress == [(1, 3), (2, 3), (3, 3)]
    assert len(opener.requests) == 5
    assert b"abcdefg" not in b"".join(request.data or b"" for request in opener.requests)
    assert all(isinstance(request, urllib.request.Request) for request in opener.requests)

    cancelled = []

    def cancel_now():
        cancelled.append(True)
        raise JobCancelled()

    with pytest.raises(JobCancelled):
        client.upload_file(source, "/apps/vaj-save/cancel.zip", cancel_check=cancel_now)
    assert cancelled


def test_api_errors_do_not_include_secret_or_request_url():
    keyring = _MemoryKeyring()
    store = BaiduCredentialStore(keyring, clock=lambda: 1)
    store.save_app("app-key", "secret-value", "vaj-save")
    store.save_tokens(access_token="access", refresh_token="refresh", expires_in=600)
    opener = _QueueOpener({"errno": -10, "request_id": "request-123"})
    client = BaiduNetdiskClient(store, opener=opener, clock=lambda: 2)

    with pytest.raises(BaiduSyncError) as caught:
        client.list_app_files("vaj-save")

    message = str(caught.value)
    assert "errno=-10" in message
    assert "request-123" in message
    assert "secret-value" not in message
    assert "access_token" not in message
