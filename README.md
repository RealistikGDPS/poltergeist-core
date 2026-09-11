# Poltergeist core

The layers that a [Poltergeist](https://github.com/RealistikGDPS/Poltergeist)
Geometry Dash server and its
[control room](https://github.com/RealistikGDPS/poltergeist-panel) share:
adapters for MySQL, Redis, object storage and the official servers, one
repository per stored resource, the business services, configuration, and the
logging and event loop utilities. It contains no transport code; each
application supplies its own `AbstractContext` and drives the services.

```
poltergeist_core/
├── adapters/    Wrappers around asyncmy, redis-py, the storage volume and Boomlings
├── resources/   <Resource>Repository classes and their models
├── services/    Business rules; expected failures are returned as error values
├── utilities/   clock, logging, loop, permissions
└── settings.py  Flat configuration read from the environment at import time
```

## Using it

The library is consumed straight from Git, not from PyPI:

```bash
uv add git+https://github.com/RealistikGDPS/poltergeist-core
```

The lockfile pins the commit. Pull a newer revision with:

```bash
uv lock --upgrade-package poltergeist-core
```

Everything in `settings.py` is read with `os.environ[...]`, so the consuming
process must provide the full configuration before importing the package.

## Development

```bash
uv sync
make lint   # ruff and strict mypy
```

The code follows the layered layout `services → resources → adapters`, each
layer importing only the one to its right, with `utilities` and `settings` as
leaves.
