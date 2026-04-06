"""Pure-Python async IPMI RMCP+ client for Dell iDRAC fan control."""
from __future__ import annotations

import asyncio
import hashlib
import hmac as hmac_mod
import os
import struct
from typing import Any, Callable

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# ---------------------------------------------------------------------------
# Protocol constants
# ---------------------------------------------------------------------------

RMCP_HEADER = bytes([0x06, 0x00, 0xFF, 0x07])
_AUTH_TYPE = 0x06
_PAYLOAD_IPMI = 0x00
_PAYLOAD_OPEN_REQ = 0x10
_PAYLOAD_OPEN_RESP = 0x11
_PAYLOAD_RAKP1 = 0x12
_PAYLOAD_RAKP2 = 0x13
_PAYLOAD_RAKP3 = 0x14
_PAYLOAD_RAKP4 = 0x15
_BMC_ADDR = 0x20
_REMOTE_SWID = 0x81
_DEFAULT_PRIV = 0x04

DELL_SET_AUTO = bytes([0x30, 0x30, 0x01, 0x01])
DELL_DISABLE_AUTO = bytes([0x30, 0x30, 0x01, 0x00])
DELL_SET_SPEED_PREFIX = bytes([0x30, 0x30, 0x02, 0xFF])

_CIPHER_PROFILES: list[dict[str, Any]] = [
    {
        "name": "sha256-aes128",
        "auth_alg": 0x03,
        "integ_alg": 0x04,
        "hash": "sha256",
        "ac_len": 16,
    },
    {
        "name": "sha1-aes128",
        "auth_alg": 0x01,
        "integ_alg": 0x01,
        "hash": "sha1",
        "ac_len": 12,
    },
]


