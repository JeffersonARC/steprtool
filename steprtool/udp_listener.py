"""UDP listener for N1MM RadioInfo broadcasts."""

from __future__ import annotations

import logging
import socket
import threading
import xml.etree.ElementTree as ET
from typing import Iterable


logger = logging.getLogger(__name__)

_RECV_BUFSIZE = 8192


class UdpListener:
    def __init__(self, host: str, ports: Iterable[int], sda100_controller):
        self.host = host
        self.ports = list(ports)
        self.sda100 = sda100_controller
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._sockets: list[socket.socket] = []

    def start(self) -> None:
        for port in self.ports:
            t = threading.Thread(
                target=self._run_port, args=(port,),
                name=f"udp-listener-{port}", daemon=True,
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

    def _open_socket(self, port: int) -> socket.socket | None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            except OSError:
                pass
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

    def _handle_packet(self, data: bytes, addr, port: int) -> None:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("latin-1", errors="replace")

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
            self.sda100.maybe_auto_retune(tx_freq_tens_of_hz)
        except Exception as e:
            logger.warning("auto-retune crashed on UDP packet from %s: %s", addr, e)
