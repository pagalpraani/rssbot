# =============================================================================
# Module: Help Registry
# Path: utilitybot/utils/help_registry.py
# Description: Utility functions and helpers for operations related to Help Registry.
# Scope: channel | group
# =============================================================================

from typing import Dict, List, Tuple, Optional

class HelpRegistry:
    # Key -> (Name, HelpText, SupportedChatTypes)
    _registry: Dict[str, Tuple[str, str, List[str]]] = {}

    @classmethod
    def register(cls, name: str, key: str, help_text: str, supported_chat_types: Optional[List[str]] = None):
        """
        Registers a module for the help menu.
        :param name: Display name of the module (e.g. "Night Mode")
        :param key: Callback key (e.g. "night_mode")
        :param help_text: The help text content (HTML supported)
        :param supported_chat_types: List of supported chat types (e.g. ['group', 'channel']). Defaults to Group/Supergroup.
        """
        if supported_chat_types is None:
            supported_chat_types = ["group", "supergroup"]

        cls._registry[key] = (name, help_text, supported_chat_types)

    @classmethod
    def get_all(cls, chat_type: Optional[str] = None, exclude_type: Optional[str] = None) -> List[Tuple[str, str]]:
        """
        Returns a list of (name, key) tuples sorted alphabetically by name.
        - If chat_type is provided, only entries supporting that type are returned.
        - If exclude_type is provided, entries whose *only* supported type is
          exclude_type are left out (used to list every regular module while
          keeping the owner-only "dev" entries in their own section).
        """
        items = []
        for key, data in cls._registry.items():
            name, _, supported_types = data
            if chat_type:
                if chat_type in supported_types:
                    items.append((name, key))
            elif exclude_type:
                if supported_types != [exclude_type]:
                    items.append((name, key))
            else:
                items.append((name, key))

        return sorted(items, key=lambda x: x[0])

    @classmethod
    def get_help_text(cls, key: str) -> str:
        """
        Returns the help text for a given key.
        """
        data = cls._registry.get(key)
        if data:
            return data[1]
        return "No help text available."
