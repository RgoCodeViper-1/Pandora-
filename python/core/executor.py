import subprocess
from typing import Any, Dict, List, Union

try:
    from .registry import ActionRegistry, PANDORAMode
except ImportError:
    from registry import ActionRegistry, PANDORAMode
from core.config import get_config
from core.guards import ExecutionGuard, GuardViolation
from core.locks import ExecutionLockManager, LockTimeoutError
from core.dialogue import DialogueManager

# Import dynamic bridge to LLM client for intent fallbacks / Conversation Mode
try:
    from ai.llm_client import LLMClient
    llm_client = LLMClient()
except ImportError:
    llm_client = None


class Executor:
    def __init__(self):
        self.registry = ActionRegistry()
        self.config = get_config()
        self.guard = ExecutionGuard(self.config)
        timeout = self.config.get("execution.lockTimeoutSeconds", 10)
        self.locks = ExecutionLockManager(float(timeout))
        self.dialogue = DialogueManager()

    def execute(self, result: Dict[str, Any]) -> Union[str, Dict[str, Any]]:
        """
        Legacy execution entry point. Safely retrieves handlers from ActionRegistry
        without raising an opaque TypeError if an intent is missing.
        """
        intent = result.get("intent", "unknown")
        entities = result.get("entities", result.get("slots", {}))
        try:
            self.guard.require_intent(result)
            with self.locks.for_intent(intent, entities):
                if (
                    self.registry.current_mode == PANDORAMode.OPTIMISED_IDLE
                    and intent not in self.registry.IDLE_ALLOWED
                ):
                    return {
                        "status": "ignored",
                        "response": "PANDORA is in Optimised Idle mode. Say 'Wake Up' to activate.",
                        "mode": self.registry.current_mode.value,
                    }
                action = self.registry.get(intent)
                if action is None:
                    return f"I don't have an action registered for '{intent}' yet."
                response = action(entities)
                self.registry.finish_interaction(result)
                return self.dialogue.format_result(response)
        except (GuardViolation, LockTimeoutError) as exc:
            return {"status": "blocked", "response": str(exc), "intent": intent}

    def execute_intent(self, intent_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Routes and processes a single intent according to execution_type and PANDORA state.
        """
        try:
            self.guard.require_intent(intent_data)
        except GuardViolation as exc:
            return {
                "status": "blocked",
                "response": str(exc),
                "intent": intent_data.get("intent", "unknown"),
            }

        intent_name = intent_data.get("intent", "unknown")
        slots = intent_data.get("slots", intent_data.get("entities", {}))
        try:
            with self.locks.for_intent(intent_name, slots):
                return self._execute_intent_unlocked(intent_data)
        except LockTimeoutError as exc:
            return {
                "status": "blocked",
                "response": str(exc),
                "intent": intent_name,
            }

    def _execute_intent_unlocked(self, intent_data: Dict[str, Any]) -> Dict[str, Any]:
        execution_type = intent_data.get("execution_type", "Sync")
        intent_name = intent_data.get("intent", "unknown")
        slots = intent_data.get("slots", intent_data.get("entities", {}))

        current_mode = self.registry.current_mode

        # 1. OPTIMISED IDLE MODE GATEWAY
        if current_mode == PANDORAMode.OPTIMISED_IDLE:
            if intent_name in ["wake_up", "set_mode"]:
                return self.registry.execute(intent_name, slots)
            return {
                "status": "ignored",
                "response": "PANDORA is in Optimised Idle mode. Say 'Wake Up' to activate.",
                "mode": current_mode.value,
            }

        # 2. CONVERSATION MODE ROUTING GATEWAY
        category = str(intent_data.get("category", "")).casefold()
        is_dialogue = category in {"dialogue", "conversation"} or str(execution_type).casefold() in {
            "dialogue",
            "llmrouter",
        }
        if current_mode == PANDORAMode.CONVERSATION and intent_name not in ["set_mode", "system_status"]:
            result = self._delegate_to_llm(
                self._prompt_for_llm(intent_data, slots),
                execution_type,
            )
            self.registry.finish_interaction(intent_data)
            result["mode"] = self.registry.current_mode.value
            return result

        # 3. EXECUTION TYPE DISPATCH (Sync, Async, Subprocess, LlmRouter)
        if is_dialogue or (intent_name == "unknown" and current_mode == PANDORAMode.CONVERSATION):
            result = self._delegate_to_llm(
                self._prompt_for_llm(intent_data, slots),
                execution_type,
            )
            self.registry.finish_interaction(intent_data)
            result["mode"] = self.registry.current_mode.value
            return result

        if execution_type == "Subprocess":
            cmd = slots.get("command")
            if cmd:
                try:
                    subprocess.Popen(cmd, shell=True)
                    result = {
                        "status": "success",
                        "response": f"Launched subprocess detached: {cmd}",
                        "mode": self.registry.current_mode.value,
                        "execution_type": execution_type,
                    }
                    self.registry.finish_interaction(intent_data)
                    result["mode"] = self.registry.current_mode.value
                    return result
                except Exception as e:
                    return {"status": "error", "response": f"Subprocess launch failed: {str(e)}"}

        # Standard Registry Handler Execution
        res = self.registry.execute(intent_name, slots)
        res["execution_type"] = execution_type
        self.registry.finish_interaction(intent_data)
        res["mode"] = self.registry.current_mode.value
        return res

    @staticmethod
    def _prompt_for_llm(intent_data: Dict[str, Any], slots: Dict[str, Any]) -> str:
        return str(
            slots.get(
                "query",
                slots.get("raw_text", intent_data.get("text", intent_data.get("transcript", ""))),
            )
        ).strip()

    def _delegate_to_llm(self, prompt: str, execution_type: str) -> Dict[str, Any]:
        """Directs payload processing to the PANDORA LLM pipeline."""
        if llm_client:
            llm_res = llm_client.query(prompt)
            return {
                "status": "success",
                "response": llm_res,
                "mode": self.registry.current_mode.value,
                "execution_type": execution_type,
            }
        return {
            "status": "fallback",
            "response": f"Registry path unhandled and LLM Client unreachable for prompt: '{prompt}'",
            "mode": self.registry.current_mode.value,
        }

    def execute_batch(self, payload: Union[Dict[str, Any], List[Dict[str, Any]]]) -> Dict[str, Any]:
        """
        Sequentially executes multi-intent chains within a single request context.
        """
        if isinstance(payload, dict):
            intents = payload.get("intents", [payload])
        else:
            intents = payload

        if not intents:
            return {"status": "empty", "response": "No intents provided in payload."}

        batch_results = []
        response_messages = []

        for intent in intents:
            res = self.execute_intent(intent)
            batch_results.append(res)
            if isinstance(res, dict) and res.get("response"):
                response_messages.append(res["response"])
            elif isinstance(res, str):
                response_messages.append(res)

        return {
            "status": "success",
            "batch_results": batch_results,
            "response": " | ".join(response_messages),
            "mode": self.registry.current_mode.value,
        }