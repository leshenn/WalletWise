from __future__ import annotations

import json
import re
import uuid
from typing import Any

from .database import Database
from .wally_router import SmartLocalRouter


class AIChatRepository:
    """Persist complete Wally conversations locally while exposing only compact AI memory.

    Full messages remain in SQLite so users can reopen old chats. Groq does not receive
    the full saved conversation. For follow-ups, WalletWise builds a small conversation
    structured summary plus only the most recent exchange when needed.
    """

    MEMORY_VERSION = 3
    TITLE_LIMIT = 34
    SUMMARY_LIMIT = 120
    MEMORY_USER_CHAR_LIMIT = 420
    MEMORY_ASSISTANT_CHAR_LIMIT = 520
    MEMORY_EXCHANGE_LIMIT = 3

    def __init__(self, database: Database):
        self.database = database
        self._backfill_saved_chat_metadata()

    def _backfill_saved_chat_metadata(self) -> None:
        """Give chats created by older MVP builds a summary/context without an AI call."""
        with self.database.connect() as connection:
            chats = connection.execute(
                "SELECT id, title, summary, context_json FROM ai_chats"
            ).fetchall()

            for chat in chats:
                summary_empty = not str(chat["summary"] or "").strip()
                raw_context = str(chat["context_json"] or "{}").strip()
                try:
                    parsed_context = json.loads(raw_context or "{}")
                except json.JSONDecodeError:
                    parsed_context = {}
                context_version = (
                    parsed_context.get("memoryVersion")
                    if isinstance(parsed_context, dict)
                    else None
                )
                context_needs_rebuild = (
                    raw_context in {"", "{}"}
                    or context_version != self.MEMORY_VERSION
                )
                title_needs_refresh = len(str(chat["title"] or "")) > self.TITLE_LIMIT
                if not summary_empty and not context_needs_rebuild and not title_needs_refresh:
                    continue

                first_user = connection.execute(
                    """
                    SELECT content FROM ai_chat_messages
                    WHERE chat_id = ? AND role = 'user'
                    ORDER BY id ASC LIMIT 1
                    """,
                    (chat["id"],),
                ).fetchone()
                first_assistant = connection.execute(
                    """
                    SELECT answer_json FROM ai_chat_messages
                    WHERE chat_id = ? AND role = 'assistant'
                    ORDER BY id ASC LIMIT 1
                    """,
                    (chat["id"],),
                ).fetchone()
                latest_user = connection.execute(
                    """
                    SELECT content FROM ai_chat_messages
                    WHERE chat_id = ? AND role = 'user'
                    ORDER BY id DESC LIMIT 1
                    """,
                    (chat["id"],),
                ).fetchone()
                latest_assistant = connection.execute(
                    """
                    SELECT answer_json FROM ai_chat_messages
                    WHERE chat_id = ? AND role = 'assistant'
                    ORDER BY id DESC LIMIT 1
                    """,
                    (chat["id"],),
                ).fetchone()

                if latest_assistant is None:
                    continue

                try:
                    latest_answer = json.loads(latest_assistant["answer_json"] or "{}")
                except json.JSONDecodeError:
                    latest_answer = {}
                if not isinstance(latest_answer, dict) or not latest_answer:
                    continue

                title = str(chat["title"] or "New chat")
                if first_user:
                    first_question = str(first_user["content"] or "")
                    # Version migration deliberately regenerates a short topic name.
                    # This prevents old full-question titles from overflowing the sidebar.
                    if context_needs_rebuild or title_needs_refresh:
                        title = self._fallback_title_from_question(first_question)
                    elif first_assistant:
                        try:
                            first_answer = json.loads(first_assistant["answer_json"] or "{}")
                        except json.JSONDecodeError:
                            first_answer = {}
                        if isinstance(first_answer, dict):
                            title = self._safe_title_from_answer(first_answer, first_question)

                latest_question = str(latest_user["content"] or "") if latest_user else ""
                display_summary = (
                    self._sidebar_summary(latest_question, latest_answer)
                    if context_needs_rebuild or summary_empty or len(str(chat["summary"] or "")) > self.SUMMARY_LIMIT
                    else str(chat["summary"] or "")
                )
                if context_needs_rebuild:
                    # Old memory is intentionally NOT merged into the rebuilt state.
                    # The latest explicit user question becomes the new active topic.
                    memory = self._memory_from_answer(
                        title=title,
                        question=latest_question,
                        answer=latest_answer,
                    )
                    # Do not carry an old build's potentially incorrect conclusion
                    # into future reasoning. Keep only the rebuilt topic/state.
                    memory["latestSummary"] = display_summary
                    memory["status"] = None
                    memory["keyMetrics"] = []
                    memory["nextStep"] = None
                else:
                    memory = parsed_context if isinstance(parsed_context, dict) else {}

                connection.execute(
                    """
                    UPDATE ai_chats
                    SET title = ?, summary = ?, context_json = ?
                    WHERE id = ?
                    """,
                    (
                        title,
                        display_summary,
                        json.dumps(memory, ensure_ascii=False),
                        chat["id"],
                    ),
                )

    @classmethod
    def _fallback_title_from_question(cls, question: str) -> str:
        clean = " ".join((question or "New chat").split()).strip() or "New chat"
        lower = clean.casefold()
        subject_match = SmartLocalRouter._extract_subject(lower)
        subject = (subject_match or "").replace("_", " ").title()

        # Prefer short semantic topic names over clipped copies of the question.
        if any(word in lower for word in ("contract", "instalment", "installment")) and subject:
            return f"{subject} contract decision"[: cls.TITLE_LIMIT]
        if any(marker in lower for marker in (
            "can i afford", "could i afford", "financial pressure", "financial decision",
            "should i buy", "would buying", "too expensive", "worth buying",
        )):
            return (f"{subject} affordability" if subject else "Purchase affordability")[: cls.TITLE_LIMIT]
        if re.search(r"\bsav(?:e|ing)\b", lower) and subject:
            return f"{subject} savings plan"[: cls.TITLE_LIMIT]
        if "why did i spend" in lower or "spent more" in lower or "spending change" in lower:
            return "Spending change review"
        if re.search(r"\bhow much\b.*\bsp(?:end|ent|ending)\b", lower):
            month = re.search(r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\b", lower)
            return (f"{month.group(1).title()} spending" if month else "Spending summary")
        if "reduce" in lower and ("spend" in lower or "expense" in lower):
            return "Spending reduction plan"
        if "budget" in lower:
            return "Budget planning"
        if "salary" in lower or "income" in lower:
            return "Income review"

        compact = clean.rstrip("?.!")
        if len(compact) <= cls.TITLE_LIMIT:
            return compact
        return compact[: cls.TITLE_LIMIT - 1].rstrip() + "…"

    @classmethod
    def _safe_title_from_answer(cls, answer: dict[str, Any], question: str) -> str:
        # Chat names should describe the topic, not repeat a long AI conclusion.
        # This makes the saved-chat rail predictable and keeps every name within width.
        semantic = cls._fallback_title_from_question(question)
        if semantic and semantic != "New chat":
            return semantic
        candidate = " ".join(str(answer.get("title") or "").split()).strip().rstrip(".")
        if candidate:
            return candidate[: cls.TITLE_LIMIT].rstrip()
        return "New chat"

    @classmethod
    def _display_summary(cls, answer: dict[str, Any]) -> str:
        value = " ".join(str(answer.get("summary") or "").split()).strip()
        if not value:
            value = " ".join(str(answer.get("next_step") or "").split()).strip()
        if len(value) <= cls.SUMMARY_LIMIT:
            return value
        return value[: cls.SUMMARY_LIMIT - 1].rstrip() + "…"

    @classmethod
    def _sidebar_summary(cls, question: str, answer: dict[str, Any]) -> str:
        """Create a short stable topic preview for the narrow saved-chat rail."""
        decision = SmartLocalRouter.route(question, {})
        entities = decision.entities or {}
        subject = str(entities.get("subject") or "").replace("_", " ").strip()

        def money(value: Any) -> str:
            try:
                number = float(value)
            except (TypeError, ValueError):
                return ""
            return f"R{number:,.0f}" if number.is_integer() else f"R{number:,.2f}"

        if decision.intent == "affordability":
            if entities.get("recurringPayment") is not None:
                label = subject or "purchase"
                return f"{money(entities['recurringPayment'])} monthly {label} commitment"[: cls.SUMMARY_LIMIT]
            if entities.get("purchasePrice") is not None:
                label = subject or "purchase"
                return f"{money(entities['purchasePrice'])} {label} affordability"[: cls.SUMMARY_LIMIT]

        if decision.intent in {"saving_goal_calculation", "saving_goal_missing_info", "saving_goal"}:
            target = money(entities.get("targetAmount"))
            months = entities.get("timeframeMonths")
            label = subject or "savings"
            if target and months:
                return f"{target} {label} goal over {months} months"[: cls.SUMMARY_LIMIT]
            if target:
                return f"{target} {label} savings goal"[: cls.SUMMARY_LIMIT]

        return cls._display_summary(answer)

    @classmethod
    def _extract_structured_state(cls, question: str) -> dict[str, Any]:
        """Extract compact machine-readable conversation state without an AI call."""
        decision = SmartLocalRouter.route(question, {})
        entities = decision.entities or {}
        state: dict[str, Any] = {}

        is_goal_state = decision.intent in {"saving_goal_calculation", "saving_goal_missing_info", "saving_goal"}
        if is_goal_state and entities.get("targetAmount") is not None:
            state["targetAmount"] = entities["targetAmount"]
            # Keep the legacy key only inside active savings-goal state.
            state["amount"] = entities["targetAmount"]
        if is_goal_state and entities.get("timeframeMonths") is not None:
            state["timeframeMonths"] = entities["timeframeMonths"]
        if is_goal_state and entities.get("existingSavings") is not None:
            state["existingSavings"] = entities["existingSavings"]
        if is_goal_state and entities.get("periodicContribution") is not None:
            state["periodicContribution"] = entities["periodicContribution"]
            state["contributionFrequency"] = entities.get("contributionFrequency") or "month"
        if entities.get("subject"):
            state["subject"] = entities["subject"]
        if decision.intent == "affordability" and entities.get("purchasePrice") is not None:
            state["purchasePrice"] = entities["purchasePrice"]
        if decision.intent == "affordability" and entities.get("recurringPayment") is not None:
            state["recurringPayment"] = entities["recurringPayment"]
            state["paymentFrequency"] = entities.get("paymentFrequency") or "month"

        intent_map = {
            "saving_goal_calculation": "saving_goal",
            "saving_goal_missing_info": "saving_goal",
            "spending_total": "spending_analysis",
            "spending_comparison": "spending_analysis",
        }
        state["intent"] = intent_map.get(decision.intent, decision.intent)
        # Financial query context: essential for follow-ups such as
        # "How much did I spend on groceries in July?" -> "What about June?"
        # without incorrectly applying old filters to standalone questions.
        from .wally_query_plan import DeterministicQueryPlanner
        plan = DeterministicQueryPlanner.build(question, {})
        # A pure follow-up might lack a measure, but its new date still matters.
        explicit_period = DeterministicQueryPlanner._extract_period_text(SmartLocalRouter.normalize(question))
        if explicit_period:
            state["period"] = explicit_period
        if plan.measure in {"spending", "income", "savings", "transactions"}:
            state["measure"] = plan.measure
            state["operation"] = plan.operation
            state["groupBy"] = plan.group_by
            if plan.period:
                state["period"] = plan.period
            if plan.entities.get("category"):
                state["category"] = plan.entities["category"]
        return state

    @classmethod
    def _memory_from_answer(
        cls,
        *,
        title: str,
        question: str,
        answer: dict[str, Any],
    ) -> dict[str, Any]:
        metrics: list[dict[str, Any]] = []
        for item in (answer.get("key_metrics") or [])[:4]:
            if not isinstance(item, dict):
                continue
            metrics.append({
                "label": item.get("label"),
                "value": item.get("value"),
                "change": item.get("change"),
            })

        state = cls._extract_structured_state(question)
        return {
            "memoryVersion": cls.MEMORY_VERSION,
            "topic": title,
            "activeTopic": state.get("subject") or state.get("intent") or title,
            "state": state,
            "latestQuestion": " ".join(question.split())[:180],
            "latestSummary": str(answer.get("summary") or "")[:260],
            "status": answer.get("status"),
            "keyMetrics": metrics[:3],
            "nextStep": str(answer.get("next_step") or "")[:180] or None,
        }

    @classmethod
    def _compact_answer_for_history(cls, answer: dict[str, Any]) -> str:
        metrics = []
        for item in (answer.get("key_metrics") or [])[:3]:
            if isinstance(item, dict):
                metrics.append({
                    "label": item.get("label"),
                    "value": item.get("value"),
                    "change": item.get("change"),
                })

        compact = {
            "summary": answer.get("summary"),
            "status": answer.get("status"),
            "key_metrics": metrics[:2],
            "next_step": answer.get("next_step"),
        }
        return json.dumps(compact, ensure_ascii=False, separators=(",", ":"))[
            : cls.MEMORY_ASSISTANT_CHAR_LIMIT
        ]

    def create(self, first_question: str) -> dict[str, Any]:
        chat_id = f"chat-{uuid.uuid4().hex[:16]}"
        title = self._fallback_title_from_question(first_question)
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO ai_chats (id, title, summary, context_json)
                VALUES (?, ?, '', '{}')
                """,
                (chat_id, title),
            )
        return self.get_summary(chat_id)

    def exists(self, chat_id: str) -> bool:
        with self.database.connect() as connection:
            row = connection.execute("SELECT 1 FROM ai_chats WHERE id = ?", (chat_id,)).fetchone()
        return row is not None

    def ensure(self, chat_id: str | None, first_question: str) -> dict[str, Any]:
        if chat_id:
            if not self.exists(chat_id):
                raise KeyError(chat_id)
            return self.get_summary(chat_id)
        return self.create(first_question)

    def append_exchange(
        self,
        *,
        chat_id: str,
        question: str,
        answer: dict[str, Any],
        data_used: dict[str, Any] | None,
        model: str,
    ) -> None:
        with self.database.connect() as connection:
            chat_row = connection.execute(
                "SELECT title, summary, context_json FROM ai_chats WHERE id = ?",
                (chat_id,),
            ).fetchone()
            if chat_row is None:
                raise KeyError(chat_id)

            existing_message_count = int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM ai_chat_messages WHERE chat_id = ?",
                    (chat_id,),
                ).fetchone()["count"]
                or 0
            )

            connection.execute(
                "INSERT INTO ai_chat_messages (chat_id, role, content) VALUES (?, 'user', ?)",
                (chat_id, question.strip()),
            )
            connection.execute(
                """
                INSERT INTO ai_chat_messages (
                    chat_id, role, content, answer_json, data_used_json, model
                ) VALUES (?, 'assistant', '', ?, ?, ?)
                """,
                (
                    chat_id,
                    json.dumps(answer, ensure_ascii=False),
                    json.dumps(data_used or {}, ensure_ascii=False),
                    model or "",
                ),
            )

            title = chat_row["title"]
            if existing_message_count == 0:
                title = self._safe_title_from_answer(answer, question)

            summary = self._sidebar_summary(question, answer)
            context = self._memory_from_answer(
                title=title,
                question=question,
                answer=answer,
            )
            # Preserve structured goal state across short follow-ups such as
            # "What if I make it R70,000?" without re-sending old chat text.
            try:
                previous_context = json.loads(chat_row["context_json"] or "{}")
            except json.JSONDecodeError:
                previous_context = {}
            previous_state = previous_context.get("state") if isinstance(previous_context, dict) else {}
            previous_version = previous_context.get("memoryVersion") if isinstance(previous_context, dict) else None
            current_state = context.get("state") or {}
            is_follow_up = SmartLocalRouter.is_contextual_followup(question)
            if (
                is_follow_up
                and previous_version == self.MEMORY_VERSION
                and isinstance(previous_state, dict)
            ):
                # Follow-ups may inherit missing values, but the current question wins.
                context["state"] = {**previous_state, **current_state}
                older_period = previous_state.get("period")
                newer_period = current_state.get("period")
                if older_period and newer_period and older_period != newer_period:
                    context["state"]["previousPeriod"] = older_period
            else:
                # Standalone questions start a fresh active topic. Old goal state must
                # never contaminate a complete new question in the same saved chat.
                context["state"] = current_state
            context["memoryVersion"] = self.MEMORY_VERSION
            context["activeTopic"] = (context.get("state") or {}).get("subject") or (context.get("state") or {}).get("intent") or title

            connection.execute(
                """
                UPDATE ai_chats
                SET title = ?,
                    summary = ?,
                    context_json = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    title,
                    summary,
                    json.dumps(context, ensure_ascii=False),
                    chat_id,
                ),
            )

    def list_chats(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT c.id, c.title, c.summary, c.created_at, c.updated_at,
                       COUNT(m.id) AS message_count
                FROM ai_chats c
                LEFT JOIN ai_chat_messages m ON m.chat_id = c.id
                GROUP BY c.id
                ORDER BY c.updated_at DESC, c.created_at DESC
                """
            ).fetchall()
        return [
            {
                "id": row["id"],
                "title": row["title"],
                "summary": row["summary"] or "",
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
                "messageCount": int(row["message_count"] or 0),
            }
            for row in rows
        ]

    def get_summary(self, chat_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT c.id, c.title, c.summary, c.created_at, c.updated_at,
                       COUNT(m.id) AS message_count
                FROM ai_chats c
                LEFT JOIN ai_chat_messages m ON m.chat_id = c.id
                WHERE c.id = ?
                GROUP BY c.id
                """,
                (chat_id,),
            ).fetchone()
        if row is None:
            raise KeyError(chat_id)
        return {
            "id": row["id"],
            "title": row["title"],
            "summary": row["summary"] or "",
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "messageCount": int(row["message_count"] or 0),
        }

    def get(self, chat_id: str) -> dict[str, Any]:
        summary = self.get_summary(chat_id)
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, role, content, answer_json, data_used_json, model, created_at
                FROM ai_chat_messages
                WHERE chat_id = ?
                ORDER BY id ASC
                """,
                (chat_id,),
            ).fetchall()

        messages = []
        for row in rows:
            if row["role"] == "user":
                messages.append({
                    "id": row["id"],
                    "role": "user",
                    "content": row["content"],
                    "createdAt": row["created_at"],
                })
                continue

            answer = {}
            data_used = {}
            try:
                answer = json.loads(row["answer_json"] or "{}")
            except json.JSONDecodeError:
                pass
            try:
                data_used = json.loads(row["data_used_json"] or "{}")
            except json.JSONDecodeError:
                pass
            messages.append({
                "id": row["id"],
                "role": "assistant",
                "answer": answer,
                "dataUsed": data_used,
                "model": row["model"],
                "createdAt": row["created_at"],
            })

        return {**summary, "messages": messages}

    def get_ai_memory(self, chat_id: str) -> tuple[dict[str, Any], list[dict[str, str]]]:
        """Return a tiny AI-facing memory instead of the full saved conversation."""
        with self.database.connect() as connection:
            chat = connection.execute(
                "SELECT title, summary, context_json FROM ai_chats WHERE id = ?",
                (chat_id,),
            ).fetchone()
            if chat is None:
                raise KeyError(chat_id)

            rows = connection.execute(
                """
                SELECT role, content, answer_json
                FROM ai_chat_messages
                WHERE chat_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (chat_id, self.MEMORY_EXCHANGE_LIMIT * 2),
            ).fetchall()

        try:
            summary = json.loads(chat["context_json"] or "{}")
        except json.JSONDecodeError:
            summary = {}

        if not summary and (chat["summary"] or chat["title"]):
            summary = {
                "topic": chat["title"],
                "latestSummary": chat["summary"] or "",
            }

        history: list[dict[str, str]] = []
        for row in reversed(rows):
            if row["role"] == "user":
                content = " ".join(str(row["content"] or "").split()).strip()
                if content:
                    history.append({
                        "role": "user",
                        "content": content[: self.MEMORY_USER_CHAR_LIMIT],
                    })
                continue

            try:
                answer = json.loads(row["answer_json"] or "{}")
            except json.JSONDecodeError:
                answer = {}
            if answer:
                history.append({
                    "role": "assistant",
                    "content": self._compact_answer_for_history(answer),
                })

        return summary, history

    def rename(self, chat_id: str, title: str) -> dict[str, Any]:
        clean = " ".join((title or "").split()).strip()
        if not clean:
            raise ValueError("Chat title cannot be empty.")
        clean = clean[: self.TITLE_LIMIT]
        with self.database.connect() as connection:
            cursor = connection.execute(
                "UPDATE ai_chats SET title = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (clean, chat_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(chat_id)
        return self.get_summary(chat_id)

    def delete(self, chat_id: str) -> None:
        with self.database.connect() as connection:
            cursor = connection.execute("DELETE FROM ai_chats WHERE id = ?", (chat_id,))
            if cursor.rowcount == 0:
                raise KeyError(chat_id)
