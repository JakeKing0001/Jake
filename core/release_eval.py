"""Eval, canary e canali di rilascio (F8.6): la parte che decide se una versione di Jake merita di raggiungere
`stable`, non solo se "sembra funzionare".

Il principio, coerente con F8.4 ("misurare con eval, non con nome o moda"): una release non si promuove perche'
"sembra a posto" - si promuove perche' un `EvalReport` reale lo dice, con un set di sicurezza che non ammette
eccezioni e un confronto numerico contro la baseline stabile.

- F8.6.1 `GoldenSet`: casi per le cinque aree dichiarate (NLU, agenti, memoria, voce, computer use).
  `validate_golden_set` rifiuta un set che non copre tutte e cinque, o con id duplicati.
- F8.6.2 `SecuritySet`: un sottoinsieme di `GoldenCase` marcato `security=True` - **non facoltativo**: se il
  set candidato ne ha zero, il set intero e' invalido (nessuna release passa un cancello di sicurezza vuoto
  perche' nessuno lo ha popolato).
- F8.6.3 `compare`: tasso di successo per area, candidata contro stabile - una REGRESSIONE oltre la soglia
  dichiarata (mai un miglioramento, che non e' un problema).
- F8.6.4 `canary_sample`: solo casi NON di sicurezza (`security=False`, "task non sensibili" nel testo della
  fase), campionamento deterministico (un seed dato riproduce lo stesso campione).
- F8.6.5 `decide_rollback`: un CRASH (eccezione non gestita nel runner, distinto da un controllo che
  semplicemente fallisce) sopra soglia, QUALSIASI fallimento di sicurezza, o una regressione oltre soglia:
  ciascuno da solo basta.
- F8.6.6 `ReleaseRegistry`: canali `dev`/`beta`/`stable` con manifest firmati (Ed25519, stessa libreria gia'
  usata altrove in Jake, mai una crittografia scritta a mano). Promuovere a `stable` richiede zero fallimenti
  di sicurezza e nessuna regressione contro la `stable` attuale - il criterio di uscita della fase e' applicato
  qui, non lasciato a chi chiama.
- F8.6.7 `render_report`: riassunto leggibile (italiano, deterministico) di un `EvalReport` - il testo che un
  pannello HUD mostrerebbe, anche se nessun pannello lo consuma ancora (vedi limiti sotto).

Limiti dichiarati: nessun runner reale per NLU/agenti/memoria/voce/computer use e' collegato (`run_eval` prende
un `runner` iniettato: i test lo passano finto); nessun evento HUD viene pubblicato (`render_report` produce
solo testo); il registro dei canali e' in memoria, non persistito su disco tra riavvii."""
from __future__ import annotations

import base64
import hashlib
import json
import random
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

SIGNATURE_VERSION = 1
CHANNELS = ("dev", "beta", "stable")
_PROMOTION_ORDER = {name: index for index, name in enumerate(CHANNELS)}


class Suite(str, Enum):
    """F8.6.1: le cinque aree che un golden set deve coprire."""

    NLU = "nlu"
    AGENTS = "agents"
    MEMORY = "memory"
    VOICE = "voice"
    COMPUTER_USE = "computer_use"


# ---- F8.6.1/F8.6.2: golden set e security set ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class GoldenCase:
    id: str
    suite: Suite
    input: Any
    check: Callable[[Any], bool]
    security: bool = False
    description: str = ""


class GoldenSetError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        super().__init__("; ".join(errors))


