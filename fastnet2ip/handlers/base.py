import argparse
import socket
from abc import ABC, abstractmethod


class OutputHandler(ABC):

    @classmethod
    @abstractmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Register format-specific CLI flags on the shared parser."""

    @abstractmethod
    def setup(self, args: argparse.Namespace) -> None:
        """Called once after arg parsing. Configure state from args."""

    @abstractmethod
    def startup(self, udp_socket: socket.socket) -> None:
        """Called once before the main loop. Send any startup messages."""

    @abstractmethod
    def process_channel(
        self,
        channel_name: str,
        old_entry: dict | None,
        udp_socket: socket.socket,
    ) -> None:
        """Encode and transmit output for one updated channel.

        Called after live_data has been updated. The handler owns all
        rate-limit and value-change logic.
        """

    @property
    @abstractmethod
    def udp_host(self) -> str: ...

    @property
    @abstractmethod
    def udp_port(self) -> int: ...
