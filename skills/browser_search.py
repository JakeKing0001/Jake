"""Ricerca e riproduzione nel browser (v3.0).

Prima "cerca su youtube gatti" finiva su WEB_SEARCH (risposta istantanea di DuckDuckGo, che
per 'gatti' non ha nulla) e falliva con "Non ho accesso a internet". L'utente pero' voleva
semplicemente il browser aperto sui risultati: e' quello che fa SEARCH_IN_BROWSER, con
i motori/siti piu' comuni. PLAY_MEDIA va oltre: su YouTube risolve il primo video dei
risultati e apre direttamente la pagina di riproduzione (parte da sola)."""
import os
import re
import webbrowser
from urllib import parse, request

from core.network import is_online
from core.skill_result import SkillResult

SITE_SEARCH_URLS = {
    "google": "https://www.google.com/search?q={q}",
    "youtube": "https://www.youtube.com/results?search_query={q}",
    "amazon": "https://www.amazon.it/s?k={q}",
    "wikipedia": "https://it.wikipedia.org/w/index.php?search={q}",
    "github": "https://github.com/search?q={q}",
    "maps": "https://www.google.com/maps/search/{q}",
    "images": "https://www.google.com/search?tbm=isch&q={q}",
    "reddit": "https://www.reddit.com/search/?q={q}",
    "twitch": "https://www.twitch.tv/search?term={q}",
    "spotify": "https://open.spotify.com/search/{q}",
    "netflix": "https://www.netflix.com/search?q={q}",
    "ebay": "https://www.ebay.it/sch/i.html?_nkw={q}",
    "bing": "https://www.bing.com/search?q={q}",
    "duckduckgo": "https://duckduckgo.com/?q={q}",
    "stackoverflow": "https://stackoverflow.com/search?q={q}",
    "pinterest": "https://www.pinterest.it/search/pins/?q={q}",
    "x": "https://x.com/search?q={q}",
    "translate": "https://translate.google.com/?sl=auto&tl=it&text={q}",
    "chatgpt": "https://chatgpt.com/?q={q}",
    "subito": "https://www.subito.it/annunci-italia/vendita/usato/?q={q}",
}

SITE_HOME_URLS = {
    "google": "https://www.google.com", "youtube": "https://www.youtube.com", "amazon": "https://www.amazon.it",
    "wikipedia": "https://it.wikipedia.org", "github": "https://github.com", "maps": "https://www.google.com/maps",
    "images": "https://images.google.com", "reddit": "https://www.reddit.com", "twitch": "https://www.twitch.tv",
    "spotify": "https://open.spotify.com", "netflix": "https://www.netflix.com", "ebay": "https://www.ebay.it",
    "bing": "https://www.bing.com", "duckduckgo": "https://duckduckgo.com", "stackoverflow": "https://stackoverflow.com",
    "pinterest": "https://www.pinterest.it", "x": "https://x.com", "translate": "https://translate.google.com",
    "chatgpt": "https://chatgpt.com", "subito": "https://www.subito.it",
}

SITE_ALIASES = {
    "yt": "youtube", "you tube": "youtube", "video": "youtube", "immagini": "images", "foto": "images",
    "mappe": "maps", "google maps": "maps", "google mappe": "maps", "wiki": "wikipedia",
    "twitter": "x", "traduttore": "translate", "google translate": "translate", "traduci": "translate",
    "ddg": "duckduckgo", "stack overflow": "stackoverflow", "chat gpt": "chatgpt", "gpt": "chatgpt",
    # nomi di browser: l'utente dice "cerca su opera ..." intendendo "nel browser"
    "opera": "google", "chrome": "google", "edge": "google", "firefox": "google", "brave": "google",
    "browser": "google", "internet": "google", "web": "google", "online": "google", "rete": "google",
}


def normalize_site(site: str) -> str:
    site = (site or "").strip().lower()
    site = re.sub(r"^(su|sul|sulla|in|nel|nella|con)\s+", "", site)
    site = SITE_ALIASES.get(site, site)
    return site if site in SITE_SEARCH_URLS else "google"


