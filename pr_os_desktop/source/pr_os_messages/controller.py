from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable

from pr_os_desktop_service.control_store import ControlStore

from .contracts import MessageRecord, stable_message_sort
from .store import MessageStore, utc_now


LIST_MODES = frozenset({"CURRENT", "HISTORY", "STARRED"})
BULK_ACTIONS = frozenset({"STAR", "UNSTAR", "MARK_READ", "CONFIRM"})


class MessageActionError(ValueError):
    pass


class MessageController:
    def __init__(self, store: MessageStore, control_store: ControlStore | None = None):
        self.store = store
        self.control_store = control_store
        self.mode = "CURRENT"
        self.selected_message_id: str | None = None

    def _persist_ui_state(self) -> None:
        if self.control_store is None:
            return

        def mutate(state: dict[str, Any]) -> None:
            state["route"] = "messages"
            state["filters"]["messages.mode"] = self.mode
            state["filters"]["messages.selected_message_id"] = self.selected_message_id

        self.control_store.update(mutate)

    def restore_ui_state(self) -> dict[str, Any]:
        if self.control_store is None:
            return {"mode": self.mode, "selected_message_id": self.selected_message_id}
        state = self.control_store.load()["state"]
        mode = state["filters"].get("messages.mode", "CURRENT")
        selected = state["filters"].get("messages.selected_message_id")
        self.mode = mode if mode in LIST_MODES else "CURRENT"
        existing = {row.message_id for row in self.list_messages(self.mode)}
        self.selected_message_id = selected if isinstance(selected, str) and selected in existing else None
        if selected is not None and self.selected_message_id is None:
            self._persist_ui_state()
        return {"mode": self.mode, "selected_message_id": self.selected_message_id}

    def list_messages(self, mode: str | None = None, *, search: str = "") -> list[MessageRecord]:
        selected_mode = mode or self.mode
        if selected_mode not in LIST_MODES:
            raise MessageActionError("MESSAGE_LIST_MODE_INVALID")
        query = search.casefold().strip()
        rows = self.store.records()
        if selected_mode == "CURRENT":
            rows = [row for row in rows if row.collection_state == "CURRENT"]
        elif selected_mode == "HISTORY":
            rows = [row for row in rows if row.collection_state == "HISTORY"]
        else:
            rows = [row for row in rows if row.starred]
        if query:
            rows = [row for row in rows if query in f"{row.title}\n{row.summary}".casefold()]
        return stable_message_sort(rows)

    def switch_mode(self, mode: str) -> dict[str, Any]:
        if mode not in LIST_MODES:
            raise MessageActionError("MESSAGE_LIST_MODE_INVALID")
        self.mode = mode
        self.selected_message_id = None
        self._persist_ui_state()
        return {"mode": mode, "selected_message_id": None, "first_item_auto_selected": False}

    def open_detail(self, message_id: str) -> MessageRecord:
        record = self.store.get(message_id)
        if record.read_state == "UNREAD":
            stamp = utc_now()
            record = self.store.update_record(
                message_id,
                lambda raw: raw.update({"read_state": "READ", "read_at": stamp}),
            )
        self.selected_message_id = message_id
        self._persist_ui_state()
        return record

    def close_detail(self) -> dict[str, Any]:
        source = self.selected_message_id
        self.selected_message_id = None
        self._persist_ui_state()
        return {"closed_message_id": source, "confirmation_changed": False, "focus_return": source}

    def toggle_star(self, message_id: str) -> MessageRecord:
        record = self.store.get(message_id)
        next_value = not record.starred
        stamp = utc_now() if next_value else None
        return self.store.update_record(
            message_id,
            lambda raw: raw.update({"starred": next_value, "starred_at": stamp}),
        )

    def confirm(self, message_id: str, *, from_detail: bool = False) -> MessageRecord:
        record = self.store.get(message_id)
        if record.collection_state == "HISTORY" or record.confirmation_state == "CONFIRMED":
            raise MessageActionError("MESSAGE_ALREADY_ARCHIVED")
        if record.protected_bulk_read and not from_detail:
            raise MessageActionError("MESSAGE_PROTECTED_REQUIRES_DETAIL_CONFIRMATION")
        stamp = utc_now()
        return self.store.update_record(
            message_id,
            lambda raw: raw.update(
                {
                    "read_state": "READ",
                    "read_at": raw["read_at"] or stamp,
                    "confirmation_state": "CONFIRMED_PENDING_HISTORY",
                    "confirmed_at": stamp,
                }
            ),
        )

    def mark_all_read(self) -> dict[str, Any]:
        stamp = utc_now()

        def apply(state: dict[str, Any]) -> dict[str, Any]:
            eligible: list[str] = []
            protected: list[str] = []
            for message_id, raw in state["messages"].items():
                if raw["collection_state"] != "CURRENT" or raw["read_state"] != "UNREAD":
                    continue
                if not raw["protected_bulk_read"]:
                    eligible.append(message_id)
                    raw.update(
                        {
                            "read_state": "READ",
                            "read_at": stamp,
                            "confirmation_state": "CONFIRMED_PENDING_HISTORY",
                            "confirmed_at": stamp,
                        }
                    )
                    MessageRecord.from_mapping(raw)
                else:
                    protected.append(message_id)
            return {"processed": sorted(eligible), "protected": sorted(protected)}

        _, result = self.store.transaction(apply)
        return {
            "schema_version": "P08T07MarkAllReadReceipt-v1",
            "processed_count": len(result["processed"]),
            "protected_count": len(result["protected"]),
            **result,
            "task_state_changed": False,
            "status": "PASS",
        }

    def refresh_archive(self) -> dict[str, Any]:
        archived: list[str] = []

        def apply(state: dict[str, Any]) -> None:
            for message_id, raw in state["messages"].items():
                if raw["confirmation_state"] == "CONFIRMED_PENDING_HISTORY":
                    raw.update({"confirmation_state": "CONFIRMED", "collection_state": "HISTORY"})
                    MessageRecord.from_mapping(raw)
                    archived.append(message_id)

        self.store.transaction(apply)
        if self.selected_message_id in archived:
            self.selected_message_id = None
            self._persist_ui_state()
        return {
            "schema_version": "P08T07ArchiveProjectionReceipt-v1",
            "archived_count": len(archived),
            "archived": sorted(archived),
            "delete_performed": False,
            "star_state_changed": False,
            "status": "PASS",
        }

    def archive_confirmed(self, message_id: str) -> dict[str, Any]:
        """Move one user-confirmed message to history without deleting audit data."""

        record = self.store.get(message_id)
        if record.collection_state == "HISTORY" and record.confirmation_state == "CONFIRMED":
            return {
                "schema_version": "P08T07ExactArchiveProjectionReceipt-v1",
                "archived_count": 0,
                "archived": [],
                "already_archived": True,
                "delete_performed": False,
                "star_state_changed": False,
                "status": "PASS",
            }
        if (
            record.collection_state != "CURRENT"
            or record.confirmation_state != "CONFIRMED_PENDING_HISTORY"
        ):
            raise MessageActionError("MESSAGE_CONFIRMATION_REQUIRED_BEFORE_ARCHIVE")

        updated = self.store.update_record(
            message_id,
            lambda raw: raw.update(
                {"confirmation_state": "CONFIRMED", "collection_state": "HISTORY"}
            ),
        )
        if self.selected_message_id == message_id:
            self.selected_message_id = None
            self._persist_ui_state()
        return {
            "schema_version": "P08T07ExactArchiveProjectionReceipt-v1",
            "archived_count": 1,
            "archived": [updated.message_id],
            "already_archived": False,
            "delete_performed": False,
            "star_state_changed": False,
            "status": "PASS",
        }

    def bulk_apply(self, message_ids: Iterable[str], action: str) -> dict[str, Any]:
        if action not in BULK_ACTIONS:
            raise MessageActionError("MESSAGE_BULK_ACTION_INVALID")
        selected = list(dict.fromkeys(message_ids))
        stamp = utc_now()

        def apply(state: dict[str, Any]) -> dict[str, list[str]]:
            success: list[str] = []
            skipped: list[str] = []
            failed: list[str] = []
            for message_id in selected:
                raw = state["messages"].get(message_id)
                if raw is None:
                    failed.append(message_id)
                    continue
                eligible = True
                if action in {"MARK_READ", "CONFIRM"}:
                    eligible = (
                        raw["collection_state"] == "CURRENT"
                        and not raw["protected_bulk_read"]
                    )
                if not eligible:
                    skipped.append(message_id)
                    continue
                if action == "STAR":
                    raw.update({"starred": True, "starred_at": raw["starred_at"] or stamp})
                elif action == "UNSTAR":
                    raw.update({"starred": False, "starred_at": None})
                elif action == "MARK_READ":
                    raw.update({"read_state": "READ", "read_at": raw["read_at"] or stamp})
                else:
                    raw.update(
                        {
                            "read_state": "READ",
                            "read_at": raw["read_at"] or stamp,
                            "confirmation_state": "CONFIRMED_PENDING_HISTORY",
                            "confirmed_at": raw["confirmed_at"] or stamp,
                        }
                    )
                MessageRecord.from_mapping(raw)
                success.append(message_id)
            return {"success": success, "skipped": skipped, "failed": failed}

        _, result = self.store.transaction(apply)
        return {
            "schema_version": "P08T07BulkMessageActionReceipt-v1",
            "action": action,
            "selected_count": len(selected),
            "success_count": len(result["success"]),
            "skipped_count": len(result["skipped"]),
            "failed_count": len(result["failed"]),
            **{key: sorted(value) for key, value in result.items()},
            "task_state_changed": False,
            "status": "PASS" if not result["failed"] else "PARTIAL",
        }

    def unread_count(self) -> int:
        return sum(
            row.collection_state == "CURRENT" and row.read_state == "UNREAD"
            for row in self.store.records()
        )

    def aggregate_action_state(self) -> str:
        current = [
            row
            for row in self.store.records()
            if row.collection_state == "CURRENT" and row.confirmation_state == "UNCONFIRMED"
        ]
        if any(row.severity == "RED" for row in current):
            return "RED"
        if any(row.severity == "YELLOW" for row in current):
            return "YELLOW"
        return "NONE"


__all__ = ["BULK_ACTIONS", "LIST_MODES", "MessageActionError", "MessageController"]
