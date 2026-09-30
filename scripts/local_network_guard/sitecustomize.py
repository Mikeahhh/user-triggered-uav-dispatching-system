import ipaddress
import os
import socket
import sys


def local_host(host):
    if isinstance(host, bytes):
        host = host.decode("ascii", errors="strict")
    if host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
    except (ValueError, TypeError):
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    return address.is_loopback or (mapped is not None and mapped.is_loopback)


def require_local(host):
    if not local_host(host):
        raise PermissionError("Local verification forbids non-loopback network access")


def network_audit(event, args):
    if event in ("socket.connect", "socket.bind", "socket.sendto", "socket.sendmsg"):
        connection, address = args[0], args[-1]
        if connection.family in (socket.AF_INET, socket.AF_INET6):
            if address is None:
                address = connection.getpeername()
            require_local(address[0])
    elif event in ("socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyname_ex",
                   "socket.gethostbyaddr"):
        require_local(args[0])
    elif event == "socket.getnameinfo":
        require_local(args[0][0])


if os.environ.get("MASS26_LOCAL_VERIFICATION") == "1":
    sys.addaudithook(network_audit)
    original_create_connection = socket.create_connection

    def create_local_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, **options):
        require_local(address[0])
        if source_address is not None and source_address[0] in ("", "0.0.0.0", "::"):
            source_address = ("::1" if ":" in address[0] else "127.0.0.1", source_address[1])
        return original_create_connection(address, timeout, source_address=source_address, **options)

    socket.create_connection = create_local_connection
