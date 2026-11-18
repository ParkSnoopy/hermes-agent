"""Prompt submission, attachments, side agents, approvals, and batch-clarify locks.

``profile`` rides on every method here: the desktop routes session-scoped calls through
``requestForOwnedSession`` / ``requestForBot`` / ``requestGatewayForProfile``, which stamp it.
"""

from __future__ import annotations

from pydantic import Field

from .base import JsonValue, Params, Result, WireEnum
from .common import OpenModel, PendingApproval, SessionParams
from .registry import method

# ── prompt.submit ─────────────────────────────────────────────────────────────────────────────


class ClientSurface(WireEnum):
    """Where the user typed this turn; anything else is treated as the plain app window."""

    hud = "hud"


class PromptSubmitParams(SessionParams):
    """``text`` is normally a string; the relay / hosted paths may hand a structured (parts list)
    payload, and the busy path renders it. Truncation (rewind / edit / regenerate) needs explicit
    consent: ``confirm_truncate`` plus one durable target (``truncate_before_row_id`` preferred,
    ``truncate_before_message_id``, or the legacy ``truncate_before_user_ordinal``)."""

    text: JsonValue = ""
    display_kind: str | None = None  # only "hidden" is honoured; anything else renders as a user row
    queued: bool | None = None  # client queue drain — the busy path must hold it, never redirect/steer
    surface: str | None = None  # a ClientSurface value; unknown values clear the surface
    # Desktop-generated large-paste preview (first ~1000 chars); TITLE input only, never the model turn.
    title_preview: str | None = None
    truncate_before_user_ordinal: int | None = None
    truncate_before_row_id: int | None = None
    truncate_before_message_id: str | None = None
    confirm_truncate: bool | None = None
    confirm_empty_truncate: bool | None = None
    confirm_deep_truncate: bool | None = None  # send when the cut archives >1 user turn (enforced next release)
    rebind_survivor_row_ids: list[int] | None = None
    # In-process only: injected by the hosted-room / bot-relay handlers, never accepted from a
    # client (a client dict for ``_turn_author`` answers 4124). Excluded from the rendered wire.
    hosted_task: JsonValue | None = Field(default=None, exclude=True, alias="_hosted_task")
    turn_author: JsonValue | None = Field(default=None, exclude=True, alias="_turn_author")
    hosted_terminal_callback: JsonValue | None = Field(
        default=None, exclude=True, alias="_hosted_terminal_callback")
    hosted_images: JsonValue | None = Field(default=None, exclude=True, alias="_hosted_images")


class PromptSubmitStatus(WireEnum):
    streaming = "streaming"
    queued = "queued"
    steered = "steered"
    redirected = "redirected"


class PromptSubmitResult(Result):
    """After a truncation the survivor row ids let the client rebind its cached ``rowId``s (``None`` map
    entries: drop the cached id). ``turn_isolation`` marks a compute-host dispatch."""

    status: PromptSubmitStatus
    # The row written for THIS accepted input, captured before the worker can consume it.
    # Absent on queued/steered/redirected inputs and whenever persistence is not yet proven.
    user_row_id: int | None = None
    survivor_user_row_ids: list[int | None] | None = None
    survivor_row_id_map: dict[str, int | None] | None = None
    turn_isolation: bool | None = None
    # RT-37: the gateway's durable ledger held work for this session, so the prompt was admitted into
    # the OWNER's FIFO (``status: queued``) and runs there after the current turn; nothing runs here.
    admission_id: str | None = None
    forwarded: bool | None = None


method("prompt.submit", params=PromptSubmitParams, result=PromptSubmitResult,
       doc="Send a user turn to a live session; busy sessions queue / steer / redirect instead of refusing.")


# ── attachments ───────────────────────────────────────────────────────────────────────────────


class ImageMeta(Result):
    """``tui_gateway/server.py::_image_meta`` — dimensions when PIL can open the file."""

    name: str | None = None
    width: int | None = None
    height: int | None = None
    token_estimate: int | None = None


class AttachedImageResult(ImageMeta):
    """``methods_prompt.py::_attached_image_result``: the image is queued for the next turn."""

    attached: bool
    path: str | None = None
    count: int | None = None
    remainder: str | None = None
    text: str | None = None
    bytes: int | None = None
    message: str | None = None  # clipboard.paste: why nothing was attached


class ClipboardPasteParams(SessionParams):
    pass


method("clipboard.paste", params=ClipboardPasteParams, result=AttachedImageResult,
       doc="Save the host clipboard image into the session and queue it for the next turn.")


class ImageAttachParams(SessionParams):
    path: str  # a host path, optionally followed by remainder text (drop syntax accepted)


method("image.attach", params=ImageAttachParams, result=AttachedImageResult,
       doc="Queue a gateway-visible image file for the next turn.")


class ImageAttachBytesParams(SessionParams):
    """``content_base64`` (or the ``data`` alias) carries the bytes; ``filename`` / ``ext`` only hint
    the extension — magic bytes decide."""

    content_base64: str | None = None
    data: str | None = None
    filename: str | None = None
    ext: str | None = None


method("image.attach_bytes", params=ImageAttachBytesParams, result=AttachedImageResult,
       doc="Queue an image uploaded as base64 (remote client); reply mirrors image.attach.")


