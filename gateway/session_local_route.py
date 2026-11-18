"""``-m <alias | provider:model | provider/model>`` at ``session.create``, resolved by the owner."""
from __future__ import annotations


def resolve_local_target(authority, actor, sid):
    """Resolve a canonical local transcript segment to its authorized owner."""
    from gateway.session_contract import SessionRef
    from hermes_state_local import local_lineage_owner
    from hermes_state_runtime import RuntimeStoreError

    row = authority.db.get_session(sid)
    if row is None:
        raise RuntimeStoreError('not_found')
    with authority.db._read_ctx() as conn:
        owner = local_lineage_owner(conn, sid)
    if not owner or row.get('chat_id') != owner:
        raise RuntimeStoreError('not_found')
    ref = SessionRef(authority.profile_id, owner)
    authority.authorize(actor, ref, 'session:read')
    return ref


def resolve_launch_route(params: dict, config: dict) -> dict:
    """Return ``params`` with an explicit launch model decoded the way every other startup path does.

    The client sends the raw ``-m`` string; the authority owns the profile whose ``model_aliases:``
    / ``providers:`` define it. Unresolved, the alias name becomes the model sent to the profile
    DEFAULT provider, and the alias's own endpoint/key are never used (#103933/#107191/#109440).
    Same owner as HermesCLI / oneshot / the TUI factory: ``resolve_startup_model_route``. A plain
    model id (no alias, no qualifier) is returned untouched; explicit ``--base-url``/``--api-key``
    launches already carry their route.
    """
    model = params.get('model')
    if not isinstance(model, str) or not model.strip() or 'base_url' in params or 'api_key' in params:
        return params
    from hermes_cli.model_switch import resolve_startup_model_route
    model_cfg = config.get('model')
    if not isinstance(model_cfg, dict):
        model_cfg = {}
    explicit_provider = str(params.get('provider') or '').strip()
    route = resolve_startup_model_route(
        model, explicit_provider=explicit_provider,
        current_provider=(explicit_provider or str(model_cfg.get('provider') or '')).strip().lower(),
        user_providers=config.get('providers'), custom_providers=config.get('custom_providers'),
        alias_config=config)  # this snapshot's aliases, not a cache keyed on config.yaml alone
    if route is None:
        return params
    resolved = dict(params, model=route.model)
    if route.provider:
        resolved['provider'] = route.provider
    if route.base_url:
        resolved['base_url'] = route.base_url
    if route.api_key:
        # An alias key is a launch key: authority memory only, revoked by restart (bind_launch_key).
        resolved['api_key'] = route.api_key
    return resolved
