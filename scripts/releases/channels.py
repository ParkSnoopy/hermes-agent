"""Authenticated channel administration; mutable records use ETag CAS, never CDN state."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import http.client


from hermes_cli.release_channels import (
    ChannelError, ChannelReader, artifact_key,
    canonical_json, channel_key, decode_json, public_base as validate_public_base,
    validate_record, validate_repository,
)
from scripts.releases import r2



class ChannelConflict(ChannelError):
    pass


class PublicVisibilityError(ChannelError):
    """The authenticated write committed; do not roll it back on a stale CDN read."""


class R2ChannelStore:
    def __init__(self, creds: dict, base: str, bucket: str, *, scope=None):
        from scripts.releases.r2_scope import R2Scope
        self.creds, self.base, self.bucket = creds, base.rstrip("/"), bucket
        self.scope = R2Scope.configured() if scope is None else scope

    def get(self, key: str) -> tuple[bytes, str] | None:
        url = self.scope.object_url(self.base, self.bucket, artifact_key(key))
        try:
            response = r2.signed_request("GET", url, creds=self.creds, now=r2.amz_timestamp())
        except r2.R2RequestError as exc:
            if exc.status == 404:
                return None
            raise
        etag = response.header("etag")
        if not etag:
            raise ChannelError("Authenticated channel read missing ETag")
        body = response._body
        if len(body) > 4 * 1024 * 1024:
            raise ChannelError("Channel metadata exceeds size limit")
        return body, etag

    def put(self, key: str, body: bytes, etag: str | None = None) -> None:
        url = self.scope.object_url(self.base, self.bucket, artifact_key(key))
        condition = {"If-Match": etag} if etag is not None else {"If-None-Match": "*"}
        condition["Cache-Control"] = r2.cache_control_for(key) or "no-store"
        try:
            r2.signed_request("PUT", url, body=body, body_hash=hashlib.sha256(body).hexdigest(),
                              content_length=len(body), content_type="application/json",
                              creds=self.creds, now=r2.amz_timestamp(), extra_headers=condition, tries=1)
        except (r2.R2RequestError, OSError, http.client.HTTPException) as exc:
            # Lost success responses and conditional retries are settled by exact bytes.
            current = self.get(key)
            if current is not None and current[0] == body:
                from scripts.releases.upload_summary import note
                note(self.scope.key(key))
                return
            if isinstance(exc, r2.R2RequestError) and exc.status == 412:
                raise ChannelConflict(f"Channel write conflict: {key}") from exc
            raise ChannelError(f"Channel write outcome uncertain: {key}; inspect before retry") from exc
        current = self.get(key)
        if current is None or current[0] != body:
            raise ChannelConflict(f"Channel changed before authenticated readback: {key}")
        from scripts.releases.upload_summary import note
        note(self.scope.key(key))

    def keys(self, prefix: str) -> list[str]:
        keys, seen = [], set()
        prefix = self.scope.listing_prefix(prefix)
        token = None
        while True:
            params = {"list-type": "2", "max-keys": "1000", "prefix": prefix}
            if token is not None:
                params["continuation-token"] = token
            url = f"{self.scope.bucket_url(self.base, self.bucket)}?{r2.canonical_query(params)}"
            response = r2.signed_request("GET", url, creds=self.creds, now=r2.amz_timestamp())
            page = r2.parse_list_xml(response.text())
            keys.extend(self.scope.logical_key(key) for key in page["keys"])
            if not page["truncated"]:
                return keys
            token = page["nextToken"]
            if not token or token in seen:
                raise ChannelError("Incomplete or repeated channel pagination token")
            seen.add(token)


class ChannelPublisher:
    """Authorize source branch records without native identity or build allocation."""

    def __init__(self, store: R2ChannelStore, repository: str, public_base: str,
                 authorize):
        self.store = store
        self.repository = validate_repository(repository)
        self.public_base = validate_public_base(public_base)
        self.reader = ChannelReader(self.public_base, self.repository)
        self.authorize = authorize



    def _read(self, name: str) -> tuple[dict, str] | None:
        value = self.store.get(channel_key(name))
        if value is None:
            return None
        body, etag = value
        return validate_record(decode_json(body), name=name, repository=self.repository), etag

    def _write(self, key: str, value: dict, etag: str | None = None) -> None:
        body = canonical_json(value)
        self.store.put(key, body, etag)
        try:
            visible = self.reader.read_bytes(key)
        except ChannelError as exc:
            raise PublicVisibilityError(f"Committed {key}; public visibility verification failed") from exc
        if visible != body:
            current = self.store.get(key)
            if current is not None and current[0] != body:
                # A real subsequent CAS is not a stale CDN response.
                raise ChannelConflict(f"Committed {key}; superseded before public readback")
            raise PublicVisibilityError(f"Committed {key}; public visibility verification failed")


    def list(self) -> list[dict]:
        result = []
        for key in self.store.keys("releases/channels/"):
            name = key.removeprefix("releases/channels/").removesuffix(".json")
            if channel_key(name) != key:
                raise ChannelError("Invalid object in channel namespace")
            current = self._read(name)
            if current is None:
                raise ChannelError("Listed channel disappeared")
            result.append(current[0])
        return result

    def bootstrap(self, record: dict, *, publish: bool = False) -> dict:
        record = deepcopy(validate_record(record, repository=self.repository))
        if record["policy"] != "source-branch" or record["state"] != "active":
            raise ChannelError("Bootstrap requires an active source branch")
        self.authorize("bootstrap", record)
        if publish:
            self._write(channel_key(record["name"]), record)
        return record
