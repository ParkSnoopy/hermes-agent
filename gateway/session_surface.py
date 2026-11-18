"""Per-turn client surface (floating HUD) carried through canonical admission.

A surface is a fact about ONE turn, never about the session: the same session alternates
between the app window and the HUD. It is committed with the admission
row and reaches the model as a note on the user message (api_content sidecar), so the
persisted user row stays the words the user typed and the frozen system prefix is untouched.
"""
from contextlib import contextmanager
from contextvars import ContextVar

from hermes_state_runtime import RuntimeStoreError

SURFACES = frozenset({'hud'})
_SUBMIT_FIELDS = ('surface',)
_surface_turn = ContextVar('surface_turn', default=None)


def submit_surface_fields(params):
    """The raw per-turn surface fields of a ``prompt.submit`` request, forwarded to admission."""
    return {key: params[key] for key in _SUBMIT_FIELDS if key in params}


def admit_surface(params):
    """Validate and commit the per-turn surface (``{}`` when absent)."""
    surface = params.get('surface')
    if surface is not None and surface not in SURFACES:
        raise RuntimeStoreError('invalid_params')

    committed = {}
    if surface:
        committed['surface'] = surface

    return {'surface_v1': committed} if committed else {}


def restore_surface(committed):
    """A committed ``surface_v1`` arriving across a process boundary (managed worker), re-checked by
    the admission validator: only an object admission itself could have committed is accepted."""
    if (not isinstance(committed, dict) or not committed or set(committed) - set(_SUBMIT_FIELDS)
            or admit_surface(committed).get('surface_v1', {}) != committed):
        raise RuntimeStoreError('invalid_params')
    return committed


def _hud_note(committed, valid_tool_names):
    from agent.prompt_builder import hud_surface_note
    return hud_surface_note(valid_tool_names)


_SURFACE_NOTES = {'hud': _hud_note}


def surface_note(committed, valid_tool_names=None):
    """The model-bound HUD note (``""`` for the plain app window)."""
    render = _SURFACE_NOTES.get(committed.get('surface'))
    return render(committed, valid_tool_names) if render is not None else ''


@contextmanager
def surface_turn_scope(committed):
    token = _surface_turn.set(committed)
    try:
        yield
    finally:
        _surface_turn.reset(token)



def surface_turn_note(agent):
    """The executing admission's surface note, gated on the tools this agent actually has."""
    committed = _surface_turn.get()
    if not committed:
        return ''
    return surface_note(committed, getattr(agent, 'valid_tool_names', None))


def arm_surface_turn(agent, notes=()):
    """Project the executing admission's surface onto the agent's one-turn inputs, the same way in
    process and in a managed worker: notes ride the current user message (never the system prompt).
    Assigned unconditionally so a reused agent never replays a stale note."""
    agent._gateway_turn_context_notes = '\n\n'.join(
        note for note in (*notes, surface_turn_note(agent)) if note)
