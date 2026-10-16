"""Minimal RADIUS PAP client (RFC 2865, Message-Authenticator per RFC 2869)."""
import hashlib
import hmac
import os
import socket
import struct

ACCESS_ACCEPT, ACCESS_REJECT = 2, 3
USER_NAME, USER_PASSWORD, REPLY_MESSAGE, NAS_IDENTIFIER, MESSAGE_AUTHENTICATOR = 1, 2, 18, 32, 80


def _attr(kind, value):
    return bytes([kind, 2 + len(value)]) + value


def access_request(host, port, secret, username, password, nas_identifier=None, timeout=15):
    """Send one Access-Request; return (code, {attribute type: [values]})."""
    secret = secret.encode()
    authenticator = os.urandom(16)
    identifier = os.urandom(1)[0]
    padded = password.encode()
    padded += bytes((-len(padded)) % 16 or (16 if not padded else 0))
    encrypted = b''
    previous = authenticator
    for offset in range(0, len(padded), 16):
        digest = hashlib.md5(secret + previous).digest()
        previous = bytes(a ^ b for a, b in zip(padded[offset:offset+16], digest))
        encrypted += previous
    attrs = _attr(USER_NAME, username.encode()) + _attr(USER_PASSWORD, encrypted)
    if nas_identifier:
        attrs += _attr(NAS_IDENTIFIER, nas_identifier.encode())
    attrs += _attr(MESSAGE_AUTHENTICATOR, bytes(16))
    header = struct.pack('!BBH', 1, identifier, 20+len(attrs)) + authenticator
    packet = header + attrs
    packet = packet[:-16] + hmac.new(secret, packet, hashlib.md5).digest()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.settimeout(timeout)
        udp.sendto(packet, (host, port))
        response, _ = udp.recvfrom(4096)
    assert response[1] == identifier
    assert len(response) == struct.unpack('!H', response[2:4])[0]
    expected = hashlib.md5(response[:4] + authenticator + response[20:] + secret).digest()
    assert hmac.compare_digest(response[4:20], expected), 'Invalid RADIUS response authenticator'
    replies = {}
    body = response[20:]
    while len(body) >= 2 and body[1] >= 2:
        replies.setdefault(body[0], []).append(body[2:body[1]])
        body = body[body[1]:]
    return response[0], replies