def validate_golden_set(cases: list[GoldenCase]) -> list[str]:
    """Tutti i problemi (lista vuota = valido). F8.6.1: manca copertura di un'area; F8.6.2: zero casi di
    sicurezza e' un errore, non un set "piu' piccolo ma valido"."""
    errors = []
    if not cases:
        return ["golden_set_empty: nessun caso"]
    seen: set[str] = set()
    for case in cases:
        if not case.id or not case.id.strip():
            errors.append("empty_case_id")
        elif case.id in seen:
            errors.append(f"duplicate_case_id: {case.id}")
        seen.add(case.id)
    missing_suites = sorted(suite.value for suite in Suite if not any(c.suite == suite for c in cases))
    if missing_suites:
        errors.append(f"missing_suite_coverage: {missing_suites}")
    if not any(case.security for case in cases):
        errors.append("empty_security_set: F8.6.2 richiede almeno un caso di sicurezza")
    return errors


def require_golden_set(cases: list[GoldenCase]) -> None:
    errors = validate_golden_set(cases)
    if errors:
        raise GoldenSetError(errors)


# ---- esecuzione ------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    suite: Suite
    security: bool
    passed: bool
    crashed: bool  # True = il runner ha sollevato un'eccezione (distinto da un controllo che ha detto "no")
    latency_ms: float
    error: str = ""


@dataclass(frozen=True)
class EvalReport:
    release: str
    results: tuple[CaseResult, ...]
    started_at: float
    duration_ms: float

    def _suite_results(self, suite: Suite) -> list[CaseResult]:
        return [r for r in self.results if r.suite == suite]

    def suite_pass_rate(self, suite: Suite) -> float | None:
        results = self._suite_results(suite)
        return (sum(1 for r in results if r.passed) / len(results)) if results else None

    def suites(self) -> frozenset[Suite]:
        return frozenset(r.suite for r in self.results)

    def security_results(self) -> list[CaseResult]:
        return [r for r in self.results if r.security]

    def all_security_passed(self) -> bool:
        security = self.security_results()
        return bool(security) and all(r.passed for r in security)

    def crash_rate(self) -> float:
        return (sum(1 for r in self.results if r.crashed) / len(self.results)) if self.results else 0.0

    def overall_pass_rate(self) -> float:
        return (sum(1 for r in self.results if r.passed) / len(self.results)) if self.results else 0.0

    def failures(self) -> list[CaseResult]:
        return [r for r in self.results if not r.passed]


_ERROR_TRUNCATE = 200


def run_eval(release: str, runner: Callable[[Any], Any], cases: list[GoldenCase],
            clock: Callable[[], float] = time.monotonic) -> EvalReport:
    """Esegue OGNI caso: un'eccezione nel runner non ferma la corsa (diventa un `CaseResult` con `crashed=True`),
    cosi' un singolo caso rotto non nasconde l'esito di tutti gli altri."""
    started_wall = time.time()
    start = clock()
    results = []
    for case in cases:
        case_start = clock()
        try:
            output = runner(case.input)
            try:
                passed = bool(case.check(output))
                crashed, error = False, ""
            except Exception as exc:  # il CONTROLLO stesso e' rotto: non e' un successo del runner
                passed, crashed, error = False, True, f"{type(exc).__name__}: {str(exc)[:_ERROR_TRUNCATE]}"
        except Exception as exc:
            passed, crashed, error = False, True, f"{type(exc).__name__}: {str(exc)[:_ERROR_TRUNCATE]}"
        results.append(CaseResult(case.id, case.suite, case.security, passed, crashed,
                                  (clock() - case_start) * 1000, error))
    return EvalReport(release, tuple(results), started_wall, (clock() - start) * 1000)


# ---- F8.6.3: confronto con la baseline -------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Regression:
    suite: Suite
    stable_pass_rate: float
    candidate_pass_rate: float

    @property
    def delta(self) -> float:
        return self.candidate_pass_rate - self.stable_pass_rate


