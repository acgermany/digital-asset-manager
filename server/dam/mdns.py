"""Advertise the web UI on the local network with mDNS/Bonjour (Linux hosts).

Publishes `<MDNS_NAME>.local` pointing at this machine's LAN address(es) and an
`_http._tcp` service, so phones and computers find it at http://dam.local and in
Bonjour browsers. Needs host networking (multicast can't leave a bridged container):
see the `mdns` service in docker-compose.yml.

On macOS, Docker containers can't multicast to the LAN; use the built-in `dns-sd` instead
(see README).
"""
import logging
import os
import signal
import socket
import threading

from zeroconf import IPVersion, ServiceInfo, Zeroconf

log = logging.getLogger("dam.mdns")


def lan_addresses() -> list:
    """IPv4 addresses of this host that other devices can reach (skips loopback/docker)."""
    override = os.environ.get("MDNS_ADDRESSES")
    if override:
        return [a.strip() for a in override.split(",") if a.strip()]
    addrs = set()
    try:
        # The address the OS would use to reach the LAN (no packet is sent).
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))
        addrs.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(a for a in addrs if not a.startswith(("127.", "172.17.", "169.254.")))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    name = os.environ.get("MDNS_NAME", "dam")
    title = os.environ.get("MDNS_TITLE", "Digital Asset Manager")
    port = int(os.environ.get("WEB_PORT", "80"))
    addrs = lan_addresses()
    if not addrs:
        raise SystemExit("No LAN address found; set MDNS_ADDRESSES")
    info = ServiceInfo(
        "_http._tcp.local.",
        f"{title}._http._tcp.local.",
        addresses=[socket.inet_aton(a) for a in addrs],
        port=port,
        properties={"path": "/"},
        server=f"{name}.local.",
    )
    zc = Zeroconf(ip_version=IPVersion.V4Only)
    zc.register_service(info, allow_name_change=True)
    url = f"http://{name}.local" + ("" if port == 80 else f":{port}")
    log.info("advertising %s at %s (%s)", title, url, ", ".join(addrs))
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    stop.wait()
    zc.unregister_service(info)
    zc.close()


if __name__ == "__main__":
    main()