class PdfAttachParams(SessionParams):
    """Host ``path`` or base64 ``content_base64`` / ``data``; ``first_page`` / ``last_page`` bound the
    render (per-call page cap enforced server-side)."""

    path: str | None = None
    content_base64: str | None = None
    data: str | None = None
    filename: str | None = None
    first_page: int | None = None
    last_page: int | None = None


class PdfPage(ImageMeta):
    path: str
    page: int


class PdfAttachResult(Result):
    attached: bool
    filename: str
    pages_attached: int
    pages: list[PdfPage]
    count: int
    text: str


method("pdf.attach", params=PdfAttachParams, result=PdfAttachResult,
       doc="Render a PDF's pages to PNG and queue them as images for the next turn.")


class StagedFileUpload(Params):
    """Source identity returned by the browser upload route, checked against the live profile."""

    install_id: str
    path: str
    profile_home: str


class FileAttachParams(SessionParams):
    """``path`` when the file is gateway-visible, else ``data_url`` carries the bytes; ``name`` labels
    an uploaded file."""

    path: str | None = None
    data_url: str | None = None
    name: str | None = None
    staged_upload: StagedFileUpload | None = None


class FileAttachResult(Result):
    attached: bool
    name: str
    path: str
    ref_path: str
    ref_text: str
    uploaded: bool


method("file.attach", params=FileAttachParams, result=FileAttachResult,
       doc="Stage a non-image file into the session workspace and hand back its @file: ref.")


class ImageDetachParams(SessionParams):
    path: str


class ImageDetachResult(Result):
    detached: bool
    count: int


method("image.detach", params=ImageDetachParams, result=ImageDetachResult,
       doc="Drop a queued image before the turn is sent.")


class InputDetectDropParams(SessionParams):
    text: str | None = None


class InputDetectDropResult(ImageMeta):
    """``matched: false`` alone when the text is not a drop; an image drop is queued immediately
    (``is_image`` + ``count``), a file drop only yields the ``text`` to insert."""

    matched: bool
    is_image: bool | None = None
    path: str | None = None
    count: int | None = None
    text: str | None = None


method("input.detect_drop", params=InputDetectDropParams, result=InputDetectDropResult,
       doc="Recognise a terminal file drop pasted into the composer and turn it into an attachment.")


# ── side agents ───────────────────────────────────────────────────────────────────────────────


class SideAgentParams(SessionParams):
    text: str


class TaskIdResult(Result):
    """The side agent runs detached; its answer lands on the parent session as an event carrying
    this ``task_id``."""

    task_id: str


method("prompt.background", params=SideAgentParams, result=TaskIdResult,
       doc="Run a task on a fresh agent in the background; the answer arrives as background.complete.")
method("prompt.btw", params=SideAgentParams, result=TaskIdResult,
       doc="Side question over a snapshot of the live conversation; the answer arrives as btw.complete.")


class PreviewRestartParams(SessionParams):
    url: str
    cwd: str | None = None
    context: str | None = None  # the preview pane's console output


method("preview.restart", params=PreviewRestartParams, result=TaskIdResult,
       doc="Spawn a hidden agent that brings the desktop preview's dev server back up.")


# ── batch clarify locks / proxied request answers ─────────────────────────────────────────────


class ClarifyLockParams(Params):
    request_id: str
    question_id: str
    answer: JsonValue = ""  # non-string answers are JSON-encoded server-side
    profile: str | None = None


class ClarifyLockStatus(WireEnum):
    ok = "ok"
    expired = "expired"


class ClarifyLockResult(Result):
    """``remaining`` lists the qids still unanswered; the lock that empties it resolves the request.
    ``expired``: the wait already ended (timeout / cancel) — not an error."""

    status: ClarifyLockStatus
    remaining: list[str] | None = None


method("clarify.lock", params=ClarifyLockParams, result=ClarifyLockResult,
       doc="Lock one answer of a batch clarify request (editable until every question is locked).")


class RequestAnswerParams(Params):
    id: str  # the open server→client request id
    result: dict[str, JsonValue]
    profile: str | None = None


class RequestAnswerResult(Result):
    status: ClarifyLockStatus


method("request.answer", params=RequestAnswerParams, result=RequestAnswerResult,
       doc="Answer an open server→client request from a client that never received the frame.")


# ── approvals ─────────────────────────────────────────────────────────────────────────────────


class ApprovalPendingParams(SessionParams):
    pass


class ApprovalPendingResult(Result):
    approvals: list[PendingApproval]


method("approval.pending", params=ApprovalPendingParams, result=ApprovalPendingResult,
       doc="Replay the approvals still waiting on this session (reconnect / polling).")


class ApprovalReceivedParams(SessionParams):
    request_id: str


class ApprovalReceivedResult(Result):
    acknowledged: bool


method("approval.received", params=ApprovalReceivedParams, result=ApprovalReceivedResult,
       doc="Tell the backend the card is on screen, so its timeout clock starts.")


class ApprovalRespondParams(SessionParams):
    """``choice`` is one of the offered ``approval`` choices (once / session / always / deny); ``all``
    resolves every pending approval, ``request_id`` a specific one, neither the oldest."""

    choice: str | None = None  # default "deny"
    all: bool | None = None
    request_id: str | None = None


class ApprovalRespondResult(Result):
    resolved: int  # how many pending approvals this decision unblocked


method("approval.respond", params=ApprovalRespondParams, result=ApprovalRespondResult,
       doc="Deliver the user's decision on a dangerous command (falls back to durable identity on a stale sid).")