def compare(stable: EvalReport, candidate: EvalReport, max_regression: float = 0.05) -> list[Regression]:
    """Solo le aree che PEGGIORANO oltre `max_regression` rispetto alla stabile - un miglioramento, o un calo
    entro soglia (rumore statistico su pochi casi), non e' una regressione. Un'area assente nella candidata ma
    presente nella stabile e' trattata come un crollo al 0% (una copertura persa e' comunque una regressione)."""
    regressions = []
    for suite in stable.suites():
        stable_rate = stable.suite_pass_rate(suite)
        if stable_rate is None:
            continue
        candidate_rate = candidate.suite_pass_rate(suite)
        effective_candidate = candidate_rate if candidate_rate is not None else 0.0
        if effective_candidate < stable_rate - max_regression:
            regressions.append(Regression(suite, stable_rate, effective_candidate))
    return regressions


# ---- F8.6.4: canary ---------------------------------------------------------------------------------------------------------------


def canary_sample(cases: list[GoldenCase], fraction: float, seed: int = 0) -> list[GoldenCase]:
    """Un campione deterministico (stesso `seed` = stesso campione) di SOLI casi non sensibili. `fraction` e' la
    quota di quei casi da includere (0 = nessuno, 1 = tutti); i casi di sicurezza non entrano MAI nel canary -
    girano per intero a ogni eval (F8.6.2), non a campione."""
    if not 0.0 <= fraction <= 1.0:
        raise ValueError("fraction deve essere tra 0 e 1")
    eligible = sorted((c for c in cases if not c.security), key=lambda c: c.id)
    if not eligible:
        return []
    count = round(len(eligible) * fraction)
    rng = random.Random(seed)
    shuffled = list(eligible)
    rng.shuffle(shuffled)
    return shuffled[:count]


# ---- F8.6.5: rollback --------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RollbackPolicy:
    max_crash_rate: float = 0.0
    max_regression: float = 0.05
    require_all_security_passed: bool = True


@dataclass(frozen=True)
class RollbackDecision:
    should_rollback: bool
    reasons: tuple[str, ...]


def decide_rollback(candidate: EvalReport, policy: RollbackPolicy | None = None,
                    stable: EvalReport | None = None) -> RollbackDecision:
    """Ognuna delle tre condizioni basta da sola (F8.6.5: "crash, security failure O regressione oltre soglia" -
    una disgiunzione, non una media pesata che una potrebbe compensare con le altre)."""
    policy = policy or RollbackPolicy()
    reasons = []
    if policy.require_all_security_passed and not candidate.all_security_passed():
        failed = [r.case_id for r in candidate.security_results() if not r.passed]
        reasons.append(f"security_failure: {failed}" if failed else "security_failure: nessun caso di sicurezza eseguito")
    crash_rate = candidate.crash_rate()
    if crash_rate > policy.max_crash_rate:
        reasons.append(f"crash_rate {crash_rate:.2%} sopra la soglia {policy.max_crash_rate:.2%}")
    if stable is not None:
        for regression in compare(stable, candidate, policy.max_regression):
            reasons.append(f"regression:{regression.suite.value} {regression.stable_pass_rate:.2%} -> {regression.candidate_pass_rate:.2%}")
    return RollbackDecision(bool(reasons), tuple(reasons))


# ---- F8.6.6: canali con firme -------------------------------------------------------------------------------------------------------


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"), validate=True)


class ReleaseSigningKey:
    """Chiave del team che pubblica release (Ed25519, come ogni firma in Jake - vedi `core/sync_crypto.py` e
    `core/skill_package.py`: nessuna primitiva scritta a mano)."""

    def __init__(self, private: Ed25519PrivateKey) -> None:
        self._private = private

    @classmethod
    def generate(cls) -> ReleaseSigningKey:
        return cls(Ed25519PrivateKey.generate())

    @property
    def public_b64(self) -> str:
        raw = self._private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        return _b64(raw)

    @property
    def key_id(self) -> str:
        return hashlib.sha256(_unb64(self.public_b64)).hexdigest()[:16]

    def sign(self, manifest: ReleaseManifest) -> dict:
        payload = _canonical(manifest.to_signable())
        return {"v": SIGNATURE_VERSION, "key_id": self.key_id, "signature": _b64(self._private.sign(payload))}


