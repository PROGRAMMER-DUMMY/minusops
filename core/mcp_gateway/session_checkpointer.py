"""
session_checkpointer.py -- Stateful inter-agent session persistence and checkpointing.

Provides dual-tier state persistence:
1. Short-Term (Thread-Level): Serializes step state, message history, and pending tickets
   indexed by thread_id with TTL expiration (RedisSaver / in-memory fallback).
2. Long-Term Memory Store: Preserves cross-thread domain facts, preferences, and entity context.
"""
import time
from typing import Any, Dict, List, Optional


class SessionCheckpointer:
    def __init__(self, default_ttl_seconds: int = 3600):
        self.default_ttl = default_ttl_seconds
        self._threads: Dict[str, Dict[str, Any]] = {}
        self._long_term_memory: Dict[str, Dict[str, Any]] = {}

    def save_checkpoint(
        self,
        thread_id: str,
        step_index: int,
        state_data: Dict[str, Any],
        ttl_seconds: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Save a point-in-time state checkpoint for an execution thread.
        """
        now = time.time()
        ttl = ttl_seconds or self.default_ttl
        expires_at = now + ttl

        if thread_id not in self._threads:
            self._threads[thread_id] = {
                "thread_id": thread_id,
                "created_at": now,
                "updated_at": now,
                "expires_at": expires_at,
                "checkpoints": []
            }

        entry = {
            "step_index": step_index,
            "timestamp": now,
            "state": state_data
        }

        self._threads[thread_id]["updated_at"] = now
        self._threads[thread_id]["expires_at"] = expires_at
        self._threads[thread_id]["checkpoints"].append(entry)

        return entry

    def load_latest_checkpoint(self, thread_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve the latest valid checkpoint for a thread.
        """
        thread = self._threads.get(thread_id)
        if not thread:
            return None

        if time.time() > thread["expires_at"]:
            del self._threads[thread_id]
            return None

        checkpoints = thread.get("checkpoints", [])
        return checkpoints[-1] if checkpoints else None

    def store_long_term_fact(self, namespace: str, key: str, value: Any) -> None:
        """
        Store a permanent or cross-session domain memory.
        """
        if namespace not in self._long_term_memory:
            self._long_term_memory[namespace] = {}
        self._long_term_memory[namespace][key] = {
            "value": value,
            "updated_at": time.time()
        }

    def retrieve_long_term_fact(self, namespace: str, key: str) -> Optional[Any]:
        """
        Retrieve a long-term domain memory.
        """
        ns = self._long_term_memory.get(namespace, {})
        entry = ns.get(key)
        return entry["value"] if entry else None
