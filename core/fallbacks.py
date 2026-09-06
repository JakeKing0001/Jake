"""Ripieghi intelligenti (v3.1): quando una skill fallisce in modo prevedibile, Jake prova la
mossa successiva sensata invece di fermarsi a "non trovo".

- "apri repubblica" con nessuna app chiamata cosi' -> se sembra un sito, OPEN_URL; altrimenti
  propone di cercarlo nel browser;
- "apri spotify" capito come sito (OPEN_URL senza dominio) ma c'e' l'app -> OPEN_APP;
- "clicca su Accedi" non trovato dall'OCR -> il modello di visione (CLICK_ELEMENT);
- "passa a blender" ma non e' aperto -> lo avvia;
- "chiudi X" senza processo -> chiude la finestra col titolo X."""
import re

from core.command import Command

KNOWN_SITES = {
    "youtube": "youtube.com", "google": "google.com", "gmail": "mail.google.com", "netflix": "netflix.com",
    "amazon": "amazon.it", "wikipedia": "it.wikipedia.org", "github": "github.com", "facebook": "facebook.com",
    "instagram": "instagram.com", "twitter": "x.com", "x": "x.com", "reddit": "reddit.com", "twitch": "twitch.tv",
    "chatgpt": "chatgpt.com", "repubblica": "repubblica.it", "corriere": "corriere.it", "ansa": "ansa.it",
    "gazzetta": "gazzetta.it", "linkedin": "linkedin.com", "tiktok": "tiktok.com", "pinterest": "pinterest.it",
    "ebay": "ebay.it", "subito": "subito.it", "prime video": "primevideo.com", "disney plus": "disneyplus.com",
    "spotify web": "open.spotify.com", "whatsapp web": "web.whatsapp.com", "drive": "drive.google.com",
    "maps": "maps.google.com", "google maps": "maps.google.com", "outlook": "outlook.live.com", "unipd": "unipd.it",
}


def looks_like_site(name: str) -> str | None:
    lowered = (name or "").strip().lower()
    if not lowered:
        return None
    if lowered in KNOWN_SITES:
        return KNOWN_SITES[lowered]
    if re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", lowered):
        return lowered
    return None


def pre_execution_rewrite(command: Command, registry) -> Command:
    """Correzioni prima di eseguire: OPEN_URL con un nome di app installata -> OPEN_APP."""
    if command.intent == "OPEN_URL":
        url = str((command.parameters or {}).get("url") or "").strip().lower()
        bare = re.sub(r"^https?://", "", url).rstrip("/")
        if bare and "." not in bare and " " not in bare:
            open_app = registry.get_skill("OPEN_APP")
            resolver = getattr(open_app, "app_resolver", None)
            if resolver is not None:
                try:
                    match = resolver.resolve(bare)
                except Exception:
                    match = None
                if match is not None and match.score >= 0.9:
                    return Command("OPEN_APP", {"app": bare})
    return command


def alternative_for(command: Command, result, registry) -> tuple[Command | None, str | None]:
    """(comando alternativo, nota da anteporre alla risposta) oppure (None, None)."""
    if result is None or result.success:
        return None, None
    intent = command.intent
    parameters = command.parameters or {}
    error = result.error

    if intent == "OPEN_APP" and error == "UNSUPPORTED_APP":
        site = looks_like_site(parameters.get("app", ""))
        if site:
            return Command("OPEN_URL", {"url": site}), None
        return None, None

    if intent == "FOCUS_WINDOW" and error == "WINDOW_NOT_FOUND":
        title = parameters.get("title", "")
        open_app = registry.get_skill("OPEN_APP")
        resolver = getattr(open_app, "app_resolver", None)
        if title and resolver is not None:
            try:
                match = resolver.resolve(title)
            except Exception:
                match = None
            if match is not None and match.score >= 0.85:
                return Command("OPEN_APP", {"app": title}), "Non era aperta: l'ho avviata."
        return None, None

    if intent == "CLICK_TEXT" and error in ("NOT_FOUND", "OCR_UNAVAILABLE") and registry.has_skill("CLICK_ELEMENT"):
        return Command("CLICK_ELEMENT", {"description": parameters.get("text", "")}), None

    if intent == "PLAY_MEDIA" and error in ("NETWORK_UNAVAILABLE", "OPERATION_FAILED"):
        return Command("OPEN_APP", {"app": "spotify"}), "Non riesco a riprodurlo direttamente: ti apro Spotify."

    if intent == "CLOSE_APP" and error == "NOT_FOUND" and registry.has_skill("CLOSE_WINDOW"):
        return Command("CLOSE_WINDOW", {"title": parameters.get("name", "")}), None

    return None, None


def offer_after_failure(command: Command, result) -> dict | None:
    """Un'azione da PROPORRE (con conferma) dopo un fallimento: es. cercare nel browser
    un'app che non esiste."""
    if result is None or result.success:
        return None
    if command.intent == "OPEN_APP" and result.error == "UNSUPPORTED_APP":
        app = (command.parameters or {}).get("app", "")
        if app:
            return {
                "intent": "SEARCH_IN_BROWSER",
                "parameters": {"query": app, "site": "google"},
                "message": f"Non trovo nessun programma chiamato {app}. Lo cerco nel browser?",
            }
    return None
