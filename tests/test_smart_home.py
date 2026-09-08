"""Test unitari per le skill domotiche (v5.7, skills/smart_home.py): un HomeAssistantClient
finto, nessuna vera chiamata di rete (gia' coperta da tests/test_home_assistant_client.py)."""
import unittest

from core.home_assistant_client import HomeAssistantError
from skills.smart_home import ControlSmartDeviceSkill, ListSmartDevicesSkill, _find_device

LIVING_ROOM_LIGHT = {"entity_id": "light.soggiorno", "state": "off", "attributes": {"friendly_name": "Luce soggiorno"}}
KITCHEN_SWITCH = {"entity_id": "switch.presa_cucina", "state": "on", "attributes": {"friendly_name": "Presa cucina"}}
STATES = [LIVING_ROOM_LIGHT, KITCHEN_SWITCH]


class FakeClient:
    def __init__(self, available=True, states=None, error=None, service_error=None):
        self._available = available
        self.states = states if states is not None else list(STATES)
        self.error = error
        self.service_error = service_error
        self.service_calls = []

    def is_available(self):
        return self._available

    def list_states(self, domain=None):
        if self.error is not None:
            raise self.error
        if domain:
            return [s for s in self.states if s["entity_id"].startswith(f"{domain}.")]
        return self.states

    def call_service(self, domain, service, entity_id=None, **extra):
        if self.service_error is not None:
            raise self.service_error
        self.service_calls.append((domain, service, entity_id, extra))
        return []


class FindDeviceTests(unittest.TestCase):
    def test_exact_friendly_name_match(self):
        self.assertIs(_find_device("Luce soggiorno", STATES), LIVING_ROOM_LIGHT)

    def test_case_insensitive_partial_match(self):
        self.assertIs(_find_device("soggiorno", STATES), LIVING_ROOM_LIGHT)

    def test_no_match_returns_none(self):
        self.assertIsNone(_find_device("bagno", STATES))

    def test_empty_name_returns_none(self):
        self.assertIsNone(_find_device("", STATES))


class ListSmartDevicesSkillTests(unittest.TestCase):
    def test_lists_all_devices_with_state(self):
        result = ListSmartDevicesSkill(FakeClient()).execute({})
        self.assertTrue(result.success)
        self.assertEqual(len(result.data["devices"]), 2)
        self.assertEqual(result.data["devices"][0]["name"], "Luce soggiorno")

    def test_filters_by_domain(self):
        result = ListSmartDevicesSkill(FakeClient()).execute({"domain": "light"})
        self.assertEqual([d["entity_id"] for d in result.data["devices"]], ["light.soggiorno"])

    def test_unavailable_client_is_reported(self):
        result = ListSmartDevicesSkill(FakeClient(available=False)).execute({})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "HOME_ASSISTANT_UNAVAILABLE")

    def test_home_assistant_error_is_reported(self):
        result = ListSmartDevicesSkill(FakeClient(error=HomeAssistantError("boom"))).execute({})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "HOME_ASSISTANT_ERROR")

    def test_no_devices_is_not_found(self):
        result = ListSmartDevicesSkill(FakeClient(states=[])).execute({})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "NOT_FOUND")


class ControlSmartDeviceSkillTests(unittest.TestCase):
    def test_turns_on_the_matched_device(self):
        client = FakeClient()
        result = ControlSmartDeviceSkill(client).execute({"name": "soggiorno", "action": "on"})

        self.assertTrue(result.success)
        self.assertEqual(client.service_calls, [("light", "turn_on", "light.soggiorno", {})])
        self.assertEqual(result.data["name"], "Luce soggiorno")

    def test_toggle_and_off_map_to_the_right_service(self):
        client = FakeClient()
        ControlSmartDeviceSkill(client).execute({"name": "presa cucina", "action": "off"})
        ControlSmartDeviceSkill(client).execute({"name": "presa cucina", "action": "toggle"})
        self.assertEqual([call[1] for call in client.service_calls], ["turn_off", "toggle"])

    def test_device_not_found(self):
        result = ControlSmartDeviceSkill(FakeClient()).execute({"name": "bagno", "action": "on"})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "NOT_FOUND")

    def test_invalid_action_is_missing_parameters(self):
        result = ControlSmartDeviceSkill(FakeClient()).execute({"name": "soggiorno", "action": "dance"})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_unavailable_client_never_lists_states(self):
        client = FakeClient(available=False)
        result = ControlSmartDeviceSkill(client).execute({"name": "soggiorno", "action": "on"})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "HOME_ASSISTANT_UNAVAILABLE")
        self.assertEqual(client.service_calls, [])

    def test_service_call_failure_is_reported(self):
        client = FakeClient(service_error=HomeAssistantError("boom"))
        result = ControlSmartDeviceSkill(client).execute({"name": "soggiorno", "action": "on"})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "HOME_ASSISTANT_ERROR")


if __name__ == "__main__":
    unittest.main()
