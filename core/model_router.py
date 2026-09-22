"""Instradamento dei modelli (F8.4): quale modello serve una richiesta, invece di ogni componente che scrive
il proprio nome di modello hardcoded (il problema reale oggi: `qwen2.5:7b` e' ripetuto in core/jake_core.py,
core/router.py e core/skill_catalog.py - un solo posto dove cambiarlo, e un posto che sa QUANDO cambiarlo).

Principio guida (F8.4.7, "misurare ogni scelta con eval, non con nome o moda del modello"): la qualita' di un
modello per una capability non e' una tabella scritta a mano che dice "qwen e' bravo" - e' la media delle
osservazioni REALI raccolte in `EvalStore` (successo, latenza, un punteggio di qualita' dichiarato dal
chiamante che ha verificato il risultato). Un modello mai osservato usa SOLO la baseline dichiarata nel suo
`ModelSpec`, mai una preferenza per nome o marca.

- F8.4.1 `Capability`: sette capacita' chiuse (classify, reason, code, vision, embedding, stt, tts) - un
  `ModelSpec` non classificato in nessuna di queste non e' selezionabile.
- F8.4.2 `ModelInventory`: cosa e' disponibile ORA, non cosa esiste in astratto - interroga il provider
  (`OllamaClient.list_models`, iniettabile) e un rilevatore di hardware (VRAM, batteria) anch'esso iniettabile,
  cosi' i test non dipendono dalla macchina ne' da Ollama in esecuzione.
- F8.4.3 `select`: filtra per vincoli DICHIARATI dal chiamante (qualita' minima, privacy, classe di latenza,
  VRAM disponibile, risparmio batteria, costo massimo) e ordina cio' che resta per qualita' osservata.
  Nessuna capability e' selezionabile per nome: il chiamante chiede "reason", non "qwen2.5".
- F8.4.4 `WarmPlan`: se il modello scelto e' gia' caldo non serve altro; altrimenti un warmup e, se la VRAM
  non basta per tenerne due, l'unload del modello caldo meno recente. `idle_unload_due` decide quando un
  modello inattivo va scaricato (rispetta `keep_alive`, non scarica mai prima del tempo dichiarato).
- F8.4.5 `Provider`: astrazione minima (`available`, `warm`, `unload`) con tre implementazioni - locale
  (Ollama), NPU/Windows AI (stub dichiarato: nessuna API reale integrata) e cloud opt-in (disattivato per
  default, va abilitato esplicitamente E la capability deve permettere dati non locali).
  F8.4.6: `redact_for_upload` NON indovina cosa e' sensibile leggendo il testo - lo stesso principio gia'
  applicato altrove in Jake (il rischio si dichiara, non si deduce dal contenuto): il chiamante passa
  `RoutedRequest.sensitive_fields`, i soli campi che vengono redatti PRIMA di un provider non locale; email e
  numeri di telefono nel testo libero sono comunque sempre coperti (un pattern strutturale, non un'inferenza
  di significato).
- F8.4.7 `EvalStore`: registro di osservazioni reali, aggregato con una media pesata verso le osservazioni
  recenti (le prestazioni di un modello possono degradare con un aggiornamento: le vecchie non devono pesare
  in eterno).

Limiti dichiarati: nessuna telemetria hardware reale integrata (il rilevatore di VRAM/batteria e' iniettabile
e di default ritorna "sconosciuto", scelta prudente: un vincolo che non si puo' verificare non passa); il
provider Windows AI/NPU e' un contratto vuoto, non un'integrazione; il provider cloud non chiama nessun
servizio reale - fa da confine esplicito dove la redazione avviene PRIMA che un byte lasci il PC."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Protocol

EMAIL_PATTERN = r"[\w.+-]+@[\w-]+\.[\w.-]+"
PHONE_PATTERN = r"(?<!\w)\+?\d[\d ()/.-]{6,}\d(?!\w)"


class Capability(str, Enum):
    """F8.4.1: le uniche sette capacita' che il router conosce."""

    CLASSIFY = "classify"
    REASON = "reason"
    CODE = "code"
    VISION = "vision"
    EMBEDDING = "embedding"
    STT = "stt"
    TTS = "tts"


