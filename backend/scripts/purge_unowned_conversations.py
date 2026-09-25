"""One-off cleanup for legacy/anonymous conversations — the
Conversation.user_id IS NULL rows created before per-account chat scoping
existed (see routes/chat.py's get_current_user_optional), plus whatever
anonymous/shared-browser test traffic has accumulated since.

These rows were already confirmed NOT to leak into any authenticated
account's Recent Chats — db/crud.py's list_conversations only ever matches
a logged-in caller's own user_id, never NULL. This script exists purely to
physically remove them from the database for cleanliness/peace of mind, not
to fix a leak.

Safety:
- Defaults to a DRY RUN: reports what would be removed and writes nothing.
  Pass --apply to actually delete.
- --apply always writes a full JSON backup of every row about to be removed
  (both the conversations and their chat_history messages) to --backup-dir
  BEFORE deleting anything, so the cleanup is reversible — restore by
  re-inserting the backed-up rows if ever needed.
- Only ever targets Conversation.user_id IS NULL rows. An owned conversation
  (user_id set, i.e. anything belonging to a real account) is never touched,
  no matter what.
- Deletes chat_history rows before their parent conversation row — the
  chat_history.conversation_id column has an inbound foreign key to
  conversations with no ON DELETE CASCADE, so deleting a conversation while
  messages still reference it would fail outright otherwise.
- Both deletes run in a single transaction: either both succeed or neither
  does.

Run from the backend folder:
    python -m scripts.purge_unowned_conversations              # dry run — reports only
    python -m scripts.purge_unowned_conversations --apply       # backs up, then deletes
"""

import argparse
import json
import os
from datetime import datetime, timezone

from db.models import ChatHistory, Conversation
from db.session import SessionLocal

DEFAULT_BACKUP_DIR = os.path.join(os.path.dirname(__file__), "backups")


def _serialize_conversation(c: Conversation) -> dict:
    return {
        "conversation_id": c.conversation_id,
        "session_id": c.session_id,
        "user_id": c.user_id,
        "title": c.title,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


def _serialize_message(m: ChatHistory) -> dict:
    return {
        "id": m.id,
        "session_id": m.session_id,
        "conversation_id": m.conversation_id,
        "user_message": m.user_message,
        "assistant_reply": m.assistant_reply,
        "destination": m.destination,
        "days": m.days,
        "budget_lkr": m.budget_lkr,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Back up and remove unowned (anonymous/legacy) conversations and their messages."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete the rows. Without this flag, only reports what would happen.",
    )
    parser.add_argument(
        "--backup-dir",
        default=DEFAULT_BACKUP_DIR,
        help=f"Where to write the pre-delete JSON backup (default: {DEFAULT_BACKUP_DIR})",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        legacy_conversations = db.query(Conversation).filter(Conversation.user_id.is_(None)).all()

        if not legacy_conversations:
            print("No unowned (user_id IS NULL) conversations found - nothing to do.")
            return

        conversation_ids = [c.conversation_id for c in legacy_conversations]
        legacy_messages = (
            db.query(ChatHistory)
            .filter(ChatHistory.conversation_id.in_(conversation_ids))
            .all()
        )

        print(
            f"Found {len(legacy_conversations)} unowned conversation(s) with "
            f"{len(legacy_messages)} message(s) total."
        )

        if not args.apply:
            print(
                "Dry run only - no changes made. Re-run with --apply to delete "
                "(a JSON backup is written first)."
            )
            return

        os.makedirs(args.backup_dir, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = os.path.join(args.backup_dir, f"unowned_conversations_{timestamp}.json")

        backup = {
            "backed_up_at": datetime.now(timezone.utc).isoformat(),
            "conversations": [_serialize_conversation(c) for c in legacy_conversations],
            "messages": [_serialize_message(m) for m in legacy_messages],
        }
        with open(backup_path, "w", encoding="utf-8") as f:
            json.dump(backup, f, indent=2)
        print(f"Backup written to {backup_path}")

        db.query(ChatHistory).filter(ChatHistory.conversation_id.in_(conversation_ids)).delete(
            synchronize_session=False
        )
        db.query(Conversation).filter(Conversation.conversation_id.in_(conversation_ids)).delete(
            synchronize_session=False
        )
        db.commit()

        print(
            f"Deleted {len(legacy_conversations)} conversation(s) and "
            f"{len(legacy_messages)} message(s)."
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
