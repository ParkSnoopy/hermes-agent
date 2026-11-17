"""Tests for tools/tool_result_storage.py -- 3-layer tool result persistence."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from tools.budget_config import (
    DEFAULT_PREVIEW_SIZE_CHARS,
    BudgetConfig,
)
from tools.tool_result_storage import (
    PERSISTED_OUTPUT_CLOSING_TAG,
    PERSISTED_OUTPUT_TAG,
    _build_persisted_message,
    _pageable_text,
    _safe_result_filename,
    cleanup_spillover_cache,
    enforce_turn_budget,
    extract_persisted_path,
    generate_preview,
    get_spillover_dir,
    maybe_persist_tool_result,
)

# ── generate_preview ──────────────────────────────────────────────────

class TestGeneratePreview:
    def test_short_content_unchanged(self):
        text = "short result"
        preview, has_more = generate_preview(text)
        assert preview == text
        assert has_more is False

    def test_exact_boundary(self):
        text = "x" * DEFAULT_PREVIEW_SIZE_CHARS
        preview, has_more = generate_preview(text)
        assert preview == text
        assert has_more is False

class TestWriteToSpillover:
    @pytest.mark.parametrize("probed, ok", [(3, False), (None, True)])
    def test_size_probe_decides_lossless(self, probed, ok):
        """Discard a short archive; compare UTF-8 bytes, not character counts."""
        from tools.tool_result_storage import _write_to_spillover

        content = "héllo wörld ✓" * 10
        assert len(content.encode("utf-8")) == 170 != len(content)
        filename = "tc_verify_host.txt"
        real_stat = os.stat

        def fake_stat(p, *a, **kw):
            if probed is not None and str(p).endswith(filename):
                return type("S", (), {"st_size": probed})()
            return real_stat(p, *a, **kw)

        with patch("tools.tool_result_storage.os.stat", side_effect=fake_stat):
            path = _write_to_spillover(content, filename)
        assert (path is not None) is ok
        assert (get_spillover_dir() / filename).exists() is ok
        if path is not None:
            with open(path, encoding="utf-8") as fh:
                assert fh.read() == content

class TestSafeResultFilename:
    def test_preserves_normal_tool_call_id(self):
        assert _safe_result_filename("tc_456") == "tc_456.txt"

    def test_replaces_path_and_shell_metacharacters(self):
        filename = _safe_result_filename("../outside/$(whoami);x")
        assert filename.startswith("outside_whoami_x_")
        assert filename.endswith(".txt")
        assert "/" not in filename
        assert "$" not in filename
        assert ";" not in filename

# ── _build_persisted_message ──────────────────────────────────────────

class TestBuildPersistedMessage:
    def test_structure(self):
        msg = _build_persisted_message(
            preview="first 100 chars...",
            has_more=True,
            original_size=50_000,
            file_path="/tmp/hermes-results/test123.txt",
        )
        assert msg.startswith(PERSISTED_OUTPUT_TAG)
        assert msg.endswith(PERSISTED_OUTPUT_CLOSING_TAG)
        assert "/tmp/hermes-results/test123.txt" in msg
        assert "read_file" in msg
        assert "first 100 chars..." in msg
        assert "..." in msg  # has_more indicator

    def test_message_conditions_the_do_not_rerequest_promise(self):
        """#126351: the pointer must not promise the file forever, and the recovery line
        must never tell the model to blindly re-run the producing tool — that call may
        have been a mutation whose effect already landed."""
        msg = _build_persisted_message(
            preview="preview", has_more=True, original_size=50_000,
            file_path="/tmp/hermes-results/test123.txt")
        assert "re-run the original tool" not in msg
        assert "inspect the current state before retrying" in msg

# ── maybe_persist_tool_result ─────────────────────────────────────────

class TestMaybePersistToolResult:
    def test_below_threshold_returns_unchanged(self):
        content = "small result"
        result = maybe_persist_tool_result(
            content=content,
            tool_name="terminal",
            tool_use_id="tc_123",
            env=None,
            threshold=50_000,
        )
        assert result == content

    def test_above_threshold_with_env_persists(self):
        env = MagicMock()
        env.execute.return_value = {"output": "", "returncode": 0}
        content = "x" * 60_000
        result = maybe_persist_tool_result(
            content=content,
            tool_name="terminal",
            tool_use_id="tc_456",
            env=env,
            threshold=30_000,
        )
        assert PERSISTED_OUTPUT_TAG in result
        assert "tc_456.txt" in result
        assert len(result) < len(content)

    def test_persists_unwrapped_content_locally(self):
        import json
        env = MagicMock()
        raw = "line1\nline2\n" * 5_000
        content = json.dumps({"output": raw, "exit_code": 0, "error": None})
        result = maybe_persist_tool_result(
            content=content,
            tool_name="terminal",
            tool_use_id="tc_json",
            env=env,
            threshold=30_000,
        )
        assert PERSISTED_OUTPUT_TAG in result
        spill = (get_spillover_dir() / "tc_json.txt").read_text(encoding="utf-8")
        assert spill.endswith("</tool-result-metadata>\n\n" + raw)
        env.execute.assert_not_called()

    def test_tool_use_id_cannot_escape_storage_dir(self):
        env = MagicMock()
        content = "x" * 60_000
        result = maybe_persist_tool_result(
            content=content,
            tool_name="terminal",
            tool_use_id="../outside/$(whoami);x",
            env=env,
            threshold=30_000,
        )
        target = extract_persisted_path(result)
        assert target is not None
        assert target.startswith(str(get_spillover_dir() / "outside_whoami_x_"))
        assert "/../" not in target
        assert "$(whoami)" not in target
        assert ";" not in target

    def test_threshold_zero_forces_persist(self):
        env = MagicMock()
        env.execute.return_value = {"output": "", "returncode": 0}
        content = "even short content"
        result = maybe_persist_tool_result(
            content=content,
            tool_name="terminal",
            tool_use_id="tc_zero",
            env=env,
            threshold=0,
        )
        # Any non-empty content with threshold=0 should be persisted
        assert PERSISTED_OUTPUT_TAG in result

# ── enforce_turn_budget ───────────────────────────────────────────────

class TestEnforceTurnBudget:
    def test_under_budget_no_changes(self):
        msgs = [
            {"role": "tool", "tool_call_id": "t1", "content": "small"},
            {"role": "tool", "tool_call_id": "t2", "content": "also small"},
        ]
        result = enforce_turn_budget(msgs, env=None, config=BudgetConfig(turn_budget=200_000))
        assert result[0]["content"] == "small"
        assert result[1]["content"] == "also small"

    def test_medium_result_regression(self):
        """6 results of 42K chars each (252K total) — each under 100K default
        threshold but aggregate exceeds 200K budget. L3 should persist."""
        env = MagicMock()
        env.execute.return_value = {"output": "", "returncode": 0}
        msgs = [
            {"role": "tool", "tool_call_id": f"t{i}", "content": "x" * 42_000}
            for i in range(6)
        ]
        enforce_turn_budget(msgs, env=env, config=BudgetConfig(turn_budget=200_000))
        # At least some results should be persisted to get under 200K
        persisted_count = sum(
            1 for m in msgs if PERSISTED_OUTPUT_TAG in m["content"]
        )
        assert persisted_count >= 2  # Need to shed at least ~52K

    def test_empty_messages(self):
        result = enforce_turn_budget([], env=None, config=BudgetConfig(turn_budget=200_000))
        assert result == []

    def test_multimodal_results_count_their_text_and_spill_it(self):
        """Image-bearing results are part lists: the aggregate counts their text characters (not the number of
        parts) and spills the big text part, keeping images and the short notes inline."""
        img = {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}}
        msgs = [{"role": "tool", "tool_call_id": f"mm{i}",
                 "content": [{"type": "text", "text": "x" * 60_000}, {"type": "text", "text": "scale note"}, img]}
                for i in range(6)]
        enforce_turn_budget(msgs, env=None, config=BudgetConfig(turn_budget=200_000))
        assert sum(PERSISTED_OUTPUT_TAG in m["content"][0]["text"] for m in msgs) >= 2
        assert all(m["content"][1]["text"] == "scale note" and m["content"][2] == img for m in msgs)

# ── Per-tool threshold integration ────────────────────────────────────

class TestPerToolThresholds:
    """Verify registry wiring for per-tool thresholds."""

    def test_read_file_registry_cap_is_finite(self):
        """read_file must keep a finite registry cap (Layer 2 safety net)."""
        from tools.registry import registry
        try:
            import tools.file_tools
            val = registry.get_max_result_size("read_file")
            # float('inf') disables the Layer 2 result-size guard.
            assert 0 < val < float("inf"), val
        except ImportError:
            pytest.skip("file_tools not importable in test env")

    def test_search_files_threshold(self):
        from tools.registry import registry
        try:
            import tools.file_tools
            val = registry.get_max_result_size("search_files")
            assert 0 < val < float("inf"), val
        except ImportError:
            pytest.skip("file_tools not importable in test env")

# ── Host-side spillover ($HERMES_HOME/cache/spillover) ────────────────

class TestSpillover:
    @pytest.fixture(autouse=True)
    def _isolated_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
        # Reset the once-per-process prune flag so each test is independent.
        import tools.tool_result_storage as trs
        monkeypatch.setattr(trs, "_spillover_pruned_homes", set())
        yield

    def test_env_none_persists_to_spillover(self):
        """No active sandbox env (MCP-only / cron session) must persist
        host-side instead of inline-truncating — the guglielmo bundle bug."""
        content = "x" * 60_000
        result = maybe_persist_tool_result(
            content=content,
            tool_name="tool_call",
            tool_use_id="tc_mcp_1",
            env=None,
            threshold=30_000,
        )
        assert PERSISTED_OUTPUT_TAG in result
        assert "could not be saved" not in result
        spill_file = get_spillover_dir() / "tc_mcp_1.txt"
        assert spill_file.exists()
        assert spill_file.read_text(encoding="utf-8") == content
        assert str(spill_file) in result

    def test_local_env_persists_to_spillover_without_shell(self):
        """LocalEnvironment routes host-side: no env.execute() shell-out."""
        from tools.environments.local import LocalEnvironment

        env = MagicMock(spec=LocalEnvironment)

        content = "y" * 60_000
        result = maybe_persist_tool_result(
            content=content,
            tool_name="terminal",
            tool_use_id="tc_local_1",
            env=env,
            threshold=30_000,
        )
        assert PERSISTED_OUTPUT_TAG in result
        assert (get_spillover_dir() / "tc_local_1.txt").exists()
        env.execute.assert_not_called()


    def test_spillover_write_failure_falls_back_to_inline(self, monkeypatch):
        import tools.tool_result_storage as trs
        monkeypatch.setattr(trs, "_write_to_spillover", lambda *a, **k: None)
        content = "w" * 60_000
        result = maybe_persist_tool_result(
            content=content,
            tool_name="tool_call",
            tool_use_id="tc_fail_1",
            env=None,
            threshold=30_000,
        )
        assert "could not be saved" in result
        assert PERSISTED_OUTPUT_TAG not in result

    def test_cleanup_spillover_cache_removes_old_keeps_new(self):
        import os
        import time as _time

        spill_dir = get_spillover_dir()
        spill_dir.mkdir(parents=True, exist_ok=True)
        old = spill_dir / "old.txt"
        new = spill_dir / "new.txt"
        old.write_text("old", encoding="utf-8")
        new.write_text("new", encoding="utf-8")
        stale = _time.time() - (48 * 3600)
        os.utime(old, (stale, stale))

        removed = cleanup_spillover_cache(max_age_hours=24)

        assert removed == 1
        assert not old.exists()
        assert new.exists()

    def test_gateway_housekeeping_keeps_spills_for_the_retention_window(self, monkeypatch):
        """#126351: the hourly gateway sweep must not apply the 24h media age to spills — a
        25h-old unreferenced spill survives, a 100-day orphan is still reclaimed."""
        import os
        import time as _time

        import tools.tool_result_storage as trs
        from gateway.run_housekeeping_disk import housekeeping_media_caches
        spill_dir = get_spillover_dir()
        spill_dir.mkdir(parents=True, exist_ok=True)
        recent, orphan = spill_dir / "recent.txt", spill_dir / "orphan.txt"
        for path, hours in ((recent, 25), (orphan, 100 * 24)):
            path.write_text("spill", encoding="utf-8")
            stale = _time.time() - hours * 3600
            os.utime(path, (stale, stale))

        monkeypatch.setattr(trs, "_spillover_retention_hours", lambda: 90 * 24)
        housekeeping_media_caches()

        assert recent.exists()
        assert not orphan.exists()

    def test_retention_hours_reads_session_retention_days(self, monkeypatch):
        import hermes_cli.config
        import tools.tool_result_storage as trs

        monkeypatch.setattr(
            hermes_cli.config, "load_config_readonly",
            lambda: {"sessions": {"retention_days": 7}})
        assert trs._spillover_retention_hours() == 7 * 24

    def test_retention_hours_defaults_and_falls_back(self, monkeypatch):
        import hermes_cli.config
        import tools.tool_result_storage as trs

        monkeypatch.setattr(hermes_cli.config, "load_config_readonly", dict)
        assert trs._spillover_retention_hours() == 90 * 24

        def _boom():
            raise RuntimeError("config unreadable")

        monkeypatch.setattr(hermes_cli.config, "load_config_readonly", _boom)
        assert trs._spillover_retention_hours() == trs.SPILLOVER_MAX_AGE_HOURS

        monkeypatch.setattr(
            hermes_cli.config, "load_config_readonly",
            lambda: {"sessions": {"retention_days": -5}})
        assert trs._spillover_retention_hours() == trs.SPILLOVER_MAX_AGE_HOURS

        # 0 is a legal prune setting (ended sessions go at once) but open sessions survive it;
        # a 0h spill window would delete the file the first spill just wrote.
        monkeypatch.setattr(
            hermes_cli.config, "load_config_readonly",
            lambda: {"sessions": {"retention_days": 0}})
        assert trs._spillover_retention_hours() == trs.SPILLOVER_MAX_AGE_HOURS

        # YAML ``.inf`` / ``.nan`` parse as floats; int() on them raises and used to
        # silently kill every sweep after the once-per-home flag was set.
        monkeypatch.setattr(
            hermes_cli.config, "load_config_readonly",
            lambda: {"sessions": {"retention_days": float("inf")}})
        assert trs._spillover_retention_hours() == trs.SPILLOVER_MAX_AGE_HOURS
        monkeypatch.setattr(
            hermes_cli.config, "load_config_readonly",
            lambda: {"sessions": {"retention_days": float("nan")}})
        assert trs._spillover_retention_hours() == trs.SPILLOVER_MAX_AGE_HOURS

    def test_cleanup_missing_dir_returns_zero(self):
        assert cleanup_spillover_cache() == 0

    def test_first_spill_prunes_expired_files(self, monkeypatch):
        """The once-per-process prune fires on the first host-side spill."""
        import os
        import time as _time

        import tools.tool_result_storage as trs
        spill_dir = get_spillover_dir()
        spill_dir.mkdir(parents=True, exist_ok=True)
        old = spill_dir / "ancient.txt"
        old.write_text("ancient", encoding="utf-8")
        stale = _time.time() - (48 * 3600)
        os.utime(old, (stale, stale))

        # The prune default is the session retention window (>= 24h), so pin a
        # one-day retention to prove the sweep actually fires on a 48h-old file.
        monkeypatch.setattr(trs, "_spillover_retention_hours", lambda: 24)
        maybe_persist_tool_result(
            content="v" * 60_000,
            tool_name="tool_call",
            tool_use_id="tc_prune_1",
            env=None,
            threshold=30_000,
        )

        assert not old.exists()
        assert (spill_dir / "tc_prune_1.txt").exists()

    def test_read_file_on_pruned_archive_says_expired(self, tmp_path, monkeypatch):
        """A pruned archive's pointer read via read_file says "expired", not a generic miss;
        an ordinary missing file keeps the plain not-found result."""
        monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
        monkeypatch.chdir(tmp_path)
        from tools.file_tools import read_file_tool
        out = maybe_persist_tool_result(content="z" * 50_000, tool_name="terminal",
                                        tool_use_id="tc_expired_1", env=None, threshold=30_000)
        path = extract_persisted_path(out)
        assert path
        os.remove(path)

        expired = json.loads(read_file_tool(path, task_id="t_expired_archive"))
        plain = json.loads(read_file_tool(str(tmp_path / "nope.txt"), task_id="t_expired_archive"))

        assert expired["error"].startswith("File not found:")
        assert expired.get("expired") is True and "expired" in expired["hint"]
        assert "expired" not in plain and "expired" not in (plain.get("hint") or "")

# ── recovery hint in the persisted preview ────────────────────────────

# ── MCP envelope unwrapping (#90426) ──────────────────────────────────

class TestPageableText:
    """Only the MCP handler's own envelope shape is unwrapped; every other JSON stays opaque."""

    def test_single_result_envelope_is_unwrapped(self):
        assert _pageable_text(json.dumps({"result": "line one\nline two"})) == "line one\nline two"

    def test_envelope_metadata_is_kept_after_the_text(self):
        out = _pageable_text(json.dumps({"result": "text\n", "structuredContent": {"answer": 42}}))
        text, marker, tail = out.partition("\n\n<mcp-result-metadata>\n")
        assert text == "text\n"
        assert marker
        assert json.loads(tail.split("\n</mcp-result-metadata>")[0]) == {"structuredContent": {"answer": 42}}

    @pytest.mark.parametrize(
        "content",
        [
            json.dumps({"output": "line\n", "exit_code": 0}),      # ordinary tool JSON
            json.dumps({"result": "line\n", "exit_code": 0}),      # unknown sibling key
            json.dumps([{"result": "line\n"}]),                    # not an object
            json.dumps({"result": {"blob": "line\n"}}),            # structuredContent-only style
            json.dumps({"structuredContent": {"a": 1}}),           # no model-facing text at all
            json.dumps({"result": ""}),                            # empty text
            "not json at all",
            "plain text result",
        ],
    )
    def test_unrecognized_content_is_verbatim(self, content):
        assert _pageable_text(content) == content

