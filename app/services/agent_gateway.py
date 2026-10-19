"""
Gateway WebSocket untuk agent remote (agent_forwarder.py di PC klien yang memegang modem pool).

Protokol (JSON, satu objek per frame teks):

  agent -> server
    ports_list   {units: [{key, ports: [str]}], version}   (agent lama: {ports: [str]} = satu kelompok)
    slot_update  {port | slot_index, cpin: READY|EMPTY|SIM_ERROR|PORT_ERROR, iccid?, phone?}
    sms_batch    {port | slot_index, messages: [{index, sender, body, timestamp}]}   (pembacaan penuh kartu)
    sms_received {port | slot_index, sender, body, timestamp, index?, initial?}      (satu pesan)
    cmd_response {req_id, success, lines?, raw?, error?}
    ping

  server -> agent
    welcome {username}
    pong
    cmd     {req_id, port, command, timeout}
    ussd    {req_id, port, code, timeout}
    rescan  {}

Isolasi multi-tenant: koneksi dikunci pada akun (token); pesan hanya menyentuh slot di unit milik akun itu.
Agent melaporkan port per kelompok kabel USB; server membuat unit modem pool otomatis (lihat services/units.py).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import WebSocket
from sqlalchemy.orm import Session

from app.core.config import MAX_PORTS_PER_POOL, MAX_UNITS_PER_ACCOUNT
from app.core.protocol import (
    GROUP_KEY_PATTERN, MAX_BATCH_MESSAGES, MAX_BODY_CHARS, MAX_MESSAGE_BYTES, PORT_NAME_PATTERN, VALID_CPIN,
)
from app.database.connection import session_scope
from app.database.models import NumberSource, SIMCard, SIMSession, Slot, SlotStatus
from app.auth.security import SimpleRateLimiter
from app.modem.sim_status import clean_and_validate_iccid
from app.modem.sms_reader import ParsedSMS, compute_body_hash, normalize_sender
from app.modem.ussd import extract_phone_number_from_text, ussd_result_from_text
from app.services.events import log_event
from app.services.sim_numbers import apply_ussd_result, fetch_backoff, needs_auto_fetch, ussd_code_for_sim
from app.services.slot_manager import handle_sim_inserted, handle_sim_removed
from app.services.sms_ingestion import ingest_sms, ingest_sms_batch
from app.services.units import UnitError, mark_slots_offline, owned_pool_ids, sync_detected_ports

logger = logging.getLogger("agent_gateway")

MAX_REALTIME_SMS_PER_MINUTE = 300


class SmsSignal:
    """Membangunkan semua yang sedang menunggu OTP begitu SMS dari agent tersimpan, tanpa polling berkala."""

    def __init__(self):
        self._event: Optional[asyncio.Event] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _current(self) -> asyncio.Event:
        # Event terikat ke event loop pemakai pertamanya; buat baru bila loop berganti (mis. server dinyalakan ulang).
        loop = asyncio.get_running_loop()
        if self._event is None or self._loop is not loop:
            self._event, self._loop = asyncio.Event(), loop
        return self._event

    def ticket(self) -> asyncio.Event:
        """Ambil SEBELUM memeriksa database: SMS yang masuk sesudahnya pasti membangunkan wait(ticket)."""
        return self._current()

    def notify(self) -> None:
        self._current().set()
        self._event = asyncio.Event()

    @staticmethod
    async def wait(ticket: asyncio.Event, timeout: float) -> None:
        try:
            await asyncio.wait_for(ticket.wait(), timeout)
        except asyncio.TimeoutError:
            pass


sms_signal = SmsSignal()
AGENT_USSD_OVERHEAD = 12.0  # tunggu port di agent (6 dtk) + AT+CUSD=2 (2 dtk) + jaringan/WebSocket
_sms_limiter = SimpleRateLimiter()


@contextmanager
def _db_scope(db: Optional[Session]):
    if db is not None:
        yield db
    else:
        with session_scope() as scoped:
            yield scoped


def _parse_timestamp(value: Any) -> datetime:
    """Waktu SMSC dari agent (ISO-8601) -> datetime UTC beraware; jatuh ke waktu sekarang bila tidak valid."""
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _clean_text(value: Any, limit: int) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _clean_ports(raw: Any) -> List[str]:
    ports: List[str] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str) and PORT_NAME_PATTERN.match(item) and item not in ports:
                ports.append(item)
    return ports[:MAX_PORTS_PER_POOL * MAX_UNITS_PER_ACCOUNT]


def _clean_groups(data: dict) -> List[Tuple[str, List[str]]]:
    """
    Kelompok port dari agent: `units` = [{key, ports}] (agent v2.1+). Agent lama hanya mengirim `ports`, yang
    dianggap satu kelompok. Satu nama port hanya dihitung sekali dan jumlah kelompok dibatasi.
    """
    groups: List[Tuple[str, List[str]]] = []
    seen: Set[str] = set()
    raw_units = data.get("units")
    if isinstance(raw_units, list):
        for unit in raw_units[:MAX_UNITS_PER_ACCOUNT]:
            if not isinstance(unit, dict):
                continue
            key = unit.get("key")
            if not (isinstance(key, str) and GROUP_KEY_PATTERN.match(key)):
                key = "default"
            ports = [p for p in _clean_ports(unit.get("ports")) if p not in seen][:MAX_PORTS_PER_POOL]
            seen.update(ports)
            if ports:
                groups.append((key, ports))
    elif "ports" in data:
        ports = _clean_ports(data.get("ports"))[:MAX_PORTS_PER_POOL]
        if ports:
            groups.append(("default", ports))
    return groups


class AgentGateway:
    """Manajer koneksi dan pemroses pesan agent, satu koneksi aktif per akun (satu agent bisa melayani banyak unit)."""

    def __init__(self, auto_fetch_numbers: bool = False):
        self.auto_fetch_numbers = auto_fetch_numbers
        self.active_agents: Dict[int, WebSocket] = {}
        self.agent_metadata: Dict[int, dict] = {}
        self.pending_requests: Dict[str, asyncio.Future] = {}
        self._pending_by_owner: Dict[int, Set[str]] = {}
        self._fetching: Set[str] = set()
        self._tasks: Set[asyncio.Task] = set()

    # ------------------------------------------------------------------ koneksi
    def is_agent_online(self, owner_id: int) -> bool:
        return owner_id in self.active_agents

    def get_agent_info(self, owner_id: int) -> Optional[dict]:
        meta = self.agent_metadata.get(owner_id)
        if not meta:
            return None
        return {
            "online": True,
            "connected_at": meta.get("connected_at"),
            "port_count": len(meta.get("ports", [])),
            "ports": meta.get("ports", []),
            "username": meta.get("username"),
            "version": meta.get("version", "1.0"),
        }

    async def register(self, owner_id: int, ws: WebSocket, user) -> None:
        previous = self.active_agents.get(owner_id)
        if previous is not None and previous is not ws:
            self._fail_pending(owner_id, "Agent digantikan koneksi baru.")
            try:
                await previous.close(code=1012)
            except Exception:
                pass
        self.active_agents[owner_id] = ws
        self.agent_metadata[owner_id] = {
            "user_id": user.id,
            "username": user.username,
            "connected_at": datetime.now(timezone.utc).isoformat(),
            "ports": [],
        }
        logger.info("Agent terhubung: akun=%s", user.username)

    def unregister(self, owner_id: int, ws: Optional[WebSocket] = None) -> None:
        """Lepas koneksi. Bila `ws` diberikan dan sudah bukan koneksi aktif (digantikan), tidak melakukan apa pun."""
        if ws is not None and self.active_agents.get(owner_id) is not ws:
            return
        self.active_agents.pop(owner_id, None)
        self.agent_metadata.pop(owner_id, None)
        self._fail_pending(owner_id, "Agent terputus.")
        try:
            with session_scope() as db:
                mark_slots_offline(db, owner_id)
                db.commit()
        except Exception:
            logger.exception("Gagal menandai port offline (akun %s)", owner_id)
        logger.info("Agent terputus: akun_id=%s", owner_id)

    def _fail_pending(self, owner_id: int, reason: str) -> None:
        for req_id in self._pending_by_owner.pop(owner_id, set()):
            fut = self.pending_requests.get(req_id)
            if fut is not None and not fut.done():
                fut.set_result({"success": False, "error": reason})

    # ---------------------------------------------------------------- permintaan
    async def _request(self, owner_id: int, payload: dict, timeout: float, margin: float = 3.0) -> dict:
        ws = self.active_agents.get(owner_id)
        if not ws:
            return {"success": False, "error": "Agent tidak sedang online."}

        req_id = f"req_{uuid.uuid4().hex}"
        future = asyncio.get_running_loop().create_future()
        self.pending_requests[req_id] = future
        self._pending_by_owner.setdefault(owner_id, set()).add(req_id)
        try:
            await ws.send_text(json.dumps({**payload, "req_id": req_id}))
            return await asyncio.wait_for(future, timeout=timeout + margin)
        except asyncio.TimeoutError:
            return {"success": False, "error": "Timeout menunggu respons agent."}
        except Exception as exc:
            return {"success": False, "error": str(exc)}
        finally:
            self.pending_requests.pop(req_id, None)
            self._pending_by_owner.get(owner_id, set()).discard(req_id)

    async def send_command_to_agent(self, owner_id: int, port: str, command: str, timeout: float = 12.0) -> dict:
        """Kirim satu perintah AT (divalidasi allowlist di sisi agent juga) dan tunggu responsnya."""
        return await self._request(owner_id, {"type": "cmd", "port": port, "command": command, "timeout": timeout},
                                   timeout)

    async def request_ussd(self, owner_id: int, port: str, code: str,
                           timeout: float = 15.0) -> Tuple[bool, Optional[str], Optional[str]]:
        """Dial USSD lewat agent. Mengembalikan (berhasil, teks/pesan error, nomor)."""
        # Agent bisa menunggu port (AGENT_PORT_WAIT) dan AT+CUSD=2 sebelum dial; jangan menyerah sebelum agent.
        reply = await self._request(owner_id, {"type": "ussd", "port": port, "code": code, "timeout": timeout},
                                    timeout, margin=AGENT_USSD_OVERHEAD)
        raw = reply.get("raw") or ""
        if raw:
            return ussd_result_from_text(raw)
        return False, reply.get("error") or "Agent tidak mengembalikan balasan USSD.", None

    async def watch_sms(self, owner_id: int, port: str, seconds: float) -> None:
        """Minta agent membaca SMS port ini tiap beberapa detik (menunggu OTP). seconds=0 menghentikannya.
        Agent lama mengabaikan pesan ini dan tetap membaca SMS dengan jadwal biasa."""
        ws = self.active_agents.get(owner_id)
        if ws:
            try:
                await ws.send_text(json.dumps({"type": "watch_sms", "port": port, "seconds": seconds}))
            except Exception:
                logger.debug("watch_sms gagal dikirim ke agent akun %s", owner_id, exc_info=True)

    async def request_rescan(self, owner_id: int) -> bool:
        ws = self.active_agents.get(owner_id)
        if not ws:
            return False
        await ws.send_text(json.dumps({"type": "rescan"}))
        return True

    # --------------------------------------------------------------- pesan masuk
    _DB_HANDLERS = {
        "ports_list": "_on_ports_list",
        "slot_update": "_on_slot_update",
        "sms_batch": "_on_sms_batch",
        "sms_received": "_on_sms_received",
    }

    async def handle_agent_message(self, owner_id: int, raw_msg: str, db: Optional[Session] = None) -> None:
        if not isinstance(raw_msg, str) or len(raw_msg) > MAX_MESSAGE_BYTES:
            return
        try:
            data = json.loads(raw_msg)
        except ValueError:
            return
        if not isinstance(data, dict):
            return

        msg_type = data.get("type")
        if msg_type == "cmd_response":
            req_id = data.get("req_id")
            fut = self.pending_requests.get(req_id) if isinstance(req_id, str) else None
            if fut is not None and not fut.done():
                fut.set_result(data)
        elif msg_type == "ping":
            ws = self.active_agents.get(owner_id)
            if ws:
                await ws.send_text(json.dumps({"type": "pong"}))
        elif msg_type in self._DB_HANDLERS:
            handler = getattr(self, self._DB_HANDLERS[msg_type])
            # Penulisan DB bersifat sinkron; di produksi dijalankan di thread agar event loop WebSocket tidak
            # tertahan. Sesi yang diberikan pemanggil (uji) dipakai apa adanya di thread yang sama.
            result = handler(owner_id, data, db) if db is not None else await asyncio.to_thread(
                handler, owner_id, data, None)
            if msg_type == "slot_update" and result:
                self._schedule_number_fetch(owner_id, *result)
            elif msg_type in ("sms_batch", "sms_received"):
                sms_signal.notify()

    # --------------------------------------------------------------- handler DB
    @staticmethod
    def _resolve_slot(db: Session, owner_id: int, data: dict) -> Optional[Slot]:
        """Slot milik akun ini. Nama port unik per akun; `slot_index` hanya dipahami bila akun punya satu unit."""
        pool_ids = owned_pool_ids(db, owner_id)
        if not pool_ids:
            return None
        query = db.query(Slot).filter(Slot.pool_id.in_(pool_ids))
        port = data.get("port")
        if isinstance(port, str) and port:
            return query.filter(Slot.physical_port_id == port).first()
        slot_index = data.get("slot_index")
        if isinstance(slot_index, int) and not isinstance(slot_index, bool) and len(pool_ids) == 1:
            return query.filter(Slot.slot_index == slot_index).first()
        return None

    def _on_ports_list(self, owner_id: int, data: dict, db: Optional[Session]) -> None:
        groups = _clean_groups(data)
        flat = [port for _key, ports in groups for port in ports]
        meta = self.agent_metadata.get(owner_id)
        if meta is not None:
            meta["ports"] = flat
            meta["version"] = _clean_text(data.get("version"), 20) or "1.0"

        with _db_scope(db) as session:
            try:
                summary = sync_detected_ports(session, owner_id, groups)
                session.commit()
                if summary["new_units"] or summary["new_ports"]:
                    log_event(session, "INFO", "PORT",
                              f"Terdeteksi {summary['new_ports']} port baru, {summary['new_units']} modem pool baru.")
            except UnitError as exc:
                session.rollback()
                logger.warning("Deteksi port ditolak (akun %s): %s", owner_id, exc)
            except Exception:
                session.rollback()
                logger.exception("Gagal memperbarui port akun %s", owner_id)

    def _on_slot_update(self, owner_id: int, data: dict,
                        db: Optional[Session]) -> Optional[Tuple[str, str, int]]:
        """Mengembalikan (port, iccid, sim_id) bila nomor SIM perlu diambil via USSD."""
        cpin = data.get("cpin", "EMPTY")
        if cpin not in VALID_CPIN:
            return None
        iccid = clean_and_validate_iccid(data["iccid"]) if isinstance(data.get("iccid"), str) else None
        phone = extract_phone_number_from_text(data["phone"]) if isinstance(data.get("phone"), str) else None

        fetch_job: Optional[Tuple[str, str, int]] = None
        with _db_scope(db) as session:
            try:
                slot = self._resolve_slot(session, owner_id, data)
                if not slot:
                    log_event(session, "WARN", "PORT", "Laporan slot dari agent untuk port yang tidak terdaftar.",
                              dedupe_key=f"unk:{owner_id}:{data.get('port')}", dedupe_seconds=600)
                    return None

                if cpin == "READY" and iccid:
                    active = session.query(SIMSession).filter(
                        SIMSession.slot_id == slot.id, SIMSession.ended_at.is_(None)
                    ).first()
                    if active and active.sim_card and active.sim_card.iccid == iccid:
                        slot.status = SlotStatus.READY
                        slot.updated_at = datetime.now(timezone.utc)
                        session.commit()
                        sim = active.sim_card
                    else:
                        _, sim = handle_sim_inserted(
                            session, slot.id, iccid, phone_number=phone,
                            number_source=NumberSource.CNUM if phone else None,
                        )
                    if self.auto_fetch_numbers and needs_auto_fetch(sim) and slot.physical_port_id:
                        fetch_job = (slot.physical_port_id, iccid, sim.id)
                elif cpin == "EMPTY":
                    # Port yang kembali dari OFFLINE sudah berstatus EMPTY (sync_detected_ports) padahal sesi SIM
                    # lamanya masih terbuka bila kartu dicabut selama agent mati; sesi itu tetap harus ditutup.
                    open_session = session.query(SIMSession.id).filter(
                        SIMSession.slot_id == slot.id, SIMSession.ended_at.is_(None)).first()
                    if slot.status != SlotStatus.EMPTY or open_session:
                        handle_sim_removed(session, slot.id)
                elif cpin in ("SIM_ERROR", "PORT_ERROR"):
                    new_status = SlotStatus[cpin]
                    changed = slot.status != new_status
                    slot.status = new_status
                    slot.updated_at = datetime.now(timezone.utc)
                    session.commit()
                    if changed:
                        log_event(session, "WARN", "PORT" if cpin == "PORT_ERROR" else "SIM",
                                  f"Agent melaporkan {cpin}.", slot_id=slot.id, port_id=slot.physical_port_id)
            except Exception:
                session.rollback()
                logger.exception("Gagal memproses slot_update akun %s", owner_id)
                return None

        return fetch_job

    def _on_sms_batch(self, owner_id: int, data: dict, db: Optional[Session]) -> None:
        messages = data.get("messages")
        if not isinstance(messages, list):
            return
        parsed_list = [p for p in (self._parse_sms(m) for m in messages[:MAX_BATCH_MESSAGES]) if p]
        with _db_scope(db) as session:
            try:
                slot = self._resolve_slot(session, owner_id, data)
                if not slot:
                    return
                saved = ingest_sms_batch(session, slot, parsed_list)
                fresh = sum(1 for _, is_new in saved if is_new)
                if fresh:
                    logger.info("%d SMS baru dari agent akun=%s slot=%s", fresh, owner_id, slot.slot_index)
            except Exception:
                session.rollback()
                logger.exception("Gagal menyimpan batch SMS akun %s", owner_id)

    def _on_sms_received(self, owner_id: int, data: dict, db: Optional[Session]) -> None:
        parsed = self._parse_sms(data)
        if not parsed:
            return
        if not _sms_limiter.is_allowed(f"sms:{owner_id}", MAX_REALTIME_SMS_PER_MINUTE, 60):
            with _db_scope(db) as session:
                log_event(session, "WARN", "SMS",
                          f"Laju SMS dari agent melebihi {MAX_REALTIME_SMS_PER_MINUTE}/menit; pesan berlebih dibuang "
                          "(tetap aman di SIM dan terbaca pada pemindaian berikutnya).",
                          dedupe_key=f"sms-flood:{owner_id}", dedupe_seconds=300)
            return
        with _db_scope(db) as session:
            try:
                slot = self._resolve_slot(session, owner_id, data)
                if not slot:
                    log_event(session, "WARN", "SMS", "SMS dari agent untuk port yang tidak terdaftar dibuang.",
                              dedupe_key=f"unk-sms:{owner_id}", dedupe_seconds=600)
                    return
                _, is_new = ingest_sms(session, slot.id, parsed, is_initial=bool(data.get("initial")))
                if is_new:
                    logger.info("SMS baru dari agent akun=%s slot=%s", owner_id, slot.slot_index)
            except Exception:
                session.rollback()
                logger.exception("Gagal menyimpan SMS dari agent akun %s", owner_id)

    @staticmethod
    def _parse_sms(item: Any) -> Optional[ParsedSMS]:
        if not isinstance(item, dict):
            return None
        sender = _clean_text(item.get("sender"), 50) or "Unknown"
        body = _clean_text(item.get("body"), MAX_BODY_CHARS)
        index = item.get("index")
        return ParsedSMS(
            index=index if isinstance(index, int) and not isinstance(index, bool) else None,
            status_str="REC UNREAD",
            sender_raw=sender,
            sender_normalized=normalize_sender(sender),
            smsc_timestamp=_parse_timestamp(item.get("timestamp")),
            body=body,
            body_hash=compute_body_hash(body),
        )

    # ------------------------------------------------------- ambil nomor via USSD
    def _schedule_number_fetch(self, owner_id: int, port: str, iccid: str, sim_id: int) -> None:
        key = f"{owner_id}:{iccid}"
        if key in self._fetching or not fetch_backoff.allowed(iccid):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._fetching.add(key)
        task = loop.create_task(self._fetch_number(key, owner_id, port, iccid, sim_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _fetch_number(self, key: str, owner_id: int, port: str, iccid: str, sim_id: int) -> None:
        try:
            with session_scope() as db:
                sim = db.get(SIMCard, sim_id)
                if not sim or not needs_auto_fetch(sim):
                    return
                code = ussd_code_for_sim(db, iccid, sim.operator_name)
            success, _text, phone = await self.request_ussd(owner_id, port, code)
            if success:
                fetch_backoff.record_success(iccid)
            else:
                fetch_backoff.record_failure(iccid)
            with session_scope() as db:
                sim = db.get(SIMCard, sim_id)
                if sim:
                    apply_ussd_result(db, sim, success, phone)
                    db.commit()
        except Exception:
            logger.exception("Pengambilan nomor via agent gagal (akun %s)", owner_id)
        finally:
            self._fetching.discard(key)


agent_gateway = AgentGateway(auto_fetch_numbers=True)
