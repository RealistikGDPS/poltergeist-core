import uvloop

from poltergeist_core.utilities import logging

logger = logging.get_logger(__name__)


def install_optimal_loop() -> None:
    uvloop.install()
    logger.debug("Installed uvloop as the event loop policy.")
