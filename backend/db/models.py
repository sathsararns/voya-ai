from datetime import datetime
from uuid import uuid4

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from db.session import Base


class Conversation(Base):
    __tablename__ = "conversations"

    conversation_id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    session_id = Column(String(255), nullable=False, index=True)
    # Nullable: a conversation started by a logged-in caller is owned by
    # that user (see routes/chat.py's get_current_user_optional usage) and
    # scoped to them everywhere — a NULL here means an anonymous caller
    # (e.g. a direct API caller with no session cookie, same as before auth
    # existed), never mixed into any user's Recent Chats. On an existing
    # database this column is added by db/bootstrap.py's
    # run_schema_bootstrap() (conversations is bootstrap-owned, not
    # Alembic-owned — see that module's docstring); the foreign key itself
    # is added separately by an Alembic migration, since bootstrap only ever
    # does ADD COLUMN/CREATE INDEX, never constraints.
    user_id = Column(String(36), ForeignKey("users.id"), nullable=True, index=True)
    title = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ChatHistory(Base):
    __tablename__ = "chat_history"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String(255), nullable=True, index=True)
    conversation_id = Column(
        String(36), ForeignKey("conversations.conversation_id"), nullable=True, index=True
    )
    user_message = Column(Text, nullable=False)
    assistant_reply = Column(Text, nullable=False)
    destination = Column(String(255), nullable=True)
    days = Column(Integer, nullable=True)
    budget_lkr = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class User(Base):
    """Customer account. Conversation.user_id links a logged-in caller's
    conversations to their account (see routes/chat.py); the underlying
    chat flow still works unauthenticated too, via Conversation.session_id
    alone, for direct API callers with no session cookie.

    Signup creates the account directly, immediately usable for login —
    there's no email-verification step (see the auth writeup for why OTP
    verification was removed).
    """

    __tablename__ = "users"

    # UUID string, not an autoincrement int — this id gets embedded in JWTs
    # and exposed to the client, so it shouldn't leak a sequential user count.
    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    email = Column(String(255), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    password_hash = Column(String(255), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)

    # Embedded in every issued JWT as a claim; bumped on password change so
    # tokens issued before a reset stop validating even though they haven't
    # technically expired yet — see services/token_service.py.
    password_changed_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    # Persistent, account-scoped travel-preferences summary — see
    # services/user_preferences.py, which owns both the shape of this dict
    # (preferred destinations, budget range, hotel/transport/food/activity
    # preferences, overall travel style) and how it's extracted/merged from
    # each chat turn. Nullable, not a default-factory dict: NULL and {} are
    # treated identically ("nothing learned yet") by every reader, so there's
    # no need to force a row write just to normalize the column's own default.
    # Deliberately a column on User itself, not a separate table — this is a
    # small, single-owner, 1:1 summary with no independent lifecycle of its
    # own, the same relationship a "profile" field would have. Added via
    # Alembic (see alembic/versions/f3a9c1d2e4b7_add_user_preferences.py) —
    # `users` is Alembic-owned, unlike conversations/chat_history (see
    # db/bootstrap.py's module docstring).
    #
    # Loaded automatically on every authenticated chat request — no separate
    # "reload on login" step exists or is needed, because this column comes
    # back fresh every time routes/auth.py's get_current_user_optional
    # re-fetches the User row from the JWT's subject claim, which already
    # happens on every request regardless of this feature.
    preferences = Column(JSON, nullable=True)
    preferences_updated_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
