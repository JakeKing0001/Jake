"""Scelta del modello (router F8.4), propagazione ai componenti, scaricamento del modello precedente e pacchetti di skill installati.

Estratto da JakeCore (3.2 Reliability & Architecture): metodi spostati alla lettera, comportamento
invariato. Lavorano sullo stato di JakeCore tramite self, come prima."""
from __future__ import annotations

import threading
from pathlib import Path
from core.request_context import add_turn_timing, current_turn_trace


class ModelRoutingMixin:
    def _skill_package_root(self, config):

        configured = config.get("skill_packages_dir") if config is not None else None
        return Path(configured) if configured else Path(__file__).resolve().parent.parent / "data" / "skill_packages"

    def _load_skill_packages(self, config) -> list[str]:
        """Carica ogni skill installata dal catalogo firmato e registra il rischio DICHIARATO dal suo manifest
        verificato. Un pacchetto alterato va in quarantena e non si carica; nessun errore blocca l'avvio."""
        from core.skill_package import SkillStore, TrustStore

        root = self._skill_package_root(config)
        try:
            self.skill_store = SkillStore(root, TrustStore(root / "trust.json"))
        except Exception:
            self.logger.exception("Catalogo delle skill installate non leggibile: nessun pacchetto caricato")
            return []
        loaded = []
        for skill_id in self.skill_store.skills():
            if self._activate_skill_package(skill_id):
                loaded.append(skill_id)
        return loaded

    def _activate_skill_package(self, skill_id: str) -> bool:
        from core.risk import register_package_risk

        try:
            result = self.skill_store.load(self.skill_registry, skill_id, self.logger)
        except Exception:
            self.logger.exception("Pacchetto %s non caricato", skill_id)
            return False
        if not getattr(result, "ok", False) or result.manifest is None:
            self.logger.warning("Pacchetto %s rifiutato: %s", skill_id, getattr(result, "errors", []))
            return False
        for spec in result.manifest.intents:
            register_package_risk(spec.intent, spec.risk)
        return True

    @property
    def model_router(self):
        from core.model_router import build_local_router

        if getattr(self, "_model_router", None) is None:
            config = dict(getattr(getattr(self, "config", None), "data", {}) or {})
            config["ollama_model"] = self._configured_model
            for key in ("ollama_light_model", "ollama_code_model", "ollama_model_vram_mb"):
                if getattr(self, "config", None) is not None and self.config.get(key):
                    config[key] = self.config.get(key)
            self._model_router = build_local_router(config, self.ollama.list_models)
        return self._model_router

    def _record_model_call(self, model: str, success: bool, latency_ms: float) -> None:
        from core.model_router import Capability, EvalRecord

        self.model_router.evals.record(EvalRecord(Capability.REASON, model, success, latency_ms))
        add_turn_timing("llm", latency_ms)
        turn = current_turn_trace()
        if turn is not None:
            models = turn.setdefault("models", [])
            if model not in models:
                models.append(model)

    def degraded_classifier_model(self) -> str | None:
        """Il modello del classificatore a GPU ceduta: uno dichiarato veloce (`ollama_light_model`) se il ModelRouter lo
        trova installato, altrimenti None (resta il modello del classificatore, in forma compatta)."""
        from core.model_router import Capability, choose_model

        configured = getattr(self, "_configured_model", "qwen2.5:7b")
        if getattr(self, "ollama", None) is None:
            return None
        try:
            chosen = choose_model(self.model_router, Capability.CLASSIFY, configured, prefer_fast=True)
        except Exception:
            return None
        return chosen if chosen != configured else None

    @property
    def model(self) -> str:
        """Il modello per ragionare ADESSO (agenti, planner, ricevute): scelto dal ModelRouter, con il modello
        configurato come ripiego se il router non trova nulla (Ollama spento). Mai un'eccezione qui."""
        from core.model_router import Capability, choose_model

        configured = getattr(self, "_configured_model", "qwen2.5:7b")
        if getattr(self, "ollama", None) is None:
            return configured
        try:
            chosen = choose_model(self.model_router, Capability.REASON, configured)
        except Exception:
            return configured
        self._release_previous_model(chosen)
        return chosen

    @model.setter
    def model(self, value: str) -> None:
        """SET_MODEL: il modello configurato cambia e il router riparte dal nuovo catalogo; la nuova scelta arriva
        subito anche alle skill (vedi _propagate_model)."""
        self._configured_model = value
        self._model_router = None
        if getattr(self, "ollama", None) is not None:
            _ = self.model

    def _release_previous_model(self, chosen: str) -> None:
        """F8.4.4: quando il router passa a un altro modello (es. quello leggero a batteria bassa), il precedente
        viene scaricato in background: resterebbe in VRAM/RAM per tutto il keep_alive (ore) senza servire a nulla,
        proprio quando l'energia conta. Mai bloccante, mai un'eccezione: nel peggiore dei casi resta caricato."""
        previous = getattr(self, "_last_routed_model", None)
        self._last_routed_model = chosen
        if previous is None or previous == chosen:
            return
        self._propagate_model(previous, chosen)

        logger = getattr(self, "logger", None)

        def unload():
            try:
                self.ollama.unload(previous)
                if logger is not None:
                    logger.info("Modello %s scaricato: il router ora usa %s", previous, chosen)
            except Exception:
                if logger is not None:
                    logger.warning("Non sono riuscito a scaricare il modello %s", previous)

        threading.Thread(target=unload, name="jake-model-unload", daemon=True).start()

    def _propagate_model(self, previous: str, chosen: str) -> None:
        """F8.4 (router ovunque) + bug reale: ASK_QUESTION, traduzioni, riassunti, correzione testi e gli altri
        componenti ricevono il nome del modello alla costruzione e chiamano Ollama direttamente con `self.model`. Ne'
        la scelta del router (modello leggero a batteria) ne' SET_MODEL li raggiungevano: le risposte libere restavano
        sul vecchio modello fino al riavvio. Qui ogni componente che usava il modello precedente passa a quello nuovo;
        chi ha un modello proprio diverso (visione, codice, embedding) non viene toccato."""
        registry = getattr(self, "skill_registry", None)
        skills = getattr(registry, "skills", {}) or {}
        components = list(skills.values()) + [
            getattr(getattr(self, "router", None), "primary_provider", None),
            getattr(self, "planner_provider", None), getattr(self, "context_summarizer", None),
        ]
        for component in components:
            if component is not None and getattr(component, "model", None) == previous:
                try:
                    component.model = chosen
                except AttributeError:
                    pass

    def _route_models_tick(self) -> None:
        """Ricontrolla la scelta del router (batteria, modelli installati): se cambia, propaga e scarica il vecchio."""
        _ = self.model
