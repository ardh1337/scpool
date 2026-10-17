"""
Gateway WebSocket untuk Remote Agent Modem Pool
Mengelola koneksi dua arah antara Server Pusat (SaaS) dan Agen Lokal (agent.exe).
Menjaga isolasi multi-tenant data per pool_id.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.database.connection import get_db, SessionLocal
from app.database.models import UserAccount, ModemPool, Slot, SlotStatus, SIMCard, SIMSession, NumberStatus
from app.modem.sms_reader import ParsedSMS
from app.services.sms_ingestion import ingest_sms
from app.services.numbering import get_display_label

logger = logging.getLogger("agent_gateway")


class AgentGateway:
    """Manajer koneksi WebSocket untuk remote agent."""

    def __init__(self):
        # pool_id -> WebSocket
        self.active_agents: Dict[str, WebSocket] = {}
        # pool_id -> metadata
        self.agent_metadata: Dict[str, dict] = {}
        # req_id -> asyncio.Future
        self.pending_requests: Dict[str, asyncio.Future] = {}

    def is_agent_online(self, pool_id: str) -> bool:
        return pool_id in self.active_agents

    def get_agent_info(self, pool_id: str) -> Optional[dict]:
        meta = self.agent_metadata.get(pool_id)
        if not meta:
            return None
        return {
            "online": True,
            "connected_at": meta.get("connected_at"),
            "port_count": len(meta.get("ports", [])),
            "ports": meta.get("ports", []),
            "username": meta.get("username"),
            "version": meta.get("version", "1.0")
        }

    async def register(self, pool_id: str, ws: WebSocket, user: UserAccount):
        self.active_agents[pool_id] = ws
        self.agent_metadata[pool_id] = {
            "user_id": user.id,
            "username": user.username,
            "connected_at": datetime.now(timezone.utc).isoformat(),
            "ports": []
        }
        logger.info(f"Agent terhubung: pool_id={pool_id}, user={user.username}")

    def unregister(self, pool_id: str):
        self.active_agents.pop(pool_id, None)
        self.agent_metadata.pop(pool_id, None)
        # Batalkan semua future yang menggantung untuk pool ini
        logger.info(f"Agent terputus: pool_id={pool_id}")

    async def send_command_to_agent(
        self,
        pool_id: str,
        port: str,
        command: str,
        timeout: float = 12.0
    ) -> dict:
        """Mengirimkan AT command ke agen remote dan menunggu responnya."""
        ws = self.active_agents.get(pool_id)
        if not ws:
            return {"success": False, "error": f"Agent untuk pool '{pool_id}' tidak sedang online."}

        req_id = f"cmd_{datetime.now(timezone.utc).timestamp()}_{port}"
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self.pending_requests[req_id] = future

        try:
            payload = {
                "type": "cmd",
                "req_id": req_id,
                "port": port,
                "command": command,
                "timeout": timeout
            }
            await ws.send_text(json.dumps(payload))
            result = await asyncio.wait_for(future, timeout=timeout + 2.0)
            return result
        except asyncio.TimeoutError:
            return {"success": False, "error": f"Timeout menunggu respon agent untuk port {port}."}
        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            self.pending_requests.pop(req_id, None)

    async def handle_agent_message(self, pool_id: str, raw_msg: str, db: Optional[Session] = None):
        """Memproses pesan masuk dari agen remote."""
        try:
            data = json.loads(raw_msg)
        except Exception:
            return

        msg_type = data.get("type")

        # 1. Respon dari AT command yang dieksekusi agen
        if msg_type == "cmd_response":
            req_id = data.get("req_id")
            if req_id and req_id in self.pending_requests:
                fut = self.pending_requests[req_id]
                if not fut.done():
                    fut.set_result(data)
            return

        # 2. Ping-pong untuk menjaga koneksi tetap hidup
        if msg_type == "ping":
            ws = self.active_agents.get(pool_id)
            if ws:
                await ws.send_text(json.dumps({"type": "pong"}))
            return

        # 3. Laporan port yang terdeteksi di mesin agen
        if msg_type == "ports_list":
            ports = data.get("ports", [])
            version = data.get("version", "1.0")
            if pool_id in self.agent_metadata:
                self.agent_metadata[pool_id]["ports"] = ports
                self.agent_metadata[pool_id]["version"] = version

            # Perbarui mapping slot di database jika diperlukan
            should_close = False
            if db is None:
                db = SessionLocal()
                should_close = True
            try:
                slots = db.query(Slot).filter(Slot.pool_id == pool_id).order_by(Slot.slot_index).all()
                for idx, port_name in enumerate(ports):
                    slot_idx = idx + 1
                    s = next((x for x in slots if x.slot_index == slot_idx), None)
                    if s:
                        s.physical_port_id = port_name
                        if s.status == SlotStatus.UNMAPPED:
                            s.status = SlotStatus.EMPTY
                    else:
                        # Buat slot baru jika jumlah port melebihi slot terdaftar
                        new_slot = Slot(
                            pool_id=pool_id,
                            slot_index=slot_idx,
                            physical_port_id=port_name,
                            status=SlotStatus.EMPTY
                        )
                        db.add(new_slot)
                db.commit()
            except Exception as e:
                logger.error(f"Gagal update ports untuk pool {pool_id}: {e}")
                db.rollback()
            finally:
                if should_close:
                    db.close()
            return

        # 4. Laporan SMS masuk dari agen remote
        if msg_type == "sms_received":
            should_close = False
            session_to_use = db
            if session_to_use is None:
                session_to_use = SessionLocal()
                should_close = True
            try:
                slot_index = data.get("slot_index")
                port = data.get("port")
                sender = data.get("sender", "Unknown")
                body = data.get("body", "")
                timestamp_str = data.get("timestamp")
                modem_idx = data.get("index")

                # Cari slot terkait
                slot_query = session_to_use.query(Slot).filter(Slot.pool_id == pool_id)
                if slot_index:
                    target_slot = slot_query.filter(Slot.slot_index == slot_index).first()
                elif port:
                    target_slot = slot_query.filter(Slot.physical_port_id == port).first()
                else:
                    target_slot = None

                if not target_slot:
                    return

                # Parse SMS timestamp
                smsc_dt = None
                if timestamp_str:
                    try:
                        smsc_dt = datetime.fromisoformat(timestamp_str)
                    except Exception:
                        pass
                if not smsc_dt:
                    smsc_dt = datetime.now(timezone.utc)

                from app.modem.sms_reader import compute_body_hash, normalize_sender
                parsed = ParsedSMS(
                    index=modem_idx or 1,
                    status_str="REC UNREAD",
                    sender_raw=sender,
                    sender_normalized=normalize_sender(sender),
                    smsc_timestamp=smsc_dt,
                    body=body,
                    body_hash=compute_body_hash(body)
                )

                sms_obj, is_new = ingest_sms(
                    db=session_to_use,
                    slot_id=target_slot.id,
                    parsed_sms=parsed,
                    is_initial=False
                )
                if is_new:
                    logger.info(f"SMS baru diterima dari remote agent pool={pool_id}, slot={target_slot.slot_index}: {sender}")
            except Exception as e:
                logger.error(f"Gagal simpan SMS dari agent: {e}")
                session_to_use.rollback()
            finally:
                if should_close:
                    session_to_use.close()
            return

        # 5. Laporan status slot & kartu SIM dari agen remote
        if msg_type == "slot_update":
            should_close = False
            session_to_use = db
            if session_to_use is None:
                session_to_use = SessionLocal()
                should_close = True
            try:
                slot_idx = data.get("slot_index")
                cpin = data.get("cpin", "EMPTY")
                iccid = data.get("iccid")
                phone = data.get("phone")
                operator = data.get("operator")

                slot = session_to_use.query(Slot).filter(
                    Slot.pool_id == pool_id,
                    Slot.slot_index == slot_idx
                ).first()

                if not slot:
                    return

                now = datetime.now(timezone.utc)
                if cpin == "READY" and iccid:
                    slot.status = SlotStatus.READY
                    slot.updated_at = now

                    # Sesi SIM
                    sim = session_to_use.query(SIMCard).filter(SIMCard.iccid == iccid).first()
                    if not sim:
                        sim = SIMCard(
                            iccid=iccid,
                            phone_number=phone,
                            operator_name=operator,
                            number_status=NumberStatus.DIKETAHUI if phone else NumberStatus.BELUM_DIKETAHUI
                        )
                        session_to_use.add(sim)
                        session_to_use.flush()
                    elif phone and not sim.phone_number:
                        sim.phone_number = phone
                        sim.number_status = NumberStatus.DIKETAHUI

                    active_sess = session_to_use.query(SIMSession).filter(
                        SIMSession.slot_id == slot.id,
                        SIMSession.ended_at.is_(None)
                    ).first()

                    if not active_sess or active_sess.sim_card_id != sim.id:
                        if active_sess:
                            active_sess.ended_at = now
                        new_sess = SIMSession(
                            slot_id=slot.id,
                            sim_card_id=sim.id,
                            started_at=now
                        )
                        session_to_use.add(new_sess)
                elif cpin == "EMPTY":
                    slot.status = SlotStatus.EMPTY
                    active_sess = session_to_use.query(SIMSession).filter(
                        SIMSession.slot_id == slot.id,
                        SIMSession.ended_at.is_(None)
                    ).first()
                    if active_sess:
                        active_sess.ended_at = now
                elif cpin == "SIM_ERROR":
                    slot.status = SlotStatus.SIM_ERROR

                session_to_use.commit()
            except Exception as e:
                logger.error(f"Gagal update slot dari agent: {e}")
                session_to_use.rollback()
            finally:
                if should_close:
                    session_to_use.close()


# Singleton instance gateway
agent_gateway = AgentGateway()
