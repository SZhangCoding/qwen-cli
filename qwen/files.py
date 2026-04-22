"""File upload: getstsToken → OSS PUT (v4 sig) → parse → parse/status → optionally attach to project."""

from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import json
import mimetypes
import os
import time
import urllib.parse
import urllib.request

from .bridge import Bridge
from .auth import _ensure_origin


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _oss_v4_sign(
    access_key_id: str,
    access_key_secret: str,
    security_token: str,
    region: str,
    host: str,
    bucket: str,
    path: str,  # url-encoded path starting with "/"
    content_type: str,
) -> tuple[dict, str]:
    """Return (headers, iso_date). Sign a PUT with UNSIGNED-PAYLOAD per OSS v4."""
    now = _dt.datetime.now(_dt.timezone.utc)
    x_oss_date = now.strftime("%Y%m%dT%H%M%SZ")
    date = now.strftime("%Y%m%d")

    # Aliyun OSS v4 signed headers: content-type + all x-oss-* (no host)
    signed_headers_map = {
        "content-type": content_type,
        "x-oss-content-sha256": "UNSIGNED-PAYLOAD",
        "x-oss-date": x_oss_date,
        "x-oss-security-token": security_token,
    }
    signed_names = sorted(signed_headers_map.keys())
    canonical_headers = "".join(f"{k}:{signed_headers_map[k]}\n" for k in signed_names)
    additional_headers = ""  # no extra additional headers
    # virtual-hosted style: canonical URI includes the bucket name
    canonical_uri = f"/{bucket}{path}"
    canonical_request = "\n".join([
        "PUT",
        canonical_uri,
        "",  # canonical query string (path-only URL, no query)
        canonical_headers,
        additional_headers,
        "UNSIGNED-PAYLOAD",
    ])

    scope = f"{date}/{region}/oss/aliyun_v4_request"
    string_to_sign = "\n".join([
        "OSS4-HMAC-SHA256",
        x_oss_date,
        scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])

    signing_key = _hmac(("aliyun_v4" + access_key_secret).encode("utf-8"), date)
    signing_key = _hmac(signing_key, region)
    signing_key = _hmac(signing_key, "oss")
    signing_key = _hmac(signing_key, "aliyun_v4_request")
    signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    authorization = (
        f"OSS4-HMAC-SHA256 Credential={access_key_id}/{scope},"
        f"Signature={signature}"
    )
    headers = {
        "Authorization": authorization,
        "Content-Type": content_type,
        "x-oss-content-sha256": "UNSIGNED-PAYLOAD",
        "x-oss-date": x_oss_date,
        "x-oss-security-token": security_token,
    }
    return headers, x_oss_date


def _sts_token(bridge: Bridge, filename: str, filesize: int, filetype: str) -> dict:
    body = {"filename": filename, "filesize": filesize, "filetype": filetype}
    js = (
        "(async () => {"
        f"const r = await fetch('/api/v2/files/getstsToken', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:{json.dumps(json.dumps(body))}}});"
        "return await r.text();"
        "})()"
    )
    raw = bridge.evaluate(js)
    payload = json.loads(raw)
    if not payload.get("success"):
        raise RuntimeError(f"getstsToken failed: {payload}")
    return payload["data"]


