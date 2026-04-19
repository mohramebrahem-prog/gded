"""
models.py – نماذج قاعدة البيانات
"""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, Boolean,
    DateTime, ForeignKey, Text, JSON, BigInteger
)
from sqlalchemy.orm import relationship, declarative_base

Base = declarative_base()


class Proxy(Base):
    __tablename__ = "proxies"

    id       = Column(Integer, primary_key=True, index=True)
    label    = Column(String(100), nullable=False)
    scheme   = Column(String(10), default="socks5")
    hostname = Column(String(255), nullable=False)
    port     = Column(Integer, nullable=False)
    username = Column(String(100), nullable=True)
    password = Column(String(100), nullable=True)
    country  = Column(String(50), nullable=True)
    is_active = Column(Boolean, default=True)
    last_check = Column(DateTime, nullable=True)
    latency_ms = Column(Integer, nullable=True)

    accounts = relationship("Account", back_populates="proxy_rel")

    def to_dict(self):
        return {
            "id": self.id, "label": self.label, "scheme": self.scheme,
            "hostname": self.hostname, "port": self.port,
            "country": self.country, "is_active": self.is_active,
            "latency_ms": self.latency_ms,
            "last_check": self.last_check.isoformat() if self.last_check else None,
        }


class Account(Base):
    __tablename__ = "accounts"

    id             = Column(Integer, primary_key=True, index=True)
    phone          = Column(String(20), unique=True, nullable=False)
    session_string = Column(Text, nullable=True)
    proxy_id       = Column(Integer, ForeignKey("proxies.id"), nullable=True)
    proxy          = Column(JSON, nullable=True)   # fallback JSON proxy
    status         = Column(String(20), default="pending")
    health_score   = Column(Float, default=100.0)
    notes          = Column(Text, nullable=True)
    created_at     = Column(DateTime, default=datetime.utcnow)
    last_active    = Column(DateTime, nullable=True)

    proxy_rel = relationship("Proxy", back_populates="accounts")
    messages  = relationship("Message", back_populates="account", cascade="all, delete")
    groups    = relationship("Group",   back_populates="account", cascade="all, delete")

    def to_dict(self):
        return {
            "id": self.id, "phone": self.phone,
            "status": self.status, "health_score": self.health_score,
            "proxy_id": self.proxy_id, "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_active": self.last_active.isoformat() if self.last_active else None,
        }


class Group(Base):
    __tablename__ = "groups"

    id             = Column(Integer, primary_key=True, index=True)
    telegram_id    = Column(String(30), unique=True, nullable=False)
    title          = Column(String(255), nullable=False)
    username       = Column(String(100), nullable=True)
    account_id     = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    member_count   = Column(Integer, default=0)
    is_joined      = Column(Boolean, default=False)
    joined_at      = Column(DateTime, nullable=True)
    left_at        = Column(DateTime, nullable=True)
    is_banned      = Column(Boolean, default=False)
    is_blacklisted = Column(Boolean, default=False)
    is_favorite    = Column(Boolean, default=False)

    protection_bot = Column(String(50), nullable=True)
    category       = Column(String(100), nullable=True)
    sub_category   = Column(String(100), nullable=True)
    language       = Column(String(20), default="ar")
    activity_level = Column(String(20), default="unknown")
    student_ratio  = Column(Float, default=0.0)
    ads_frequency  = Column(String(20), default="unknown")
    response_rate  = Column(Float, default=0.0)
    tags           = Column(JSON, default=list)
    notes          = Column(Text, nullable=True)
    last_scan      = Column(DateTime, nullable=True)
    last_ad_at     = Column(DateTime, nullable=True)

    account  = relationship("Account", back_populates="groups")
    messages = relationship("Message", back_populates="group", cascade="all, delete")

    def to_dict(self):
        return {
            "id": self.id, "telegram_id": self.telegram_id,
            "title": self.title, "username": self.username,
            "member_count": self.member_count, "is_joined": self.is_joined,
            "is_banned": self.is_banned, "is_blacklisted": self.is_blacklisted,
            "is_favorite": self.is_favorite,
            "protection_bot": self.protection_bot,
            "category": self.category, "sub_category": self.sub_category,
            "language": self.language, "activity_level": self.activity_level,
            "student_ratio": self.student_ratio, "ads_frequency": self.ads_frequency,
            "response_rate": self.response_rate, "tags": self.tags or [],
            "notes": self.notes, "account_id": self.account_id,
            "last_scan": self.last_scan.isoformat() if self.last_scan else None,
            "last_ad_at": self.last_ad_at.isoformat() if self.last_ad_at else None,
        }


