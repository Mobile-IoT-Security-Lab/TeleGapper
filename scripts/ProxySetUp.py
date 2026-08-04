from pathlib import Path

from scripts.Util import Util


class ProxySetup:
    """Compatibility facade for the proxy utilities now centralized in Util."""

    def __init__(self, util: Util | None = None) -> None:
        self.util = util or Util()

    def reset_burp_log(self) -> None:
        self.util.reset_burp_log()

    def save_bot_traffic(self, apps_dir: Path, bot: str) -> None:
        self.util.save_bot_traffic(apps_dir, bot)


class ProxySetUp(ProxySetup):
    """Alias consistent with the historical module name."""


_DEFAULT_PROXY_SETUP = ProxySetup()


def reset_burp_log() -> None:
    _DEFAULT_PROXY_SETUP.reset_burp_log()


def save_bot_traffic(apps_dir: Path, bot: str) -> None:
    _DEFAULT_PROXY_SETUP.save_bot_traffic(apps_dir=apps_dir, bot=bot)


__all__ = ["ProxySetUp", "ProxySetup", "reset_burp_log", "save_bot_traffic"]