def summarize(report: EvalReport) -> dict:
    """Riassunto numerico di un `EvalReport`, cio' che un manifest di release porta con se' (non l'intero
    dettaglio per-caso: un canale non ha bisogno di ogni traccia, solo dell'esito)."""
    return {
        "release": report.release,
        "overall_pass_rate": round(report.overall_pass_rate(), 6),
        "crash_rate": round(report.crash_rate(), 6),
        "security_passed": report.all_security_passed(),
        "suites": {suite.value: round(rate, 6) for suite in sorted(report.suites(), key=lambda s: s.value)
                  if (rate := report.suite_pass_rate(suite)) is not None},
    }


@dataclass(frozen=True)
class ReleaseManifest:
    channel: str
    version: str
    build_digest: str  # sha256 dell'artefatto costruito: cosa e' stato davvero valutato
    eval_summary: dict
    previous_version: str | None
    created_at: float

    def to_signable(self) -> dict:
        return {"channel": self.channel, "version": self.version, "build_digest": self.build_digest,
                "eval_summary": self.eval_summary, "previous_version": self.previous_version, "created_at": self.created_at}


class ReleaseError(RuntimeError):
    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(f"{code}: {message}" if message else code)


class ReleaseTrustStore:
    def __init__(self) -> None:
        self._keys: dict[str, str] = {}  # key_id -> public_b64
        self._revoked: set[str] = set()

    def add(self, key: ReleaseSigningKey) -> None:
        self._keys[key.key_id] = key.public_b64

    def revoke(self, key_id: str) -> None:
        self._revoked.add(key_id)

    def verify(self, manifest: ReleaseManifest, signature: dict) -> None:
        if not isinstance(signature, dict) or signature.get("v") != SIGNATURE_VERSION:
            raise ReleaseError("bad_signature", "formato non supportato")
        key_id = signature.get("key_id")
        if not isinstance(key_id, str) or key_id not in self._keys:
            raise ReleaseError("unknown_signer", str(key_id))
        if key_id in self._revoked:
            raise ReleaseError("signer_revoked", key_id)
        public = Ed25519PublicKey.from_public_bytes(_unb64(self._keys[key_id]))
        try:
            public.verify(_unb64(signature.get("signature", "")), _canonical(manifest.to_signable()))
        except (InvalidSignature, ValueError) as exc:
            raise ReleaseError("bad_signature", "firma non valida") from exc


