from poltergeist_core.resources import ModerationAction
from poltergeist_core.resources import ModTarget
from poltergeist_core.services._common import AbstractContext


async def record(
    ctx: AbstractContext,
    actor_user_id: int,
    action: str,
    target_type: ModTarget,
    target_id: int,
    details: dict[str, object] | None = None,
) -> int:
    """Every mod log row is mirrored on the bus as a `ModerationAction`."""

    mod_action_id = await ctx.mod_actions.create(
        actor_user_id, action, target_type, target_id, details
    )

    await ctx.events.publish(
        ModerationAction(
            mod_action_id=mod_action_id,
            actor_user_id=actor_user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details,
        )
    )

    return mod_action_id