class IpmiError(Exception):
    """IPMI communication error."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _checksum(data: bytes | bytearray) -> int:
    return (~sum(data) + 1) & 0xFF


def _u32le(v: int) -> bytes:
    return struct.pack("<I", v & 0xFFFFFFFF)


def _r32(data: bytes | bytearray, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def _r16(data: bytes | bytearray, off: int) -> int:
    return struct.unpack_from("<H", data, off)[0]


def _aes_pad(payload: bytes) -> bytes:
    cur = len(payload) + 1
    pad_n = (16 - cur % 16) % 16
    pad = bytearray(pad_n + 1)
    for i in range(pad_n):
        pad[i] = (i + 1) & 0xFF
    pad[pad_n] = pad_n
    return bytes(pad)


def _parse_device_info(data: list[int]) -> dict[str, Any]:
    if len(data) < 11:
        return {
            "device_id": None,
            "firmware_version": None,
            "manufacturer_id": None,
            "product_id": None,
        }
    fw_major = data[2] & 0x7F
    fw_minor = data[3]
    mfr = data[6] | (data[7] << 8) | (data[8] << 16)
    pid = data[9] | (data[10] << 8)
    return {
        "device_id": data[0],
        "firmware_version": f"{fw_major}.{fw_minor:02d}",
        "manufacturer_id": mfr,
        "product_id": pid,
    }


# ---------------------------------------------------------------------------
# Asyncio UDP protocol
# ---------------------------------------------------------------------------


class _UdpProtocol(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.transport: asyncio.DatagramTransport | None = None
        self._listeners: list[Callable[[bytes], None]] = []

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:  # type: ignore[override]
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        for fn in list(self._listeners):
            fn(data)

    def error_received(self, exc: Exception) -> None:
        pass

    def add(self, fn: Callable[[bytes], None]) -> None:
        self._listeners.append(fn)

    def remove(self, fn: Callable[[bytes], None]) -> None:
        try:
            self._listeners.remove(fn)
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# Single RMCP+ session
# ---------------------------------------------------------------------------


class _Session:
    """One RMCP+ authenticated session with a BMC."""

    def __init__(
        self,
        host: str,
        port: int,
        username: bytes,
        password: bytes,
        kg: bytes,
        timeout: float,
        privilege: int,
        profile: dict[str, Any],
    ) -> None:
        self._host = host
        self._port = port
        self._user = username
        self._pw = password
        self._kg = kg
        self._timeout = timeout
        self._priv = privilege
        self._prof = profile

        self._transport: asyncio.DatagramTransport | None = None
        self._proto: _UdpProtocol | None = None

        self._csid = 0x78434154
        self._bsid = 0
        self._seq = 0
        self._out_seq = 1
        self._tag = 1
        self._cr = b""
        self._br = b""
        self._bg = b""
        self._sik = b""
        self._ik = b""
        self._ek = b""

    # -- lifecycle ---------------------------------------------------------

    async def connect(self) -> None:
        loop = asyncio.get_running_loop()
        self._transport, self._proto = await loop.create_datagram_endpoint(
            _UdpProtocol, local_addr=("0.0.0.0", 0)
        )
        await self._open_session()
        await self._rakp1()
        await self._rakp3()
        await self._set_priv()

    async def close(self) -> None:
        if self._bsid:
            try:
                await self.command(0x06, 0x3C, list(_u32le(self._bsid)), "Close")
            except Exception:
                pass
        self._shutdown_transport()

    async def close_safe(self) -> None:
        try:
            await self.close()
        except Exception:
            self._shutdown_transport()

    def _shutdown_transport(self) -> None:
        if self._transport:
            self._transport.close()
            self._transport = None

    # -- public commands ---------------------------------------------------

    async def raw(self, cmd_bytes: bytes, label: str) -> dict[str, Any]:
        return await self.command(cmd_bytes[0], cmd_bytes[1], list(cmd_bytes[2:]), label)

    async def command(
        self, net_fn: int, cmd: int, data: list[int], label: str
    ) -> dict[str, Any]:
        sq = self._seq
        payload = self._ipmi_payload(net_fn, cmd, data, sq)
        pkt = self._wrap(payload, _PAYLOAD_IPMI, True, True, self._bsid, self._out_seq)
        self._seq = (self._seq + 1) & 0x3F
        self._out_seq += 1
        resp = await self._xfer(pkt, lambda m: self._parse_ipmi(m, sq, cmd), label)
        if resp["cc"] != 0:
            raise IpmiError(f"{label}: completion 0x{resp['cc']:02x}")
        return resp

    # -- session establishment ---------------------------------------------

    async def _open_session(self) -> None:
        self._tag = (self._tag + 1) & 0xFF
        pl = bytearray(32)
        pl[0] = self._tag
        struct.pack_into("<I", pl, 4, self._csid)
        pl[8] = 0
        pl[11] = 8
        pl[12] = self._prof["auth_alg"]
        pl[16] = 1
        pl[19] = 8
        pl[20] = self._prof["integ_alg"]
        pl[24] = 2
        pl[27] = 8
        pl[28] = 1
        pkt = self._wrap(bytes(pl), _PAYLOAD_OPEN_REQ, False, False, 0, 0)

        def _m(msg: bytes) -> dict[str, Any] | None:
            p = self._parse_rmcp(msg)
            return p if p and p["pt"] == _PAYLOAD_OPEN_RESP else None

        r = await self._xfer(pkt, _m, "OpenSession")
        d = r["pl"]
        if d[1] != 0:
            raise IpmiError(f"OpenSession status 0x{d[1]:02x}")
        if _r32(d, 4) != self._csid:
            raise IpmiError("OpenSession console-ID mismatch")
        self._bsid = _r32(d, 8)

    async def _rakp1(self) -> None:
        self._tag = (self._tag + 1) & 0xFF
        self._cr = os.urandom(16)
        pl = bytearray(28 + len(self._user))
        pl[0] = self._tag
        struct.pack_into("<I", pl, 4, self._bsid)
        pl[8:24] = self._cr
        pl[24] = self._priv
        pl[27] = len(self._user)
        pl[28 : 28 + len(self._user)] = self._user
        pkt = self._wrap(bytes(pl), _PAYLOAD_RAKP1, False, False, 0, 0)

        def _m(msg: bytes) -> dict[str, Any] | None:
            p = self._parse_rmcp(msg)
            return p if p and p["pt"] == _PAYLOAD_RAKP2 else None

        r = await self._xfer(pkt, _m, "RAKP1")
        d = r["pl"]
        if d[1] != 0:
            raise IpmiError(f"RAKP2 status 0x{d[1]:02x}")
        if _r32(d, 4) != self._csid:
            raise IpmiError("RAKP2 console-ID mismatch")
        self._br = bytes(d[8:24])
        self._bg = bytes(d[24:40])
        dlen = 32 if self._prof["hash"] == "sha256" else 20
        got = bytes(d[40 : 40 + dlen])
        exp = self._hmac(
            _u32le(self._csid)
            + _u32le(self._bsid)
            + self._cr
            + self._br
            + self._bg
            + bytes([self._priv, len(self._user)])
            + self._user,
            self._pw,
        )
        if not hmac_mod.compare_digest(got, exp):
            raise IpmiError("RAKP2 HMAC mismatch")
        self._sik = self._hmac(
            self._cr
            + self._br
            + bytes([self._priv, len(self._user)])
            + self._user,
            self._kg,
        )
        self._ik = self._hmac(bytes([0x01] * 20), self._sik)
        self._ek = self._hmac(bytes([0x02] * 20), self._sik)[:16]

    async def _rakp3(self) -> None:
        self._tag = (self._tag + 1) & 0xFF
        ac = self._hmac(
            self._br
            + _u32le(self._csid)
            + bytes([self._priv, len(self._user)])
            + self._user,
            self._pw,
        )
        pl = bytes([self._tag, 0, 0, 0]) + _u32le(self._bsid) + ac
        pkt = self._wrap(pl, _PAYLOAD_RAKP3, False, False, 0, 0)

        def _m(msg: bytes) -> dict[str, Any] | None:
            p = self._parse_rmcp(msg)
            return p if p and p["pt"] == _PAYLOAD_RAKP4 else None

        r = await self._xfer(pkt, _m, "RAKP3")
        d = r["pl"]
        if d[1] != 0:
            raise IpmiError(f"RAKP4 status 0x{d[1]:02x}")
        if _r32(d, 4) != self._csid:
            raise IpmiError("RAKP4 console-ID mismatch")
        acl = self._prof["ac_len"]
        got = bytes(d[8 : 8 + acl])
        exp = self._hmac(
            self._cr + _u32le(self._bsid) + self._bg, self._sik
        )[:acl]
        if not hmac_mod.compare_digest(got, exp):
            raise IpmiError("RAKP4 HMAC mismatch")

    async def _set_priv(self) -> None:
        await self.command(0x06, 0x3B, [self._priv], "SetPrivilege")

    # -- packet framing ----------------------------------------------------

    def _ipmi_payload(
        self, nf: int, cmd: int, data: list[int], sq: int
    ) -> bytes:
        hdr = bytes([_BMC_ADDR, (nf << 2) & 0xFF])
        body = bytes([_REMOTE_SWID, (sq << 2) & 0xFF, cmd] + data)
        return hdr + bytes([_checksum(hdr)]) + body + bytes([_checksum(body)])

    def _wrap(
        self,
        payload: bytes,
        pt: int,
        auth: bool,
        enc: bool,
        sid: int,
        seq: int,
    ) -> bytes:
        flags = pt
        if auth:
            flags |= 0x40
        if enc:
            flags |= 0x80

        section = payload
        if enc:
            iv = os.urandom(16)
            plain = payload + _aes_pad(payload)
            enc_obj = Cipher(algorithms.AES(self._ek), modes.CBC(iv)).encryptor()
            section = iv + enc_obj.update(plain) + enc_obj.finalize()

        hdr = struct.pack("<II", sid & 0xFFFFFFFF, seq & 0xFFFFFFFF)
        plen = struct.pack("<H", len(section))
        pkt = RMCP_HEADER + bytes([_AUTH_TYPE, flags]) + hdr + plen + section

        if not auth:
            return pkt

        pad_n = (4 - (len(pkt) - 2) % 4) % 4
        ap = bytes([0xFF] * pad_n)
        trailer = bytes([pad_n, 0x07])
        ic = self._integrity(pkt[4:] + ap + trailer)
        return pkt + ap + trailer + ic

    # -- parsing -----------------------------------------------------------

    def _parse_rmcp(self, msg: bytes) -> dict[str, Any] | None:
        if len(msg) < 16 or msg[:4] != RMCP_HEADER or msg[4] != _AUTH_TYPE:
            return None
        plen = _r16(msg, 14)
        return {"pt": msg[5] & 0x3F, "sid": _r32(msg, 6), "pl": msg[16 : 16 + plen]}

    def _parse_ipmi(
        self, msg: bytes, exp_sq: int, exp_cmd: int
    ) -> dict[str, Any] | None:
        p = self._parse_rmcp(msg)
        if not p or p["pt"] != _PAYLOAD_IPMI:
            return None
        if not (msg[5] & 0x40) or not (msg[5] & 0x80):
            raise IpmiError("Response not authenticated/encrypted")
        if p["sid"] != self._csid:
            return None

        acl = self._prof["ac_len"]
        got_ic = msg[len(msg) - acl :]
        exp_ic = self._integrity(msg[4 : len(msg) - acl])
        if not hmac_mod.compare_digest(got_ic, exp_ic):
            raise IpmiError("Integrity check failed")

        raw = p["pl"]
        iv = raw[:16]
        dec = Cipher(algorithms.AES(self._ek), modes.CBC(iv)).decryptor()
        plain = dec.update(raw[16:]) + dec.finalize()
        pad_n = plain[-1]
        plain = plain[: len(plain) - pad_n - 1]

        if plain[4] >> 2 != exp_sq or plain[5] != exp_cmd:
            return None
        return {
            "nf": plain[1] >> 2,
            "cmd": plain[5],
            "cc": plain[6],
            "data": list(plain[7:-1]),
        }

    # -- send / receive ----------------------------------------------------

    async def _xfer(
        self,
        pkt: bytes,
        matcher: Callable[[bytes], dict[str, Any] | None],
        label: str,
    ) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()

        def _on(data: bytes) -> None:
            if fut.done():
                return
            try:
                r = matcher(data)
                if r is not None:
                    fut.set_result(r)
            except Exception as exc:
                if not fut.done():
                    fut.set_exception(exc)

        assert self._proto is not None
        self._proto.add(_on)
        assert self._transport is not None
        self._transport.sendto(pkt, (self._host, self._port))
        try:
            return await asyncio.wait_for(fut, self._timeout)
        except asyncio.TimeoutError as exc:
            raise IpmiError(f"{label} timed out ({self._timeout}s)") from exc
        finally:
            self._proto.remove(_on)

    # -- crypto helpers ----------------------------------------------------

    def _hmac(self, data: bytes, key: bytes) -> bytes:
        h = hashlib.sha256 if self._prof["hash"] == "sha256" else hashlib.sha1
        return hmac_mod.new(key, data, h).digest()

    def _integrity(self, data: bytes) -> bytes:
        return self._hmac(data, self._ik)[: self._prof["ac_len"]]


# ---------------------------------------------------------------------------
# Public client
# ---------------------------------------------------------------------------


class IpmiClient:
    """High-level async IPMI client wrapping session lifecycle."""

    def __init__(
        self,
        host: str,
        ipmi_port: int = 623,
        username: str = "root",
        password: str = "",
        timeout: int = 5,
    ) -> None:
        self._host = host
        self._port = ipmi_port
        self._user = username.encode()
        self._pw = password.encode()
        self._timeout = timeout

    async def get_device_id(self) -> dict[str, Any]:
        return await self._with_session(self._cmd_device_id)

    async def set_automatic_fan_mode(self) -> dict[str, Any]:
        return await self._with_session(self._cmd_set_auto)

    async def set_manual_fan_speed(self, speed_percent: int) -> dict[str, Any]:
        speed = max(0, min(100, speed_percent))

        async def _action(s: _Session) -> dict[str, Any]:
            await s.raw(DELL_DISABLE_AUTO, "DisableAuto")
            await s.raw(DELL_SET_SPEED_PREFIX + bytes([speed]), "SetSpeed")
            resp = await s.command(0x06, 0x01, [], "GetDeviceID")
            return _parse_device_info(resp["data"])

        return await self._with_session(_action)

    async def test_connection(self) -> dict[str, Any]:
        return await self.get_device_id()

    # -- internal ----------------------------------------------------------

    async def _with_session(
        self, action: Callable[[_Session], Any]
    ) -> dict[str, Any]:
        last_err: Exception | None = None
        for prof in _CIPHER_PROFILES:
            sess = _Session(
                self._host,
                self._port,
                self._user,
                self._pw,
                self._pw,
                self._timeout,
                _DEFAULT_PRIV,
                prof,
            )
            try:
                await sess.connect()
                result = await action(sess)
                await sess.close()
                return result
            except Exception as exc:
                last_err = exc
                await sess.close_safe()
        raise IpmiError(str(last_err)) from last_err

    @staticmethod
    async def _cmd_device_id(s: _Session) -> dict[str, Any]:
        resp = await s.command(0x06, 0x01, [], "GetDeviceID")
        return _parse_device_info(resp["data"])

    @staticmethod
    async def _cmd_set_auto(s: _Session) -> dict[str, Any]:
        await s.raw(DELL_SET_AUTO, "SetAuto")
        resp = await s.command(0x06, 0x01, [], "GetDeviceID")
        return _parse_device_info(resp["data"])
