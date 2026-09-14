"""Skill domotiche via Home Assistant (v5.7, Home/IoT). Vedi core/home_assistant_client.py per
il perche' della scelta di Home Assistant come hub."""
import time
from difflib import SequenceMatcher

from core.home_assistant_client import HomeAssistantError
from core.skill_result import SkillResult


def _friendly_name(state: dict) -> str:
    return (state.get("attributes") or {}).get("friendly_name") or state.get("entity_id", "")


def _find_device(name: str, states: list[dict]) -> dict | None:
    """Trova lo stato il cui nome (friendly_name, o l'entity_id se manca) somiglia di piu' a
    'name': stessa logica di corrispondenza approssimata gia' usata per le app installate
    (core/app_resolver.py) e per il testo sullo schermo (skills/screen_click.py)."""
    needle = name.strip().lower()
    if not needle or not states:
        return None
    best, best_score = None, 0.0
    for state in states:
        candidate = _friendly_name(state).strip().lower()
        if not candidate:
            continue
        score = SequenceMatcher(None, candidate, needle).ratio()
        if candidate == needle:
            score = 1.0
        elif needle in candidate:
            score = max(score, 0.9)
        if score > best_score:
            best, best_score = state, score
    return best if best is not None and best_score >= 0.6 else None


class ListSmartDevicesSkill:
    metadata = {
        "intent": "LIST_SMART_DEVICES",
        "description": "Elenca i dispositivi smart home (luci, prese, interruttori...) collegati tramite Home "
        "Assistant e il loro stato attuale. Richiede Home Assistant configurato.",
        "parameters": {
            "domain": {
                "type": "string", "required": False,
                "description": "Filtra per tipo, es. 'light' (luci) o 'switch' (prese/interruttori). Omesso = tutti.",
            },
        },
    }

    def __init__(self, client):
        self.client = client

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        domain = (parameters.get("domain") or "").strip().lower() or None
        if not self.client.is_available():
            return SkillResult(success=False, data={}, error="HOME_ASSISTANT_UNAVAILABLE")

        try:
            states = self.client.list_states(domain=domain)
        except HomeAssistantError:
            return SkillResult(success=False, data={"domain": domain}, error="HOME_ASSISTANT_ERROR")

        if not states:
            return SkillResult(success=False, data={"domain": domain}, error="NOT_FOUND")

        devices = [
            {"name": _friendly_name(state), "entity_id": state.get("entity_id", ""), "state": state.get("state", "")}
            for state in states
        ]
        return SkillResult(success=True, data={"devices": devices})


class ControlSmartDeviceSkill:
    """F1.3.2 ("prove forti per... casa"): prima di questa correzione, `success=True` veniva
    restituito subito dopo `call_service()`, senza controllare che il dispositivo avesse DAVVERO
    cambiato stato - stesso identico buco gia' trovato e corretto per le finestre (CLOSE_WINDOW,
    skills/close_window.py) e i processi (KILL_PROCESS_BY_PORT). L'API REST di Home Assistant
    accetta una chiamata di servizio in modo fire-and-forget: significa solo che la richiesta e'
    stata ricevuta, non che il dispositivo fisico (che potrebbe essere spento, scollegato, o non
    rispondere) abbia confermato il nuovo stato. Ora attende fino a STATE_WAIT_SECONDS che
    l'entita' riporti davvero lo stato atteso prima di dichiarare successo."""

    metadata = {
        "intent": "CONTROL_SMART_DEVICE",
        "description": "Accende, spegne o alterna un dispositivo smart home (luce, presa, interruttore) "
        "collegato tramite Home Assistant, cercandolo per nome. Usalo per 'accendi la luce del soggiorno', "
        "'spegni la presa della cucina'. Richiede Home Assistant configurato.",
        "parameters": {
            "name": {"type": "string", "required": True, "description": "Nome del dispositivo, come lo conosce l'utente (es. 'luce del soggiorno')."},
            "action": {"type": "string", "required": True, "description": "'on' per accendere, 'off' per spegnere, 'toggle' per alternare."},
        },
    }

    SERVICE_BY_ACTION = {"on": "turn_on", "off": "turn_off", "toggle": "toggle"}
    STATE_WAIT_SECONDS = 3.0
    _POLL_INTERVAL_SECONDS = 0.3

    def __init__(self, client):
        self.client = client

    def _wait_until_state_matches(self, entity_id: str, is_expected) -> bool:
        """Interroga di nuovo l'entita' finche' il suo stato soddisfa `is_expected(state)` o
        scade STATE_WAIT_SECONDS. Un errore di rete durante il polling (il dispositivo potrebbe
        essere momentaneamente irraggiungibile subito dopo la chiamata) non interrompe l'attesa:
        conta come "non ancora confermato", non come un fallimento immediato."""
        deadline = time.monotonic() + self.STATE_WAIT_SECONDS
        while True:
            try:
                current = self.client.get_state(entity_id)
            except HomeAssistantError:
                current = None
            if current is not None and is_expected(current.get("state")):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(self._POLL_INTERVAL_SECONDS)

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        name = (parameters.get("name") or "").strip()
        action = (parameters.get("action") or "").strip().lower()
        if not name or action not in self.SERVICE_BY_ACTION:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")
        if not self.client.is_available():
            return SkillResult(success=False, data={}, error="HOME_ASSISTANT_UNAVAILABLE")

        try:
            states = self.client.list_states()
        except HomeAssistantError:
            return SkillResult(success=False, data={"name": name}, error="HOME_ASSISTANT_ERROR")

        match = _find_device(name, states)
        if match is None:
            return SkillResult(success=False, data={"name": name}, error="NOT_FOUND")

        entity_id = match.get("entity_id", "")
        domain = entity_id.split(".", 1)[0] if "." in entity_id else ""
        if not domain:
            return SkillResult(success=False, data={"name": name}, error="NOT_FOUND")

        # "toggle" non ha uno stato atteso fisso: e' confermato quando lo stato e' CAMBIATO
        # rispetto a quello osservato PRIMA della chiamata - letto qui, non dopo call_service():
        # `match` viene da list_states() (uno snapshot separato da call_service() su una vera API
        # HTTP, quindi il problema non si presenterebbe mai in produzione), ma leggere lo stato
        # "originale" DOPO aver gia' chiamato il servizio sarebbe comunque concettualmente sbagliato
        # a prescindere - un bug d'ordine trovato scrivendo un test con un FakeClient che simula il
        # dispositivo mutando lo stesso stato osservato, non a tavolino.
        original_state = match.get("state")

        try:
            self.client.call_service(domain, self.SERVICE_BY_ACTION[action], entity_id=entity_id)
        except HomeAssistantError:
            return SkillResult(success=False, data={"name": name}, error="HOME_ASSISTANT_ERROR")

        def is_expected(state: str) -> bool:
            if action == "toggle":
                return state != original_state
            return state == ("on" if action == "on" else "off")

        if not self._wait_until_state_matches(entity_id, is_expected):
            return SkillResult(success=False, data={"name": name, "entity_id": entity_id}, error="OPERATION_FAILED")

        return SkillResult(success=True, data={"name": _friendly_name(match), "entity_id": entity_id, "action": action})