class Template(Base):
    __tablename__ = "templates"

    id            = Column(Integer, primary_key=True, index=True)
    name          = Column(String(100), nullable=False)
    base_content  = Column(Text, nullable=False)
    variations    = Column(JSON, default=list)
    success_score = Column(Float, default=50.0)
    ban_risk      = Column(Float, default=0.0)
    is_default    = Column(Boolean, default=False)
    created_at    = Column(DateTime, default=datetime.utcnow)

    messages = relationship("Message", back_populates="template")

    def to_dict(self):
        return {
            "id": self.id, "name": self.name,
            "base_content": self.base_content,
            "variations": self.variations or [],
            "success_score": self.success_score,
            "ban_risk": self.ban_risk,
            "is_default": self.is_default,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Campaign(Base):
    __tablename__ = "campaigns"

    id              = Column(Integer, primary_key=True, index=True)
    name            = Column(String(255), nullable=False)
    status          = Column(String(20), default="draft")
    target_criteria = Column(JSON, default=dict)
    schedule        = Column(JSON, default=dict)
    template_ids    = Column(JSON, default=list)
    stats           = Column(JSON, default=dict)
    created_at      = Column(DateTime, default=datetime.utcnow)
    started_at      = Column(DateTime, nullable=True)
    finished_at     = Column(DateTime, nullable=True)

    def to_dict(self):
        return {
            "id": self.id, "name": self.name, "status": self.status,
            "target_criteria": self.target_criteria or {},
            "schedule": self.schedule or {},
            "template_ids": self.template_ids or [],
            "stats": self.stats or {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


class Message(Base):
    __tablename__ = "messages"

    id              = Column(Integer, primary_key=True, index=True)
    account_id      = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    group_id        = Column(Integer, ForeignKey("groups.id"),   nullable=True)
    template_id     = Column(Integer, ForeignKey("templates.id"), nullable=True)
    campaign_id     = Column(Integer, nullable=True)
    telegram_msg_id = Column(Integer, nullable=True)
    content         = Column(Text, nullable=False)
    sent_at         = Column(DateTime, default=datetime.utcnow)
    status          = Column(String(20), default="sent")
    deleted_at      = Column(DateTime, nullable=True)
    deletion_reason = Column(String(100), nullable=True)

    account  = relationship("Account",  back_populates="messages")
    group    = relationship("Group",    back_populates="messages")
    template = relationship("Template", back_populates="messages")

    def to_dict(self):
        return {
            "id": self.id, "account_id": self.account_id,
            "group_id": self.group_id, "template_id": self.template_id,
            "campaign_id": self.campaign_id,
            "content": self.content[:120] + "..." if len(self.content) > 120 else self.content,
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
            "status": self.status,
            "deleted_at": self.deleted_at.isoformat() if self.deleted_at else None,
            "deletion_reason": self.deletion_reason,
        }


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id         = Column(Integer, primary_key=True, index=True)
    agent      = Column(String(50), nullable=False)
    action     = Column(String(100), nullable=False)
    details    = Column(JSON, default=dict)
    result     = Column(String(20), default="ok")
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "agent": self.agent,
            "action": self.action, "details": self.details or {},
            "result": self.result,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
