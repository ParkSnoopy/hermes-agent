"""Source-channel administration retains authorization and signed CAS boundaries."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from hermes_cli.release_channels import ChannelError, canonical_json, channel_key
from scripts.releases.channels import ChannelConflict, PublicVisibilityError
from tests.scripts.channel_helpers import ARCHIVED_IDENTITY, object_server, publisher, source_record


def test_source_channel_authorization_cas_and_public_readback():
    record = source_record()
    with object_server() as (url, objects, headers, _requests, faults):
        def deny(_action, _record):
            raise ValueError("not authorized")

        with pytest.raises(ValueError, match="not authorized"):
            publisher(url, authorize=deny).bootstrap(record, publish=True)
        assert not objects
        pub = publisher(url)
        with pytest.raises(ChannelError, match="cannot carry native artifacts"):
            pub.bootstrap({**record, "identity": {}}, publish=True)
        assert not objects
        pub.bootstrap(record, publish=True)
        assert pub.reader.resolve("main").terminal == record
        assert headers[channel_key("main")]["If-None-Match"] == "*"
        assert headers[channel_key("main")]["Cache-Control"] == "no-store"
        old, etag = pub._read("main")
        updated = {**old, "revision": old["revision"] + 1}
        faults["lose_put"] = True
        pub._write(channel_key("main"), updated, etag)
        assert headers[channel_key("main")]["If-Match"] == etag
        with pytest.raises(ChannelConflict):
            pub.store.put(channel_key("main"), canonical_json(old), etag)
        assert pub._read("main")[0] == updated
        faults["stale_public"] = b"{}"
        with pytest.raises(PublicVisibilityError, match="Committed"):
            pub._write(channel_key("main"), {**updated, "revision": 3}, pub._read("main")[1])
        assert pub._read("main")[0]["revision"] == 3


def test_source_bootstrap_preview_listing_and_incomplete_pagination():
    with object_server() as (url, objects, _headers, requests, faults):
        pub = publisher(url)
        main = source_record()
        assert pub.bootstrap(main) == main and not objects
        names = ("main", "first", "second", "third")
        for name in names:
            pub.bootstrap(source_record(name), publish=True)
        assert {r["name"] for r in pub.list()} == set(names)
        assert any("continuation-token" in path for _method, path in requests)
        assert pub.reader.resolve("main").manifest is None
        faults["bad_pagination"] = True
        with pytest.raises(ChannelError, match="pagination"):
            pub.list()
        assert set(objects) == {channel_key(name) for name in names}


def test_concurrent_source_cas_refuses_a_stale_writer():
    with object_server() as (url, _objects, _headers, _requests, _faults):
        pub = publisher(url)
        original = pub.bootstrap(source_record(), publish=True)
        _record, etag = pub._read("main")
        candidates = [{**original, "revision": 2,
                       "delivery": {"kind": "source-branch", "branch": branch}}
                      for branch in ("first", "second")]

        def write(record):
            try:
                pub.store.put(channel_key("main"), canonical_json(record), etag)
            except ChannelConflict:
                return False
            return True

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(write, candidates))
        assert sum(outcomes) == 1
        assert pub._read("main")[0] == candidates[outcomes.index(True)]


def test_immutable_metadata_lost_response_and_exact_retry():
    with object_server() as (url, objects, headers, _requests, faults):
        pub = publisher(url)
        key = "releases/commit/" + "a" * 40 + "/handoff-termux.json"
        body = canonical_json({"schema": 2, "commit": "a" * 40, "name": "termux",
                               "files": [{"path": "package.deb", "size": 1, "sha256": "b" * 64}]})
        faults["lose_put"] = True
        pub.store.put(key, body)
        assert objects[key] == body
        assert headers[key]["If-None-Match"] == "*"
        assert headers[key]["authorization"].startswith("AWS4-HMAC-SHA256 ")
        pub.store.put(key, body)
        with pytest.raises(ChannelConflict):
            pub.store.put(key, body + b" ")
        assert objects[key] == body


@pytest.mark.parametrize("policy", ["preview", "stable-release", "canary-release"])
def test_native_channel_bootstrap_is_refused_before_authorization(policy):
    with object_server() as (url, objects, _headers, requests, _faults):
        actions = []
        pub = publisher(url, authorize=lambda action, record: actions.append(action))
        record = {**source_record(), "policy": policy, "identity": dict(ARCHIVED_IDENTITY)}
        with pytest.raises(ChannelError, match="active source branch"):
            pub.bootstrap(record, publish=True)
        assert not actions and not objects and not requests


@pytest.mark.parametrize("changes", [
    {"repository": "other/hermes-agent"},
    {"delivery": {"kind": "source-branch", "branch": "../escape"}},
    {"head": {}},
    {"revision": True},
])
def test_source_bootstrap_refuses_invalid_inputs_before_publication(changes):
    with object_server() as (url, objects, _headers, requests, _faults):
        pub = publisher(url)
        with pytest.raises(ChannelError):
            pub.bootstrap({**source_record(), **changes}, publish=True)
        assert not objects and not requests