def _put_to_oss(sts: dict, file_path: str, content_type: str) -> None:
    """Direct PUT from Python to Aliyun OSS using OSS v4 header signing.

    Bypasses the browser: we already have the STS credentials, and OSS auth is header-based.
    """
    with open(file_path, "rb") as f:
        body = f.read()

    parsed = urllib.parse.urlparse(sts["file_url"])
    host = parsed.netloc
    path = parsed.path  # already URL-encoded (e.g. %E6%... for Chinese)
    region = (sts.get("region") or "oss-ap-southeast-1").replace("oss-", "", 1)

    bucket = sts.get("bucketname") or host.split(".")[0]
    headers, _ = _oss_v4_sign(
        access_key_id=sts["access_key_id"],
        access_key_secret=sts["access_key_secret"],
        security_token=sts["security_token"],
        region=region,
        host=host,
        bucket=bucket,
        path=path,
        content_type=content_type,
    )

    url = f"https://{host}{path}"  # drop query-string signing params — header sig is canonical
    req = urllib.request.Request(url, data=body, method="PUT", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            if resp.status >= 300:
                raise RuntimeError(f"OSS PUT failed ({resp.status}): {resp.read()[:300]!r}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"OSS PUT failed ({e.code}): {e.read()[:2000].decode('utf-8','ignore')}")


def _parse(bridge: Bridge, file_id: str) -> None:
    js = (
        "(async () => {"
        f"const r = await fetch('/api/v2/files/parse', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:{json.dumps(json.dumps({'file_id': file_id}))}}});"
        "return await r.text();"
        "})()"
    )
    payload = json.loads(bridge.evaluate(js))
    if not payload.get("success"):
        raise RuntimeError(f"parse failed: {payload}")


def _parse_status(bridge: Bridge, file_ids: list[str]) -> list[dict]:
    body = {"file_id_list": file_ids}
    js = (
        "(async () => {"
        f"const r = await fetch('/api/v2/files/parse/status', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:{json.dumps(json.dumps(body))}}});"
        "return await r.text();"
        "})()"
    )
    payload = json.loads(bridge.evaluate(js))
    if not payload.get("success"):
        raise RuntimeError(f"parse/status failed: {payload}")
    return payload["data"]


def _wait_parsed(bridge: Bridge, file_id: str, timeout: float = 120.0, poll: float = 2.0) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        rows = _parse_status(bridge, [file_id])
        last = rows[0] if rows else {}
        status = last.get("status")
        if status == "success":
            return last
        if status == "failed":
            raise RuntimeError(f"file parse failed: {last}")
        time.sleep(poll)
    raise RuntimeError(f"file parse timeout (last={last})")


def _attach_to_project(bridge: Bridge, project_id: str, file_id: str) -> dict:
    body = {"files": [{"id": file_id, "type": "file"}]}
    js = (
        "(async () => {"
        f"const r = await fetch('/api/v2/projects/{project_id}/files', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:{json.dumps(json.dumps(body))}}});"
        "return await r.text();"
        "})()"
    )
    payload = json.loads(bridge.evaluate(js))
    if not payload.get("success"):
        raise RuntimeError(f"attach file to project failed: {payload}")
    return payload["data"]


def upload_file(
    bridge: Bridge,
    file_path: str,
    project_id: str | None = None,
    filetype: str | None = None,
    content_type: str = "application/octet-stream",
    wait: bool = True,
) -> dict:
    """Upload a local file to Qwen. Returns metadata incl. file_id and (if attached) project_id.

    Flow:
      1. getstsToken → pre-signed OSS URL + file_id
      2. PUT file bytes to OSS
      3. POST /files/parse to trigger server-side parsing
      4. Poll /files/parse/status until success (if wait=True)
      5. Optionally attach to a project
    """
    _ensure_origin(bridge)
    if not os.path.isfile(file_path):
        raise FileNotFoundError(file_path)
    filename = os.path.basename(file_path)
    filesize = os.path.getsize(file_path)
    if filetype is None:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "file"
        filetype = ext or "file"

    # let callers pass content_type='auto' to be inferred per-file
    if content_type == "auto":
        guessed, _ = mimetypes.guess_type(filename)
        content_type = guessed or "application/octet-stream"

    sts = _sts_token(bridge, filename, filesize, filetype)
    _put_to_oss(sts, file_path, content_type)
    _parse(bridge, sts["file_id"])

    parsed = None
    if wait:
        parsed = _wait_parsed(bridge, sts["file_id"])

    result = {
        "file_id": sts["file_id"],
        "filename": filename,
        "filesize": filesize,
        "filetype": filetype,
        "file_path": sts.get("file_path"),
        "parse": parsed,
    }
    if project_id:
        _attach_to_project(bridge, project_id, sts["file_id"])
        result["project_id"] = project_id
    return result


def delete_project_file(bridge: Bridge, project_id: str, file_id: str) -> dict:
    js = (
        "(async () => {"
        f"const r = await fetch('/api/v2/projects/{project_id}/files/{file_id}', {{method:'DELETE'}});"
        "return await r.text();"
        "})()"
    )
    payload = json.loads(bridge.evaluate(js))
    if not payload.get("success"):
        raise RuntimeError(f"delete project file failed: {payload}")
    return payload["data"]
