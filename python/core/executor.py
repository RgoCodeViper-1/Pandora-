import os
import subprocess
import sys
from typing import Any, Dict, List, Union

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from registry import ActionRegistry, PANDORAMode

# Import dynamic bridge to LLM client for intent fallbacks / Conversation Mode
try:
    from ai.llm_client import LLMClient
    llm_client = LLMClient()
except ImportError:
    llm_client = None


class Executor:
    def __init__(self):
        self.registry = ActionRegistry()

    def execute(self, result: Dict[str, Any]) -> Union[str, Dict[str, Any]]:
        """
        Legacy execution entry point. Safely retrieves handlers from ActionRegistry
        without raising an opaque TypeError if an intent is missing.
        """
        intent = result.get("intent", "unknown")
        entities = result.get("entities", result.get("slots", {}))

        action = self.registry.get(intent)
        if action is None:
            return f"I don't have an action registered for '{intent}' yet."

        return action(entities)

    def execute_intent(self, intent_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Routes and processes a single intent according to execution_type and PANDORA state.
        """
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
        if current_mode == PANDORAMode.CONVERSATION and intent_name not in ["set_mode", "system_status"]:
            return self._delegate_to_llm(slots.get("query", slots.get("raw_text", intent_name)), execution_type)

        # 3. EXECUTION TYPE DISPATCH (Sync, Async, Subprocess, LlmRouter)
        if execution_type in ["LlmRouter", "Dialogue"] or intent_name == "unknown":
            return self._delegate_to_llm(slots.get("query", slots.get("raw_text", intent_name)), execution_type)

        if execution_type == "Subprocess":
            cmd = slots.get("command")
            if cmd:
                try:
                    subprocess.Popen(cmd, shell=True)
                    return {
                        "status": "success",
                        "response": f"Launched subprocess detached: {cmd}",
                        "mode": self.registry.current_mode.value,
                        "execution_type": execution_type,
                    }
                except Exception as e:
                    return {"status": "error", "response": f"Subprocess launch failed: {str(e)}"}

        # Standard Registry Handler Execution
        res = self.registry.execute(intent_name, slots)
        res["execution_type"] = execution_type
        return res

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
