"""models.py — جداول قاعدة البيانات"""
from datetime import datetime
from sqlalchemy import (
    Integer, String, Boolean, DateTime, Text, BigInteger, ForeignKey, func
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base


class Account(Base):
    __tablename__ = "accounts"

    id:         Mapped[int]      = mapped_column(Integer, primary_key=True)
    phone:      Mapped[str]      = mapped_column(String(30), unique=True, nullable=False)
    session:    Mapped[str|None] = mapped_column(Text, nullable=True)       # Pyrogram session string
    is_active:  Mapped[bool]     = mapped_column(Boolean, default=False)
    is_online:  Mapped[bool]     = mapped_column(Boolean, default=False)
    note:       Mapped[str|None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    messages: Mapped[list["Message"]] = relationship("Message", back_populates="account")

    def to_dict(self):
        return {
            "id": self.id, "phone": self.phone,
            "is_active": self.is_active, "is_online": self.is_online,
            "note": self.note,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Group(Base):
    __tablename__ = "groups"

    id:             Mapped[int]      = mapped_column(Integer, primary_key=True)
    telegram_id:    Mapped[int]      = mapped_column(BigInteger, unique=True, nullable=False)
    username:       Mapped[str|None] = mapped_column(String(100), nullable=True)
    title:          Mapped[str]      = mapped_column(String(200), nullable=False)
    category:       Mapped[str|None] = mapped_column(String(50), nullable=True)   # news, ads, general ...
    member_count:   Mapped[int]      = mapped_column(Integer, default=0)
    language:       Mapped[str|None] = mapped_column(String(10), nullable=True)   # ar, en ...
    is_joined:      Mapped[bool]     = mapped_column(Boolean, default=False)
    protection_bot: Mapped[str|None] = mapped_column(String(100), nullable=True)  # اسم بوت الحماية
    last_sent_at:   Mapped[datetime|None] = mapped_column(DateTime, nullable=True)
    created_at:     Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    messages: Mapped[list["Message"]] = relationship("Message", back_populates="group")

    def to_dict(self):
        return {
            "id": self.id, "telegram_id": self.telegram_id,
            "username": self.username, "title": self.title,
            "category": self.category, "member_count": self.member_count,
            "language": self.language, "is_joined": self.is_joined,
            "protection_bot": self.protection_bot,
            "last_sent_at": self.last_sent_at.isoformat() if self.last_sent_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Campaign(Base):
    __tablename__ = "campaigns"

    id:         Mapped[int]      = mapped_column(Integer, primary_key=True)
    name:       Mapped[str]      = mapped_column(String(200), nullable=False)
    text:       Mapped[str]      = mapped_column(Text, nullable=False)          # نص الرسالة
    status:     Mapped[str]      = mapped_column(String(20), default="draft")   # draft, running, done, paused
    account_id: Mapped[int|None] = mapped_column(Integer, ForeignKey("accounts.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    messages: Mapped[list["Message"]] = relationship("Message", back_populates="campaign")

    def to_dict(self):
        return {
            "id": self.id, "name": self.name,
            "text": self.text, "status": self.status,
            "account_id": self.account_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Message(Base):
    __tablename__ = "messages"

    id:             Mapped[int]      = mapped_column(Integer, primary_key=True)
    account_id:     Mapped[int]      = mapped_column(Integer, ForeignKey("accounts.id"))
    group_id:       Mapped[int]      = mapped_column(Integer, ForeignKey("groups.id"))
    campaign_id:    Mapped[int|None] = mapped_column(Integer, ForeignKey("campaigns.id"), nullable=True)
    telegram_msg_id:Mapped[int|None] = mapped_column(BigInteger, nullable=True)
    content:        Mapped[str]      = mapped_column(Text, nullable=False)
    status:         Mapped[str]      = mapped_column(String(20), default="sent")  # sent, deleted, failed
    sent_at:        Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    deleted_at:     Mapped[datetime|None] = mapped_column(DateTime, nullable=True)

    account:  Mapped["Account"]  = relationship("Account",  back_populates="messages")
    group:    Mapped["Group"]    = relationship("Group",    back_populates="messages")
    campaign: Mapped["Campaign"] = relationship("Campaign", back_populates="messages")

    def to_dict(self):
        return {
            "id": self.id, "account_id": self.account_id,
            "group_id": self.group_id, "campaign_id": self.campaign_id,
            "content": self.content, "status": self.status,
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
            "deleted_at": self.deleted_at.isoformat() if self.deleted_at else None,
        }


class Log(Base):
    __tablename__ = "logs"

    id:         Mapped[int]      = mapped_column(Integer, primary_key=True)
    source:     Mapped[str]      = mapped_column(String(50), nullable=False)   # api, copilot, userbot
    action:     Mapped[str]      = mapped_column(String(100), nullable=False)
    detail:     Mapped[str|None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "source": self.source,
            "action": self.action, "detail": self.detail,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
