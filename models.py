"""
نماذج قاعدة البيانات – SQLAlchemy (async)
يدعم SQLite افتراضياً مع إمكانية التبديل إلى PostgreSQL عبر DATABASE_URL.
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, Boolean,
    DateTime, ForeignKey, Text, JSON
)
from sqlalchemy.orm import relationship, declarative_base

Base = declarative_base()


# ─── Account ─────────────────────────────────────────────────────────────────
class Account(Base):
    __tablename__ = "accounts"

    id             = Column(Integer, primary_key=True, index=True)
    phone          = Column(String(20), unique=True, nullable=False)
    session_string = Column(Text, nullable=True)          # Pyrogram StringSession
    proxy          = Column(JSON, nullable=True)           # {"scheme","hostname","port"}
    status         = Column(String(20), default="pending") # pending/active/banned/flood
    health_score   = Column(Float, default=100.0)          # 0-100
    created_at     = Column(DateTime, default=datetime.utcnow)
    last_active    = Column(DateTime, nullable=True)

    messages       = relationship("Message", back_populates="account", cascade="all, delete")
    groups         = relationship("Group",   back_populates="account", cascade="all, delete")

    def to_dict(self):
        return {
            "id": self.id, "phone": self.phone,
            "status": self.status, "health_score": self.health_score,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_active": self.last_active.isoformat() if self.last_active else None,
        }


# ─── Group ───────────────────────────────────────────────────────────────────
class Group(Base):
    __tablename__ = "groups"

    id              = Column(Integer, primary_key=True, index=True)
    telegram_id     = Column(String(30), unique=True, nullable=False)
    title           = Column(String(255), nullable=False)
    username        = Column(String(100), nullable=True)
    account_id      = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    member_count    = Column(Integer, default=0)
    is_joined       = Column(Boolean, default=False)
    joined_at       = Column(DateTime, nullable=True)
    left_at         = Column(DateTime, nullable=True)
    is_banned       = Column(Boolean, default=False)

    # تصنيف
    protection_bot  = Column(String(50), nullable=True)   # جبل/الماسة/شيلد/...
    category        = Column(String(100), nullable=True)  # تعليمي/تجاري/...
    sub_category    = Column(String(100), nullable=True)
    language        = Column(String(20), default="ar")
    activity_level  = Column(String(20), default="unknown") # low/medium/high
    student_ratio   = Column(Float, default=0.0)           # 0-1
    ads_frequency   = Column(String(20), default="unknown") # rare/moderate/frequent
    response_rate   = Column(Float, default=0.0)           # نسبة ردود الإعلانات
    tags            = Column(JSON, default=list)
    notes           = Column(Text, nullable=True)
    last_scan       = Column(DateTime, nullable=True)

    account         = relationship("Account", back_populates="groups")
    messages        = relationship("Message", back_populates="group", cascade="all, delete")

    def to_dict(self):
        return {
            "id": self.id, "telegram_id": self.telegram_id,
            "title": self.title, "username": self.username,
            "member_count": self.member_count, "is_joined": self.is_joined,
            "is_banned": self.is_banned, "protection_bot": self.protection_bot,
            "category": self.category, "sub_category": self.sub_category,
            "language": self.language, "activity_level": self.activity_level,
            "student_ratio": self.student_ratio, "ads_frequency": self.ads_frequency,
            "response_rate": self.response_rate, "tags": self.tags or [],
            "notes": self.notes,
            "last_scan": self.last_scan.isoformat() if self.last_scan else None,
            "account_id": self.account_id,
        }


# ─── Template ────────────────────────────────────────────────────────────────
class Template(Base):
    __tablename__ = "templates"

    id           = Column(Integer, primary_key=True, index=True)
    name         = Column(String(100), nullable=False)
    base_content = Column(Text, nullable=False)
    variations   = Column(JSON, default=list)   # قائمة صيغ بديلة
    success_score = Column(Float, default=50.0) # 0-100 بناءً على معدل النجاح
    ban_risk      = Column(Float, default=0.0)  # 0-100
    created_at   = Column(DateTime, default=datetime.utcnow)

    messages     = relationship("Message", back_populates="template")

    def to_dict(self):
        return {
            "id": self.id, "name": self.name,
            "base_content": self.base_content,
            "variations": self.variations or [],
            "success_score": self.success_score,
            "ban_risk": self.ban_risk,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ─── Campaign ────────────────────────────────────────────────────────────────
class Campaign(Base):
    __tablename__ = "campaigns"

    id              = Column(Integer, primary_key=True, index=True)
    name            = Column(String(255), nullable=False)
    status          = Column(String(20), default="draft") # draft/running/paused/done
    target_criteria = Column(JSON, default=dict)  # {"categories":[],"min_members":0,...}
    schedule        = Column(JSON, default=dict)  # {"start_at":"...","interval_minutes":60}
    template_ids    = Column(JSON, default=list)  # [template_id, ...]
    created_at      = Column(DateTime, default=datetime.utcnow)
    stats           = Column(JSON, default=dict)  # {"sent":0,"deleted":0,"success":0}

    def to_dict(self):
        return {
            "id": self.id, "name": self.name, "status": self.status,
            "target_criteria": self.target_criteria or {},
            "schedule": self.schedule or {},
            "template_ids": self.template_ids or [],
            "stats": self.stats or {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ─── Message ─────────────────────────────────────────────────────────────────
class Message(Base):
    __tablename__ = "messages"

    id              = Column(Integer, primary_key=True, index=True)
    account_id      = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    group_id        = Column(Integer, ForeignKey("groups.id"),   nullable=True)
    template_id     = Column(Integer, ForeignKey("templates.id"),nullable=True)
    campaign_id     = Column(Integer, nullable=True)
    telegram_msg_id = Column(Integer, nullable=True)  # معرف الرسالة في تيليجرام
    content         = Column(Text, nullable=False)
    sent_at         = Column(DateTime, default=datetime.utcnow)
    status          = Column(String(20), default="sent") # sent/deleted/error
    deleted_at      = Column(DateTime, nullable=True)
    deletion_reason = Column(String(100), nullable=True) # bot/admin/auto

    account         = relationship("Account",  back_populates="messages")
    group           = relationship("Group",    back_populates="messages")
    template        = relationship("Template", back_populates="messages")

    def to_dict(self):
        return {
            "id": self.id, "account_id": self.account_id,
            "group_id": self.group_id, "template_id": self.template_id,
            "content": self.content[:100] + "..." if len(self.content) > 100 else self.content,
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
            "status": self.status,
            "deleted_at": self.deleted_at.isoformat() if self.deleted_at else None,
            "deletion_reason": self.deletion_reason,
        }


# ─── AuditLog ────────────────────────────────────────────────────────────────
class AuditLog(Base):
    __tablename__ = "audit_logs"

    id         = Column(Integer, primary_key=True, index=True)
    agent      = Column(String(50), nullable=False)   # اسم الوكيل
    action     = Column(String(100), nullable=False)
    details    = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "agent": self.agent,
            "action": self.action, "details": self.details or {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
