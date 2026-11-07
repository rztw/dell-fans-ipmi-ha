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

# Largest chunk we ask for when walking SDR/FRU. iDRAC6 rejects anything that
# would push the response payload past 24 bytes, so we shrink on 0xCA.
_MAX_CHUNK = 0x16
_CC_CANNOT_RETURN_BYTES = 0xCA
_CC_DATA_NOT_PRESENT = 0xC9
_CC_SENSOR_NOT_PRESENT = 0xCB

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
    # Firmware Revision 2 is BCD encoded (e.g. 0x92 -> 92, not 146).
    fw_minor = f"{data[3] >> 4}{data[3] & 0x0F}"
    mfr = data[6] | (data[7] << 8) | (data[8] << 16)
    pid = data[9] | (data[10] << 8)
    return {
        "device_id": data[0],
        "firmware_version": f"{fw_major}.{fw_minor}",
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
        resp = await self.command_raw(net_fn, cmd, data, label)
        if resp["cc"] != 0:
            raise IpmiError(f"{label}: completion 0x{resp['cc']:02x}")
        return resp

    async def command_raw(
        self, net_fn: int, cmd: int, data: list[int], label: str = "command"
    ) -> dict[str, Any]:
        """Send a command and return the completion code without raising."""
        sq = self._seq
        payload = self._ipmi_payload(net_fn, cmd, data, sq)
        pkt = self._wrap(payload, _PAYLOAD_IPMI, True, True, self._bsid, self._out_seq)
        self._seq = (self._seq + 1) & 0x3F
        self._out_seq += 1
        return await self._xfer(pkt, lambda m: self._parse_ipmi(m, sq, cmd), label)

    # -- platform inventory (iDRAC6 / IPMI-only) ---------------------------

    async def get_chassis_status(self) -> dict[str, Any]:
        """Get Chassis Status (netfn 0x00, cmd 0x01)."""
        resp = await self.command_raw(0x00, 0x01, [], "GetChassisStatus")
        if resp["cc"] != 0 or not resp["data"]:
            return {"power_on": None}
        return {"power_on": bool(resp["data"][0] & 0x01)}

    async def read_fru(self, fru_id: int = 0) -> bytes:
        """Read a FRU inventory area and return its raw bytes.

        The area reported by Get FRU Inventory Area Info is usually much larger
        than the populated data (iDRAC6 reports 4 KiB), so we parse the common
        header and only read up to the end of the last declared area.
        """
        info = await self.command_raw(0x0A, 0x10, [fru_id], "GetFRUInfo")
        if info["cc"] != 0 or len(info["data"]) < 2:
            raise IpmiError(f"GetFRUInfo: completion 0x{info['cc']:02x}")
        area_size = info["data"][0] | (info["data"][1] << 8)

        chunk_size = _MAX_CHUNK

        async def _read(offset: int, count: int) -> bytes:
            nonlocal chunk_size
            out = bytearray()
            while len(out) < count:
                pos = offset + len(out)
                want = min(chunk_size, count - len(out))
                resp = await self.command_raw(
                    0x0A,
                    0x11,
                    [fru_id, pos & 0xFF, (pos >> 8) & 0xFF, want],
                    "ReadFRUData",
                )
                if resp["cc"] == _CC_CANNOT_RETURN_BYTES and chunk_size > 1:
                    chunk_size //= 2
                    continue
                if resp["cc"] in (_CC_DATA_NOT_PRESENT, _CC_SENSOR_NOT_PRESENT):
                    break
                if resp["cc"] != 0 or not resp["data"]:
                    raise IpmiError(f"ReadFRUData: completion 0x{resp['cc']:02x}")
                got = resp["data"][0]
                if got == 0:
                    break
                out.extend(resp["data"][1 : 1 + got])
            return bytes(out)

        header = await _read(0, 8)
        if len(header) < 8:
            return header

        total = 8
        for idx in (1, 2, 3, 4, 5):
            area_off = header[idx] * 8
            if area_off == 0:
                continue
            area_hdr = await _read(area_off, 2)
            if len(area_hdr) < 2:
                continue
            total = max(total, area_off + area_hdr[1] * 8)

        return await _read(0, min(total, area_size))

    async def read_sdr_records(self) -> list[bytes]:
        """Walk the SDR repository and return every raw record."""
        info = await self.command_raw(0x0A, 0x20, [], "GetSDRRepoInfo")
        if info["cc"] != 0 or len(info["data"]) < 3:
            raise IpmiError(f"GetSDRRepoInfo: completion 0x{info['cc']:02x}")
        count = info["data"][1] | (info["data"][2] << 8)

        records: list[bytes] = []
        record_id = 0
        chunk_size = _MAX_CHUNK
        while record_id != 0xFFFF and len(records) < max(count, 1) + 8:
            resv = await self.command_raw(0x0A, 0x22, [], "ReserveSDRRepo")
            if resv["cc"] != 0 or len(resv["data"]) < 2:
                raise IpmiError(f"ReserveSDRRepo: completion 0x{resv['cc']:02x}")
            resv_id = resv["data"][0] | (resv["data"][1] << 8)

            buf = bytearray()
            next_id = None
            total = None
            offset = 0
            while total is None or offset < total:
                resp = await self.command_raw(
                    0x0A,
                    0x23,
                    [
                        resv_id & 0xFF,
                        (resv_id >> 8) & 0xFF,
                        record_id & 0xFF,
                        (record_id >> 8) & 0xFF,
                        offset & 0xFF,
                        chunk_size,
                    ],
                    "GetSDR",
                )
                if resp["cc"] == _CC_CANNOT_RETURN_BYTES and chunk_size > 1:
                    # iDRAC6 caps the response payload at 24 bytes.
                    chunk_size //= 2
                    continue
                if resp["cc"] != 0 or len(resp["data"]) < 2:
                    raise IpmiError(f"GetSDR: completion 0x{resp['cc']:02x}")
                if next_id is None:
                    next_id = resp["data"][0] | (resp["data"][1] << 8)
                chunk = bytes(resp["data"][2:])
                if not chunk:
                    break
                buf.extend(chunk)
                offset += len(chunk)
                if total is None and len(buf) >= 5:
                    total = 5 + buf[4]

            if next_id is None or next_id == record_id:
                break
            records.append(bytes(buf))
            record_id = next_id
        return records

    async def get_sensor_readings(
        self, sensor_numbers: list[int]
    ) -> dict[int, bytes]:
        """Read every sensor in one session; skip unavailable sensors."""
        out: dict[int, bytes] = {}
        for number in sensor_numbers:
            resp = await self.command_raw(0x04, 0x2D, [number], "GetSensorReading")
            if resp["cc"] == 0 and resp["data"]:
                out[number] = bytes(resp["data"])
        return out

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

    async def read_static_platform(self) -> dict[str, Any]:
        """Read the parts of the platform that never change: device ID, FRU
        inventory and the SDR repository."""

        async def _action(s: _Session) -> dict[str, Any]:
            dev = await s.command(0x06, 0x01, [], "GetDeviceID")
            device_id = _parse_device_info(dev["data"])

            try:
                fru = await s.read_fru(0)
            except IpmiError:
                fru = b""

            try:
                records = await s.read_sdr_records()
            except IpmiError:
                records = []

            return {
                "device_id": device_id,
                "fru": fru,
                "sdr_records": records,
            }

        return await self._with_session(_action)

    async def read_sensor_snapshot(
        self, sensor_numbers: list[int]
    ) -> dict[str, Any]:
        """Read every sensor plus chassis power state in one session."""

        async def _action(s: _Session) -> dict[str, Any]:
            try:
                readings = await s.get_sensor_readings(sensor_numbers)
            except IpmiError:
                readings = {}

            try:
                chassis = await s.get_chassis_status()
            except IpmiError:
                chassis = {"power_on": None}

            return {"readings": readings, "chassis": chassis}

        return await self._with_session(_action)

    async def read_platform(self) -> dict[str, Any]:
        """Collect device ID, FRU inventory, SDR records and sensor readings.

        Everything is gathered inside a single RMCP+ session, which matters on
        iDRAC6 where each handshake is comparatively expensive.
        """

        async def _action(s: _Session) -> dict[str, Any]:
            dev = await s.command(0x06, 0x01, [], "GetDeviceID")
            device_id = _parse_device_info(dev["data"])

            try:
                fru = await s.read_fru(0)
            except IpmiError:
                fru = b""

            try:
                records = await s.read_sdr_records()
            except IpmiError:
                records = []

            numbers: list[int] = []
            for raw in records:
                if len(raw) >= 8 and raw[3] in (0x01, 0x02):
                    numbers.append(raw[7])

            try:
                readings = await s.get_sensor_readings(numbers)
            except IpmiError:
                readings = {}

            try:
                chassis = await s.get_chassis_status()
            except IpmiError:
                chassis = {"power_on": None}

            return {
                "device_id": device_id,
                "fru": fru,
                "sdr_records": records,
                "readings": readings,
                "chassis": chassis,
            }

        return await self._with_session(_action)

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
