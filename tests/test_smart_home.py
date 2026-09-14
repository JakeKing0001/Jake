"""Test unitari per le skill domotiche (v5.7, skills/smart_home.py): un HomeAssistantClient
finto, nessuna vera chiamata di rete (gia' coperta da tests/test_home_assistant_client.py)."""
import unittest
from unittest import mock

from core.home_assistant_client import HomeAssistantError
from skills.smart_home import ControlSmartDeviceSkill, ListSmartDevicesSkill, _find_device

LIVING_ROOM_LIGHT = {"entity_id": "light.soggiorno", "state": "off", "attributes": {"friendly_name": "Luce soggiorno"}}
KITCHEN_SWITCH = {"entity_id": "switch.presa_cucina", "state": "on", "attributes": {"friendly_name": "Presa cucina"}}
STATES = [LIVING_ROOM_LIGHT, KITCHEN_SWITCH]


class FakeClient:
    """F1.3.2 ("prove forti per... casa"): `responds` simula se il dispositivo FISICO conferma
    davvero il nuovo stato dopo call_service() (il caso comune) o resta fermo al vecchio stato
    (spento/scollegato/non risponde) - `call_service()` accettata senza sollevare non basta piu' a
    dichiarare successo, vedi ControlSmartDeviceSkill._wait_until_state_matches(). `states` viene
    sempre copiato (dizionari inclusi, non solo la lista) perche' `call_service()` con
    `responds=True` MUTA lo stato dell'entita' - senza una copia profonda, due FakeClient diversi
    nello stesso test-run muterebbero gli stessi dict globali LIVING_ROOM_LIGHT/KITCHEN_SWITCH,
    facendo trapelare lo stato da un test al successivo."""

    def __init__(self, available=True, states=None, error=None, service_error=None, get_state_error=None, responds=True):
        self._available = available
        self.states = [dict(state) for state in (states if states is not None else STATES)]
        self.error = error
        self.service_error = service_error
        self.get_state_error = get_state_error
        self.responds = responds
        self.service_calls = []

    def is_available(self):
        return self._available

    def list_states(self, domain=None):
        if self.error is not None:
            raise self.error
        if domain:
            return [s for s in self.states if s["entity_id"].startswith(f"{domain}.")]
        return self.states

    def get_state(self, entity_id):
        if self.get_state_error is not None:
            raise self.get_state_error
        for state in self.states:
            if state["entity_id"] == entity_id:
                return state
        return {"entity_id": entity_id, "state": "unknown"}

    def call_service(self, domain, service, entity_id=None, **extra):
        if self.service_error is not None:
            raise self.service_error
        self.service_calls.append((domain, service, entity_id, extra))
        if self.responds and entity_id:
            for state in self.states:
                if state["entity_id"] == entity_id:
                    if service == "turn_on":
                        state["state"] = "on"
                    elif service == "turn_off":
                        state["state"] = "off"
                    elif service == "toggle":
                        state["state"] = "off" if state["state"] == "on" else "on"
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


class ControlSmartDeviceIndependentVerificationTests(unittest.TestCase):
    """F1.3.2 ("prove forti per... casa"): call_service() accettata da Home Assistant non
    significa che il dispositivo FISICO abbia davvero cambiato stato - stesso identico buco gia'
    trovato e corretto per le finestre (CLOSE_WINDOW) e i processi (KILL_PROCESS_BY_PORT) in
    questa sessione. STATE_WAIT_SECONDS reale sarebbe 3s: qui ridotto perche' il test "il
    dispositivo non risponde" deve davvero aspettare la scadenza, non solo leggerla a codice."""

    def setUp(self):
        wait_patcher = mock.patch.object(ControlSmartDeviceSkill, "STATE_WAIT_SECONDS", 0.05)
        interval_patcher = mock.patch.object(ControlSmartDeviceSkill, "_POLL_INTERVAL_SECONDS", 0.01)
        wait_patcher.start()
        interval_patcher.start()
        self.addCleanup(wait_patcher.stop)
        self.addCleanup(interval_patcher.stop)

    def test_a_device_that_actually_turns_on_reports_success(self):
        client = FakeClient(responds=True)
        result = ControlSmartDeviceSkill(client).execute({"name": "soggiorno", "action": "on"})
        self.assertTrue(result.success)
        self.assertEqual(client.get_state("light.soggiorno")["state"], "on")

    def test_a_device_that_never_confirms_reports_operation_failed_not_success(self):
        """Il caso reale che questo previene: Home Assistant accetta la chiamata (nessun
        HomeAssistantError sollevato), ma il dispositivo e' spento/scollegato e non conferma mai
        il nuovo stato - prima di questa correzione questo sarebbe stato riportato come successo."""
        client = FakeClient(responds=False)
        result = ControlSmartDeviceSkill(client).execute({"name": "soggiorno", "action": "on"})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "OPERATION_FAILED")
        self.assertEqual(client.service_calls, [("light", "turn_on", "light.soggiorno", {})])

    def test_toggle_is_confirmed_by_a_state_change_not_a_fixed_target(self):
        """"toggle" non ha un target fisso (dipende dallo stato precedente, che la skill non
        conosce in anticipo con certezza) - deve essere confermato da un CAMBIO di stato rispetto
        a quello osservato prima della chiamata, non da un valore atteso specifico."""
        client = FakeClient(responds=True)  # presa cucina parte "on"
        result = ControlSmartDeviceSkill(client).execute({"name": "presa cucina", "action": "toggle"})
        self.assertTrue(result.success)
        self.assertEqual(client.get_state("switch.presa_cucina")["state"], "off")

    def test_a_toggle_that_never_changes_state_reports_operation_failed(self):
        client = FakeClient(responds=False)
        result = ControlSmartDeviceSkill(client).execute({"name": "presa cucina", "action": "toggle"})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "OPERATION_FAILED")

    def test_a_transient_get_state_error_during_polling_does_not_crash(self):
        """Un errore di rete durante il polling (il dispositivo potrebbe essere momentaneamente
        irraggiungibile subito dopo la chiamata) conta come "non ancora confermato", non fa
        sollevare un'eccezione ne' fa fallire l'attesa prima della scadenza."""
        client = FakeClient(responds=True, get_state_error=HomeAssistantError("rete lenta"))
        result = ControlSmartDeviceSkill(client).execute({"name": "soggiorno", "action": "on"})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "OPERATION_FAILED")


if __name__ == "__main__":
    unittest.main()