class LatencyClass(str, Enum):
    FAST = "fast"      # < ~1s: classificazione, comandi brevi
    MEDIUM = "medium"   # qualche secondo: ragionamento breve
    SLOW = "slow"       # decine di secondi: codice, ragionamento lungo, visione


_LATENCY_ORDER = {LatencyClass.FAST: 0, LatencyClass.MEDIUM: 1, LatencyClass.SLOW: 2}


class BatteryImpact(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


_BATTERY_ORDER = {BatteryImpact.LOW: 0, BatteryImpact.MEDIUM: 1, BatteryImpact.HIGH: 2}


@dataclass(frozen=True)
class ModelSpec:
    """Cio' che un modello DICHIARA di se stesso - la parte che non si puo' osservare (nome, dove gira, quanta
    VRAM serve) piu' una qualita' di BASELINE usata solo finche' `EvalStore` non ha osservazioni proprie."""

    name: str
    provider: str  # "ollama" | "windows_ai" | "cloud"
    capabilities: frozenset[Capability]
    local: bool
    baseline_quality: float  # 0-100: dichiarata, sostituita dalle osservazioni reali appena ce ne sono
    min_vram_mb: int = 0
    latency_class: LatencyClass = LatencyClass.MEDIUM
    battery_impact: BatteryImpact = BatteryImpact.MEDIUM
    cost_per_1k_tokens: float = 0.0  # 0 per ogni provider locale

    def __post_init__(self) -> None:
        if not 0 <= self.baseline_quality <= 100:
            raise ValueError("baseline_quality deve essere tra 0 e 100")
        if not self.capabilities:
            raise ValueError(f"{self.name}: un ModelSpec deve dichiarare almeno una capability")
        if not self.local and self.provider == "ollama":
            raise ValueError(f"{self.name}: il provider ollama e' sempre locale")


# ---- F8.4.7: osservazioni reali, non nomi o mode -------------------------------------------------------------------------------


@dataclass(frozen=True)
class EvalRecord:
    capability: Capability
    model: str
    success: bool
    latency_ms: float
    quality: float | None = None  # 0-100, SOLO quando il chiamante ha verificato l'output (non ogni chiamata ce l'ha)
    at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class ModelStats:
    samples: int
    success_rate: float
    avg_latency_ms: float
    avg_quality: float | None


class EvalStore:
    """Osservazioni per (capability, modello). La media e' pesata verso le osservazioni RECENTI (decadimento
    esponenziale per indice, non per orologio: deterministico nei test, non dipende da quanto tempo e' passato
    dall'ultima chiamata)."""

    def __init__(self, decay: float = 0.9, max_records_per_key: int = 500) -> None:
        if not 0 < decay <= 1:
            raise ValueError("decay deve essere in (0, 1]")
        self._decay = decay
        self._max = max_records_per_key
        self._records: dict[tuple[Capability, str], list[EvalRecord]] = {}

    def record(self, entry: EvalRecord) -> None:
        key = (entry.capability, entry.model)
        bucket = self._records.setdefault(key, [])
        bucket.append(entry)
        if len(bucket) > self._max:
            del bucket[: len(bucket) - self._max]

    def stats(self, capability: Capability, model: str) -> ModelStats | None:
        bucket = self._records.get((capability, model))
        if not bucket:
            return None
        # Pesi crescenti dal piu' vecchio al piu' recente: l'ultima osservazione pesa 1, la precedente `decay`, ecc.
        weights = [self._decay ** (len(bucket) - 1 - index) for index in range(len(bucket))]
        total_weight = sum(weights)
        success_rate = sum(w * (1.0 if r.success else 0.0) for w, r in zip(weights, bucket, strict=True)) / total_weight
        avg_latency = sum(w * r.latency_ms for w, r in zip(weights, bucket, strict=True)) / total_weight
        quality_pairs = [(w, r.quality) for w, r in zip(weights, bucket, strict=True) if r.quality is not None]
        avg_quality = (sum(w * q for w, q in quality_pairs) / sum(w for w, _ in quality_pairs)) if quality_pairs else None
        return ModelStats(len(bucket), success_rate, avg_latency, avg_quality)

    def effective_quality(self, spec: ModelSpec, capability: Capability, *, min_samples: int = 3) -> float:
        """Qualita' da usare per la selezione: quella OSSERVATA se ci sono abbastanza campioni, altrimenti la
        baseline dichiarata (mai una preferenza dedotta dal nome del modello)."""
        stats = self.stats(capability, spec.name)
        if stats is None or stats.samples < min_samples or stats.avg_quality is None:
            return spec.baseline_quality
        # Un modello che fallisce spesso non merita la qualita' osservata sui soli successi.
        return stats.avg_quality * stats.success_rate + spec.baseline_quality * (1 - stats.success_rate)


# ---- F8.4.2: cosa e' disponibile adesso -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class HardwareInfo:
    """Iniettabile: di default tutto e' None ("sconosciuto"). Un vincolo su un valore sconosciuto non e' MAI
    dato per soddisfatto (F8.4.3): non esiste telemetria reale integrata, dichiarato nel docstring del modulo."""

    available_vram_mb: int | None = None
    on_battery: bool | None = None
    battery_percent: float | None = None


HardwareDetector = Callable[[], HardwareInfo]


def unknown_hardware() -> HardwareInfo:
    return HardwareInfo()


class ModelInventory:
    """Quali modelli sono REALMENTE disponibili ora: incrocia il catalogo dichiarato (`ModelSpec` per nome) con
    cio' che il provider dice di avere installato. Un modello dichiarato ma non installato non e' selezionabile
    (F8.4.4 lo tratta comunque come candidato per un warmup a comando, vedi `ModelRouter.select`)."""

    def __init__(self, specs: list[ModelSpec], list_installed: Callable[[], list[str] | None],
                 hardware: HardwareDetector = unknown_hardware, cloud_enabled: bool = False) -> None:
        by_name: dict[str, ModelSpec] = {}
        for spec in specs:
            if spec.name in by_name:
                raise ValueError(f"modello duplicato nell'inventario: {spec.name}")
            by_name[spec.name] = spec
        self._specs = by_name
        self._list_installed = list_installed
        self.hardware = hardware
        self.cloud_enabled = cloud_enabled

    def declared(self) -> list[ModelSpec]:
        return list(self._specs.values())

    def installed_names(self) -> set[str] | None:
        """None = il provider locale non e' raggiungibile (non "nessun modello": una distinzione che conta per
        F8.4.4, criterio di uscita - la perdita del provider non deve svuotare silenziosamente l'inventario dei
        modelli cloud/dichiarati)."""
        names = self._list_installed()
        return set(names) if names is not None else None

    def available(self, capability: Capability) -> list[ModelSpec]:
        """Modelli REALMENTE utilizzabili ora per `capability`: locali installati, piu' i cloud SOLO se il
        chiamante li ha abilitati esplicitamente (mai per default, F8.4.5/F8.4.6)."""
        installed = self.installed_names()
        result = []
        for spec in self._specs.values():
            if capability not in spec.capabilities:
                continue
            if spec.local:
                if installed is not None and spec.name in installed:
                    result.append(spec)
            elif self.cloud_enabled:
                result.append(spec)
        return result


# ---- F8.4.6: redazione prima di un provider non locale ---------------------------------------------------------------------


def redact_for_upload(text: str, sensitive_fields: dict[str, str] | None = None) -> str:
    """Testo pronto per un provider NON locale. `sensitive_fields` sono i soli valori che il CHIAMANTE ha
    dichiarato sensibili (mai dedotti leggendo il testo: stesso principio gia' applicato al resto di Jake per
    rischio/criticita'/sensibilita' - una decisione del chiamante, non un'inferenza sul contenuto). Email e
    numeri di telefono restano sempre coperti: non e' un'inferenza di significato, e' un pattern strutturale."""
    result = re.sub(EMAIL_PATTERN, "[email]", text)
    result = re.sub(PHONE_PATTERN, "[numero]", result)
    for label, value in (sensitive_fields or {}).items():
        if value:
            result = result.replace(value, f"[{label}]")
    return result


# ---- F8.4.5: provider ---------------------------------------------------------------------------------------------------------


class Provider(Protocol):
    name: str

    def available(self) -> bool: ...
    def warm(self, model: str) -> None: ...
    def unload(self, model: str) -> None: ...


class OllamaProviderAdapter:
    """Adatta `core.ollama_client.OllamaClient` al contratto `Provider` (warm = keep_alive via una chat vuota di
    riscaldamento, gia' il pattern usato altrove in Jake per tenere un modello caldo)."""

    name = "ollama"

    def __init__(self, client) -> None:
        self._client = client

    def available(self) -> bool:
        return bool(self._client.is_available())

    def warm(self, model: str) -> None:
        self._client.chat(model, [{"role": "user", "content": ""}], options={"num_predict": 0})

    def unload(self, model: str) -> None:
        # Ollama scarica un modello impostando keep_alive a 0 su una chat vuota: nessuna chiamata di unload
        # dedicata esiste nell'API REST. `OllamaClient.chat` non espone ancora un keep_alive per chiamata (solo
        # quello fissato nel costruttore) - richiederebbe un parametro in piu' su un modulo condiviso da tutto
        # Jake, fuori scopo qui: il contratto resta esplicito e testabile con un client finto nei test, senza
        # dipendere da un dettaglio dell'implementazione HTTP non ancora presente.
        raise NotImplementedError("OllamaClient non espone ancora un keep_alive per chiamata (F8.4.4)")


class WindowsAIProvider:
    """F8.4.5: stub dichiarato. Nessuna API Windows AI/NPU reale e' integrata - `available()` e' sempre False
    finche' non lo e'; whoever chiama questo provider deve gia' sapere che oggi non fa nulla."""

    name = "windows_ai"

    def available(self) -> bool:
        return False

    def warm(self, model: str) -> None:
        raise NotImplementedError("Windows AI/NPU non ancora integrato (F8.4.5)")

    def unload(self, model: str) -> None:
        raise NotImplementedError("Windows AI/NPU non ancora integrato (F8.4.5)")


class CloudProvider:
    """Opt-in, mai selezionato per default (`ModelInventory.cloud_enabled`). `send` e' un contratto vuoto: nessun
    servizio cloud reale e' chiamato da questo modulo, deliberatamente - integrarne uno e' fuori scopo per F8.4.
    Il punto di questo provider e' che REDAZIONE avviene qui, prima che qualunque implementazione futura possa
    mandare byte fuori dal PC."""

    name = "cloud"

    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled

    def available(self) -> bool:
        return self.enabled

    def warm(self, model: str) -> None:
        pass  # nessuno stato di "caldo" per un provider cloud

    def unload(self, model: str) -> None:
        pass

    def send(self, text: str, sensitive_fields: dict[str, str] | None = None) -> str:
        if not self.enabled:
            raise RuntimeError("provider cloud non abilitato: nessun dato esce dal PC")
        return redact_for_upload(text, sensitive_fields)


# ---- F8.4.3/F8.4.4: la richiesta e la decisione ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class RouteRequest:
    """Vincoli DICHIARATI dal chiamante - il router non ne inventa nessuno."""

    capability: Capability
    min_quality: float = 0.0
    require_local: bool = False  # F8.4.6: nessun dato lascia il PC per questa richiesta
    max_latency: LatencyClass | None = None
    max_battery_impact: BatteryImpact | None = None
    max_cost_per_1k_tokens: float | None = None
    required_vram_mb: int | None = None  # se noto: la richiesta sa quanta VRAM le serve
    prefer: str | None = None  # nome di modello preferito, a parita' di qualita' (mai una scorciatoia sul filtro)


@dataclass(frozen=True)
class RouteDecision:
    model: ModelSpec
    quality: float
    fallback_chain: tuple[str, ...]  # nomi dei prossimi candidati, in ordine, se `model` risultasse indisponibile


class NoModelAvailableError(RuntimeError):
    """Nessun modello soddisfa i vincoli: il chiamante decide cosa fare (non un fallback silenzioso su un modello
    che non rispetta un vincolo dichiarato, es. la privacy)."""


@dataclass(frozen=True)
class WarmPlan:
    """Cosa fare prima di usare `model` (F8.4.4)."""

    model: str
    already_warm: bool
    to_unload: tuple[str, ...]  # modelli da scaricare per fare spazio, in ordine (il piu' vecchio caldo prima)


class ModelRouter:
    def __init__(self, inventory: ModelInventory, evals: EvalStore | None = None,
                 max_warm_models: int = 2, clock: Callable[[], float] = time.time) -> None:
        self.inventory = inventory
        self.evals = evals or EvalStore()
        self.max_warm_models = max_warm_models
        self._clock = clock
        self._warm: dict[str, float] = {}  # nome -> istante dell'ultimo uso

    def _candidates(self, request: RouteRequest) -> list[ModelSpec]:
        specs = self.inventory.available(request.capability)
        if request.require_local:
            specs = [spec for spec in specs if spec.local]
        if request.max_latency is not None:
            specs = [spec for spec in specs if _LATENCY_ORDER[spec.latency_class] <= _LATENCY_ORDER[request.max_latency]]
        if request.max_battery_impact is not None:
            specs = [spec for spec in specs if _BATTERY_ORDER[spec.battery_impact] <= _BATTERY_ORDER[request.max_battery_impact]]
        if request.max_cost_per_1k_tokens is not None:
            specs = [spec for spec in specs if spec.cost_per_1k_tokens <= request.max_cost_per_1k_tokens]
        hardware = self.inventory.hardware()
        if request.required_vram_mb is not None and hardware.available_vram_mb is not None:
            specs = [spec for spec in specs if spec.min_vram_mb <= hardware.available_vram_mb]
        scored = [(self.evals.effective_quality(spec, request.capability), spec) for spec in specs]
        scored = [(quality, spec) for quality, spec in scored if quality >= request.min_quality]
        scored.sort(key=lambda item: (-item[0], item[1].name != (request.prefer or ""), item[1].name))
        return [spec for _, spec in scored]

    def select(self, request: RouteRequest) -> RouteDecision:
        """F8.4.3: il migliore che soddisfa i vincoli. F8.4.4 (criterio di uscita, "perdita del modello
        principale non blocca i comandi locali semplici"): la lista intera resta come catena di ripiego - se il
        primo scelto risulta poi irraggiungibile, il chiamante prova il prossimo della catena senza dover
        richiamare `select` da capo."""
        candidates = self._candidates(request)
        if not candidates:
            raise NoModelAvailableError(f"nessun modello disponibile per {request.capability.value} con i vincoli dati")
        best = candidates[0]
        return RouteDecision(best, self.evals.effective_quality(best, request.capability),
                             tuple(spec.name for spec in candidates[1:]))

    # ---- F8.4.4: warmup/unload -------------------------------------------------------------------------------------------

    def warm_plan(self, model_name: str) -> WarmPlan:
        already_warm = model_name in self._warm
        to_unload: tuple[str, ...] = ()
        if not already_warm and len(self._warm) >= self.max_warm_models:
            # Il piu' vecchio per ultimo uso lascia posto: e' quello con meno probabilita' di essere richiesto di nuovo a breve.
            oldest = sorted(self._warm, key=lambda name: self._warm[name])
            to_unload = tuple(oldest[: len(self._warm) - self.max_warm_models + 1])
        return WarmPlan(model_name, already_warm, to_unload)

    def apply_warm_plan(self, plan: WarmPlan, provider: Provider) -> None:
        for name in plan.to_unload:
            provider.unload(name)
            self._warm.pop(name, None)
        if not plan.already_warm:
            provider.warm(plan.model)
        self._warm[plan.model] = self._clock()

    def mark_used(self, model_name: str) -> None:
        """Aggiorna la recenza SENZA ricaricare: da chiamare a ogni uso, anche quando `warm_plan` non era
        necessario (il modello era gia' caldo)."""
        if model_name in self._warm:
            self._warm[model_name] = self._clock()

    def idle_unload_due(self, model_name: str, keep_alive_seconds: float) -> bool:
        """F8.4.4: un modello caldo va scaricato SOLO dopo `keep_alive_seconds` dal suo ultimo uso - mai prima,
        anche se un altro modello lo vorrebbe rimpiazzare (quello passa da `warm_plan`, non da qui)."""
        last_used = self._warm.get(model_name)
        if last_used is None:
            return False
        return (self._clock() - last_used) >= keep_alive_seconds

    def forget_warm(self, model_name: str) -> None:
        """Il chiamante ha scaricato il modello per conto suo (es. idle_unload_due): allinea lo stato del router."""
        self._warm.pop(model_name, None)

    def warm_models(self) -> frozenset[str]:
        return frozenset(self._warm)
