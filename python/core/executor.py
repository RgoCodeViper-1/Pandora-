from core.registry import ActionRegistry


class Executor:

    def __init__(self):

        self.registry = ActionRegistry()


    def execute(
        self,
        result
    ):

        intent = result.get(
            "intent",
            "unknown"
        )

        entities = result.get(
            "entities",
            {}
        )

        action = self.registry.get(
            intent
        )

        if action is None:
            # Not yet wired in ActionRegistry — surface this clearly instead
            # of letting `None(entities)` raise an opaque TypeError. Matters
            # more now that MultiIntentDispatcher runs several intents per
            # utterance concurrently: a silent crash here would otherwise
            # just show up as an unlabelled error in the batch.
            return f"I don't have an action registered for '{intent}' yet."

        return action(
            entities
        )