class TestMcpEnvelopeSpillover:
    """An oversized MCP result spills its pageable text, not one escaped JSON line (#90426)."""

    @pytest.fixture(autouse=True)
    def _isolated_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
        import tools.tool_result_storage as trs
        monkeypatch.setattr(trs, "_spillover_pruned_homes", set())
        yield

    def test_envelope_spills_real_newlines(self):
        markdown = "# Research guide\n\n" + "pageable content\n" * 4_000
        envelope = json.dumps({"result": markdown}, ensure_ascii=False)
        assert len(envelope) > 30_000

        result = maybe_persist_tool_result(
            content=envelope, tool_name="mcp__archive__read_guide", tool_use_id="tc_mcp_text",
            env=None, threshold=30_000)

        assert PERSISTED_OUTPUT_TAG in result
        spill_file = get_spillover_dir() / "tc_mcp_text.txt"
        assert spill_file.read_text(encoding="utf-8") == markdown
        # read_file offset/limit is only usable if the preview is the text too.
        preview = result.split("Preview (first", 1)[1]
        assert "# Research guide" in preview
        assert "\\n" not in preview

    def test_metadata_survives_after_the_text(self):
        markdown = "body line\n" * 5_000
        envelope = json.dumps({"result": markdown, "structuredContent": {"count": 5_000}})
        maybe_persist_tool_result(
            content=envelope, tool_name="mcp__archive__structured", tool_use_id="tc_mcp_meta",
            env=None, threshold=30_000)

        spill = (get_spillover_dir() / "tc_mcp_meta.txt").read_text(encoding="utf-8")
        text, _, tail = spill.partition("\n\n<mcp-result-metadata>\n")
        assert text == markdown
        assert json.loads(tail.split("\n</mcp-result-metadata>")[0]) == {"structuredContent": {"count": 5_000}}

    def test_builtin_tool_spills_newlines_and_metadata(self):
        output = "terminal output line\n" * 2_000
        content = json.dumps({"output": output, "exit_code": 0, "error": None})
        result = maybe_persist_tool_result(
            content=content, tool_name="terminal", tool_use_id="tc_terminal_text",
            env=None, threshold=30_000)

        assert PERSISTED_OUTPUT_TAG in result
        spill = (get_spillover_dir() / "tc_terminal_text.txt").read_text(encoding="utf-8")
        head, _, text = spill.partition("\n</tool-result-metadata>\n\n")
        assert text == output
        assert json.loads(head.removeprefix("<tool-result-metadata>\n")) == {
            "error": None, "exit_code": 0, "text_field": "output"}
        assert '"exit_code": 0' in result

    def test_lists_and_dicts_follow_the_text_so_the_preview_shows_it(self):
        body = "# Big skill\n\n" + "".join(f"Step {i}: do the thing carefully.\n" for i in range(3_000))
        payload = {
            "success": True,
            "name": "big-skill",
            "description": "Use when testing spill layout.",
            "tags": ["Testing", "Spill"],
            "content": body,
            "skill_dir": "/srv/skills/testing/big-skill",
            "linked_files": {"references": [f"references/topic-{i}.md" for i in range(60)]},
            "usage_hint": "To view linked files, call skill_view(name, file_path).",
            "metadata": {"hermes": {"tags": ["Testing"], "related_skills": []}},
        }
        result = maybe_persist_tool_result(
            content=json.dumps(payload, ensure_ascii=False), tool_name="skill_view",
            tool_use_id="tc_skill_layout", env=None, threshold=30_000)

        preview = result.split("Preview (first", 1)[1]
        assert body[:1_000] in preview
        assert '"name": "big-skill"' in preview
        spill = (get_spillover_dir() / "tc_skill_layout.txt").read_text(encoding="utf-8")
        head, _, rest = spill.partition("\n</tool-result-metadata>\n\n")
        text, _, tail = rest.partition("\n\n<tool-result-metadata>\n")
        assert text == body
        fields = {**json.loads(head.removeprefix("<tool-result-metadata>\n")),
                  **json.loads(tail.removesuffix("\n</tool-result-metadata>\n"))}
        assert fields.pop("text_field") == "content"
        assert fields == {key: value for key, value in payload.items() if key != "content"}

    def test_long_terminal_notes_stay_in_the_preview(self):
        note = ("Output exceeded the capture window (head+tail shown). Full output saved to "
                "/srv/logs/" + "build-" * 40 + "out.log — page it with read_file.")
        content = json.dumps({"output": "build line\n" * 5_000, "exit_code": 2, "error": None,
                              "truncation_note": note}, ensure_ascii=False)
        result = maybe_persist_tool_result(
            content=content, tool_name="terminal", tool_use_id="tc_terminal_note",
            env=None, threshold=30_000)

        preview = result.split("Preview (first", 1)[1]
        assert note in preview
        assert '"exit_code": 2' in preview

    def test_unpageable_json_strings_still_verbatim(self):
        cases = {
            "tc_json_short": {"output": "one line", "exit_code": 0, "error": None},
            "tc_json_nul": {"output": "abcdefghijklmnop\x00\n" * 2_000, "exit_code": 0},
            "tc_json_minor": {"output": "line\n" * 100, "rows": list(range(2_000))},
        }
        for tool_use_id, payload in cases.items():
            content = json.dumps(payload)
            maybe_persist_tool_result(
                content=content, tool_name="terminal", tool_use_id=tool_use_id,
                env=None, threshold=1)
            assert (get_spillover_dir() / f"{tool_use_id}.txt").read_text(encoding="utf-8") == content

    def test_structured_content_only_result_still_verbatim(self):
        content = json.dumps({"result": {"blob": "z" * 40_000}})
        maybe_persist_tool_result(
            content=content, tool_name="mcp__archive__opaque", tool_use_id="tc_mcp_opaque",
            env=None, threshold=30_000)
        assert (get_spillover_dir() / "tc_mcp_opaque.txt").read_text(encoding="utf-8") == content


    def test_turn_budget_spill_unwraps_envelope(self):
        """The aggregate layer persists under __budget_enforcement__, so a tool-name gate would
        miss the very results it must fix; the shape gate still applies."""
        markdown = "budgeted line\n" * 3_000
        msgs = [{
            "role": "tool", "name": "mcp__archive__read", "tool_call_id": "tc_budget_mcp",
            "content": json.dumps({"result": markdown}),
        }]

        enforce_turn_budget(msgs, env=None, config=BudgetConfig(turn_budget=10_000))

        assert PERSISTED_OUTPUT_TAG in msgs[0]["content"]
        assert (get_spillover_dir() / "tc_budget_mcp.txt").read_text(encoding="utf-8") == markdown