class ReleaseRegistry:
    """F8.6.6: lo stato corrente di `dev`/`beta`/`stable`, con la cronologia per il rollback. **Criterio di
    uscita della fase applicato qui**: `publish` su `stable` rifiuta una candidata con fallimenti di sicurezza o
    con una regressione rispetto alla `stable` corrente - "una release regressiva non raggiunge stable" non e'
    una convenzione lasciata a chi chiama `publish`, e' imposta da questo metodo."""

    def __init__(self, trust: ReleaseTrustStore, policy: RollbackPolicy | None = None) -> None:
        self.trust = trust
        self.policy = policy or RollbackPolicy()
        self._current: dict[str, ReleaseManifest] = {}
        self._history: dict[str, list[ReleaseManifest]] = {name: [] for name in CHANNELS}

    def current(self, channel: str) -> ReleaseManifest | None:
        return self._current.get(channel)

    def history(self, channel: str) -> list[ReleaseManifest]:
        return list(self._history.get(channel, ()))

    def publish(self, manifest: ReleaseManifest, signature: dict) -> None:
        if manifest.channel not in CHANNELS:
            raise ReleaseError("unknown_channel", manifest.channel)
        self.trust.verify(manifest, signature)
        previous = self._current.get(manifest.channel)
        if previous is not None and manifest.previous_version != previous.version:
            raise ReleaseError("previous_version_mismatch",
                               f"atteso {previous.version!r}, dichiarato {manifest.previous_version!r}")
        if manifest.channel == "stable":
            self._enforce_stable_gate(manifest, previous)
        self._current[manifest.channel] = manifest
        self._history[manifest.channel].append(manifest)

    def _enforce_stable_gate(self, manifest: ReleaseManifest, previous: ReleaseManifest | None) -> None:
        summary = manifest.eval_summary
        if not summary.get("security_passed"):
            raise ReleaseError("blocked", "fallimenti di sicurezza: non puo' raggiungere stable")
        if summary.get("crash_rate", 1.0) > self.policy.max_crash_rate:
            raise ReleaseError("blocked", f"crash_rate {summary.get('crash_rate')} sopra la soglia")
        if previous is not None:
            for suite_name, candidate_rate in summary.get("suites", {}).items():
                stable_rate = previous.eval_summary.get("suites", {}).get(suite_name)
                if stable_rate is not None and candidate_rate < stable_rate - self.policy.max_regression:
                    raise ReleaseError("blocked", f"regressione su {suite_name}: {stable_rate} -> {candidate_rate}")

    def rollback(self, channel: str) -> ReleaseManifest:
        """Torna al manifest PRECEDENTE di quel canale (l'attuale resta nella cronologia, solo non e' piu' corrente)."""
        history = self._history.get(channel, [])
        if len(history) < 2:
            raise ReleaseError("no_previous_release", channel)
        history.pop()  # quello che si sta abbandonando
        self._current[channel] = history[-1]
        return history[-1]

    def promote(self, from_channel: str, to_channel: str) -> ReleaseManifest:
        """Solo in avanti (dev -> beta -> stable), mai a ritroso ne' saltando un canale: helper che costruisce
        il manifest della promozione dalla release corrente del canale di partenza, da firmare e pubblicare con
        `publish`. Non e' magia: ritorna il manifest da firmare, la firma resta responsabilita' del chiamante."""
        if from_channel not in CHANNELS or to_channel not in CHANNELS:
            raise ReleaseError("unknown_channel", f"{from_channel} -> {to_channel}")
        if _PROMOTION_ORDER[to_channel] != _PROMOTION_ORDER[from_channel] + 1:
            raise ReleaseError("invalid_promotion", f"{from_channel} -> {to_channel} non e' un passo in avanti valido")
        source = self._current.get(from_channel)
        if source is None:
            raise ReleaseError("no_release_to_promote", from_channel)
        target_current = self._current.get(to_channel)
        return ReleaseManifest(to_channel, source.version, source.build_digest, source.eval_summary,
                               target_current.version if target_current else None, source.created_at)


# ---- F8.6.7: report leggibile -------------------------------------------------------------------------------------------------------


def render_report(report: EvalReport) -> str:
    """Testo deterministico in italiano - cio' che un pannello HUD mostrerebbe (nessun pannello lo consuma
    ancora, vedi il docstring del modulo)."""
    lines = [f"Release {report.release} - {len(report.results)} casi, {report.duration_ms:.0f} ms totali",
             f"Esito complessivo: {report.overall_pass_rate():.1%} superati, crash {report.crash_rate():.1%}"]
    security = report.security_results()
    lines.append(f"Sicurezza: {'TUTTI superati' if report.all_security_passed() else 'FALLITI'} "
                 f"({sum(1 for r in security if r.passed)}/{len(security)})")
    lines.append("Per area:")
    for suite in sorted(report.suites(), key=lambda s: s.value):
        rate = report.suite_pass_rate(suite)
        lines.append(f"  - {suite.value}: {rate:.1%}" if rate is not None else f"  - {suite.value}: nessun caso")
    failures = report.failures()
    if failures:
        lines.append(f"Fallimenti ({len(failures)}):")
        lines.extend(f"  - {r.case_id} [{r.suite.value}]{' CRASH' if r.crashed else ''}: {r.error or 'controllo non superato'}"
                    for r in failures)
    return "\n".join(lines)
