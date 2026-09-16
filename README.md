# Poltergeist core

The code shared by the
[Poltergeist](https://github.com/RealistikGDPS/Poltergeist) server and its
[panel](https://github.com/RealistikGDPS/poltergeist-panel): database and
cache access, one repository per stored resource, the business services and
the configuration. It has no HTTP layer; each application builds its own
context object and calls the services.

## Layout

```
poltergeist_core/adapters/    MySQL, Redis, object storage and the Boomlings client
poltergeist_core/resources/   Repositories, their models and the published events
poltergeist_core/services/    Business rules; expected failures are returned as values
poltergeist_core/utilities/   Clock, logging, event loop, permissions
poltergeist_core/settings.py  Configuration read from the environment
```

## Using it

The package is installed from Git:

```bash
uv add git+https://github.com/RealistikGDPS/poltergeist-core
```

The lockfile pins a commit. To move to a newer one:

```bash
uv lock --upgrade-package poltergeist-core
```

`settings.py` reads every variable at import time, so the configuration must
be present before the package is imported.

## Events

Every major write is announced on Redis Pub/Sub so that webhooks and other
consumers can react without polling. Channels are `poltergeist:<kind>`; a
consumer listens with:

```bash
redis-cli PSUBSCRIBE 'poltergeist:*'
```

Each message is one JSON object:

```json
{"event": "levels.rated", "component": "rgdps-web", "emitted_at": 1789000000, "data": {...}}
```

- `event` is the kind, also the channel suffix.
- `component` names the application that emitted it: `poltergeist` (the game
  server) or `rgdps-web` (the website).
- `emitted_at` and every time inside `data` are unix seconds, UTC.
- `data` is the event's payload, listed below. Enums are their wire values:
  `ban_type` and `target_type` are strings, `difficulty`, `rating` and
  `timely_type` are the game's integers. Payloads never carry an IP address
  or a credential.

An event is published only after the transaction that produced it has
committed, so a consumer that reads the database on receipt sees the rows.
A request that is refused publishes nothing. Pub/Sub is fire-and-forget: a
consumer that is offline misses the message.

| kind | data |
|---|---|
| `users.registered` | `user_id`, `username` |
| `users.renamed` | `user_id`, `old_username`, `new_username`, `actor_user_id` (the user for a self-service rename) |
| `users.banned` | `ban_id`, `user_id`, `username`, `ban_type`, `reason`, `days`, `expires_at`, `actor_user_id` |
| `users.unbanned` | `user_id`, `username`, `ban_type`, `revoked` (bans lifted), `actor_user_id` |
| `users.flagged` | `flag_id`, `user_id`, `username`, `flag_kind` (`stats_ceiling`, `score_implausible`, `alt_account`), `summary` (one line, never an address); the write that raised the flag went through |
| `levels.uploaded` | `level_id`, `level_name`, `user_id`, `username`, `version` |
| `levels.updated` | same as `levels.uploaded`, for a re-upload of an existing level |

`levels.uploaded` is also emitted when the website's level reupload tool copies an official level; `user_id` is then the reupload bot.
| `levels.deleted` | `level_id`, `level_name`, `user_id` (the creator), `actor_user_id` |
| `levels.moved` | `level_id`, `level_name`, `from_user_id`, `from_username` (null when that account is deleted), `to_user_id`, `to_username`, `actor_user_id`; creator points of both users are recomputed |
| `levels.rated` | `level_id`, `level_name`, `user_id`, `username`, `stars`, `difficulty`, `rating`, `feature_order`, `actor_user_id`; zero stars is an unrate, a demon re-rating changes only `difficulty` |
| `timely.scheduled` | `timely_id`, `timely_type`, `sequence`, `level_id`, `level_name`, `starts_at`, `ends_at`, `actor_user_id` |
| `roles.assigned` | `user_id`, `username`, `role_id`, `role_name`, `expires_at`, `actor_user_id` |
| `roles.revoked` | `user_id`, `role_id`, `role_name`, `actor_user_id` |
| `server_settings.updated` | `changes` (setting name to new value, changed keys only), `actor_user_id` |
| `leaderboards.rebuilt` | `users` (accounts ranked) |
| `moderation.action` | `mod_action_id`, `actor_user_id`, `action`, `target_type`, `target_id`, `details`; one per `mod_actions` row, so every administrative action reaches the bus even without a typed event |

The classes live in `poltergeist_core/resources/events.py`; a service
publishes through `ctx.events`. Adding an event is a new `Event` subclass, a
`publish` call as the last write of the service function, and a row here.

## Development

```bash
uv sync
make lint
```
