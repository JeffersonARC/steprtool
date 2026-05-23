"""UDP listener for N1MM RadioInfo broadcasts.

N1MM sends UDP packets with an XML <RadioInfo> body to its configured
"radio info" ports. We listen on the configured ports, parse incoming
packets, and trigger Step 100 auto-retune via Step100Controller.

Per K3IT's note: sockets are opened with both SO_REUSEADDR and SO_BROADCAST
so multiple consumers can share a port. The listener binds to the configured
host (127.0.0.1 by default, since N1MM is typically on the same machine).

A separate background thread runs per port. Each thread loops on recvfrom
with a short timeout so it can notice shutdown signals.
"""

from __future__ import annotations

import logging
import socket
import threading
import xml.etree.ElementTree as ET
from typing import Iterable


logger = logging.getLogger(__name__)


# Defensive cap on a single UDP packet. RadioInfo XML is well under 4 KB in
# practice but we leave headroom.
_RECV_BUFSIZE = 8192


class UdpListener:
    """Owns one socket and one thread per configured UDP port."""

    def __init__(self, host: str, ports: Iterable[int], step100_controller):
        self.host = host
        self.ports = list(ports)
        self.step100 = step100_controller
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._sockets: list[socket.socket] = []

    # ------------------------------------------------------------- lifecycle

    def start(self) -> None:
        for port in self.ports:
            t = threading.Thread(
                target=self._run_port,
                args=(port,),
                name=f"udp-listener-{port}",
                daemon=True,
            )
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        for sock in self._sockets:
            try:
                sock.close()
            except Exception:
                pass

    # ------------------------------------------------------------- per-port

    def _open_socket(self, port: int) -> socket.socket | None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # SO_BROADCAST is normally used by senders; for receivers it's
            # harmless and on some platforms helps with shared sockets.
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            except OSError:
                pass
            # SO_REUSEPORT is Linux/BSD-only; on Windows SO_REUSEADDR already
            # provides the share-the-port semantics we want.
            if hasattr(socket, "SO_REUSEPORT"):
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except OSError:
                    pass
            sock.bind((self.host, port))
            sock.settimeout(1.0)
            self._sockets.append(sock)
            logger.info("UDP listener bound on %s:%d", self.host, port)
            return sock
        except OSError as e:
            logger.error("UDP listener failed to bind %s:%d: %s", self.host, port, e)
            return None

    def _run_port(self, port: int) -> None:
        sock = self._open_socket(port)
        if sock is None:
            return

        while not self._stop.is_set():
            try:
                data, addr = sock.recvfrom(_RECV_BUFSIZE)
            except socket.timeout:
                continue
            except OSError as e:
                if self._stop.is_set():
                    break
                logger.warning("UDP recv error on %d: %s", port, e)
                continue

            self._handle_packet(data, addr, port)

    # ------------------------------------------------------ packet handling

    def _handle_packet(self, data: bytes, addr, port: int) -> None:
        # Decode tolerantly; N1MM emits UTF-8 in practice but some configs
        # produce Latin-1. errors='replace' avoids exceptions on stray bytes.
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("latin-1", errors="replace")

        # Quick reject for non-XML traffic on a shared port.
        stripped = text.lstrip()
        if not stripped.startswith("<"):
            return

        try:
            root = ET.fromstring(text)
        except ET.ParseError as e:
            logger.debug("UDP %d from %s: XML parse error (%s)", port, addr, e)
            return

        if root.tag != "RadioInfo":
            return

        # N1MM's documented tag is <TXFreq>. Some versions are case-sensitive,
        # so we look in a couple of common forms.
        tx = root.find("TXFreq")
        if tx is None:
            tx = root.find("txfreq")
        if tx is None or tx.text is None:
            return

        try:
            tx_freq_tens_of_hz = int(tx.text.strip())
        except ValueError:
            logger.debug("UDP %d: non-integer TXFreq %r", port, tx.text)
            return

        if tx_freq_tens_of_hz <= 0:
            return

        try:
            self.step100.maybe_auto_retune(tx_freq_tens_of_hz)
        except Exception as e:
            logger.warning("auto-retune crashed on UDP packet from %s: %s", addr, e)