class SearchInBrowserSkill:
    metadata = {
        "intent": "SEARCH_IN_BROWSER",
        "description": "Apre il browser sui risultati di una ricerca: su Google (default), YouTube, "
        "Amazon, Wikipedia, GitHub, Google Maps, immagini, Reddit, Twitch, ecc. Usalo per 'cerca su "
        "youtube X', 'cerca X', 'googla X', 'cerca su amazon X', 'cerca video di X'. Diverso da "
        "WEB_SEARCH (risposta rapida senza browser) e da OPEN_URL (apre solo un sito).",
        "remote": True,
        "parameters": {
            "query": {"type": "string", "required": True, "description": "Cosa cercare, con le parole dell'utente (senza il nome del sito). Stringa vuota se l'utente vuole solo aprire il sito."},
            "site": {"type": "string", "required": False, "description": "Dove cercare: google (default), youtube, amazon, wikipedia, github, maps, images, reddit, twitch, spotify, netflix, ebay, x, translate, chatgpt."},
        },
    }

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        query = (parameters.get("query") or "").strip()
        site = normalize_site(parameters.get("site"))
        if not query:
            url = SITE_HOME_URLS[site]
        else:
            url = SITE_SEARCH_URLS[site].format(q=parse.quote_plus(query) if "maps" not in site else parse.quote(query))
        try:
            opened = webbrowser.open(url)
        except Exception:
            opened = False
        if not opened:
            return SkillResult(success=False, data={"url": url}, error="OPERATION_FAILED")
        return SkillResult(success=True, data={"query": query, "site": site, "url": url})


class PlayMediaSkill:
    metadata = {
        "intent": "PLAY_MEDIA",
        "description": "Riproduce musica o video: su YouTube apre direttamente il primo video trovato "
        "(parte da solo), su Spotify apre la ricerca nell'app. Usalo per 'metti X', 'fammi sentire X', "
        "'suona X', 'riproduci X su youtube'. Diverso da MEDIA_CONTROL (play/pausa di cio' che gia' suona).",
        "remote": True,
        "parameters": {
            "query": {"type": "string", "required": True, "description": "Canzone, artista, playlist o video da riprodurre."},
            "service": {"type": "string", "required": False, "description": "'spotify' (default per musica) oppure 'youtube' (default per video)."},
        },
    }

    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

    def __init__(self, timeout: float = 8):
        self.timeout = timeout

    def execute(self, parameters: dict = None):
        parameters = parameters or {}
        query = (parameters.get("query") or "").strip()
        service = (parameters.get("service") or "").strip().lower()
        if not query:
            return SkillResult(success=False, data={}, error="MISSING_PARAMETERS")
        if service not in ("spotify", "youtube"):
            service = "youtube" if re.search(r"\b(video|trailer|tutorial|film|episodio|puntata|youtube)\b", query) else "spotify"

        if service == "spotify":
            return self._play_spotify(query)
        return self._play_youtube(query)

    def _play_spotify(self, query: str):
        uri = f"spotify:search:{parse.quote(query)}"
        try:
            os.startfile(uri)
            return SkillResult(success=True, data={"query": query, "service": "spotify", "url": uri, "autoplay": False})
        except OSError:
            pass
        url = SITE_SEARCH_URLS["spotify"].format(q=parse.quote(query))
        try:
            webbrowser.open(url)
        except Exception:
            return SkillResult(success=False, data={"query": query}, error="OPERATION_FAILED")
        return SkillResult(success=True, data={"query": query, "service": "spotify", "url": url, "autoplay": False})

    def _play_youtube(self, query: str):
        results_url = SITE_SEARCH_URLS["youtube"].format(q=parse.quote_plus(query))
        if not is_online():
            return SkillResult(success=False, data={"query": query}, error="NETWORK_UNAVAILABLE")
        video_id, title = self._first_youtube_video(results_url)
        url = f"https://www.youtube.com/watch?v={video_id}" if video_id else results_url
        try:
            opened = webbrowser.open(url)
        except Exception:
            opened = False
        if not opened:
            return SkillResult(success=False, data={"query": query}, error="OPERATION_FAILED")
        return SkillResult(success=True, data={
            "query": query, "service": "youtube", "url": url, "title": title, "autoplay": bool(video_id),
        })

    def _first_youtube_video(self, results_url: str) -> tuple[str | None, str | None]:
        """Legge la pagina dei risultati (che include i dati iniziali in JSON) e prende il
        primo videoId: senza API key, senza dipendenze."""
        http_request = request.Request(results_url, headers={"User-Agent": self.USER_AGENT, "Accept-Language": "it-IT,it;q=0.9"})
        try:
            with request.urlopen(http_request, timeout=self.timeout) as response:
                html = response.read().decode("utf-8", errors="replace")
        except Exception:
            return None, None
        match = re.search(r'"videoRenderer":\{"videoId":"([\w-]{11})".*?"title":\{"runs":\[\{"text":"(.*?)"\}', html, flags=re.DOTALL)
        if match:
            title = match.group(2).encode("utf-8").decode("unicode_escape", errors="ignore")
            return match.group(1), title
        match = re.search(r'"videoId":"([\w-]{11})"', html)
        return (match.group(1), None) if match else (None, None)
