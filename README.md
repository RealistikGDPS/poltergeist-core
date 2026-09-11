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
poltergeist_core/resources/   Repositories and their models
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

## Development

```bash
uv sync
make lint
```
