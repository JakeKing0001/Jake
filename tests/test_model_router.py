"""F8.4: instradamento dei modelli. Provider finti ma con lo stesso contratto (`available`/`warm`/`unload`) che
`OllamaProviderAdapter` espone davvero su un `OllamaClient` finto, cosi' l'adattatore e' provato per il suo
comportamento reale, non solo il resto del router con doppi."""
import unittest

from core.model_router import (
    BatteryImpact, Capability, CloudProvider, EvalRecord, EvalStore, HardwareInfo, LatencyClass, ModelInventory,
    ModelRouter, ModelSpec, NoModelAvailableError, OllamaProviderAdapter, RouteRequest, WindowsAIProvider,
    redact_for_upload,
)


def spec(name, capabilities, **overrides) -> ModelSpec:
    base = {"provider": "ollama", "capabilities": frozenset(capabilities), "local": True, "baseline_quality": 50.0}
    base.update(overrides)
    return ModelSpec(name, **base)


SMALL = spec("qwen2.5:1.5b", {Capability.CLASSIFY}, baseline_quality=40, latency_class=LatencyClass.FAST,
             battery_impact=BatteryImpact.LOW, min_vram_mb=2000)
BIG = spec("qwen2.5:7b", {Capability.CLASSIFY, Capability.REASON, Capability.CODE}, baseline_quality=70,
           latency_class=LatencyClass.SLOW, battery_impact=BatteryImpact.HIGH, min_vram_mb=8000)
VISION = spec("qwen2.5vl:7b", {Capability.VISION}, baseline_quality=60, min_vram_mb=8000)
CLOUD = spec("gpt-cloud", {Capability.REASON}, provider="cloud", local=False, baseline_quality=90,
             cost_per_1k_tokens=0.01, latency_class=LatencyClass.MEDIUM)


def inventory(specs, installed, **kwargs) -> ModelInventory:
    return ModelInventory(specs, lambda: installed, **kwargs)


class ModelSpecTests(unittest.TestCase):
    def test_a_spec_needs_at_least_one_capability_and_a_valid_quality(self):
        with self.assertRaises(ValueError):
            spec("x", set())
        with self.assertRaises(ValueError):
            spec("x", {Capability.REASON}, baseline_quality=101)
        with self.assertRaises(ValueError):
            spec("x", {Capability.REASON}, baseline_quality=-1)

    def test_the_ollama_provider_is_always_local(self):
        with self.assertRaises(ValueError):
            ModelSpec("x", "ollama", frozenset({Capability.REASON}), local=False, baseline_quality=50)


class EvalStoreTests(unittest.TestCase):
    def test_unobserved_model_falls_back_to_the_declared_baseline(self):
        store = EvalStore()
        self.assertEqual(store.effective_quality(BIG, Capability.REASON), 70.0)

    def test_a_name_alone_never_grants_quality_only_recorded_evals_do(self):
        store = EvalStore()
        fancy_named = spec("qwen-ultra-mega-pro", {Capability.REASON}, baseline_quality=50)
        # nessuna osservazione: il nome altisonante non vale nulla in piu' della baseline dichiarata
        self.assertEqual(store.effective_quality(fancy_named, Capability.REASON), 50.0)

    def test_enough_successful_observations_override_the_baseline(self):
        store = EvalStore()
        for _ in range(5):
            store.record(EvalRecord(Capability.REASON, BIG.name, success=True, latency_ms=100, quality=95))
        self.assertGreater(store.effective_quality(BIG, Capability.REASON), 70.0)
        self.assertAlmostEqual(store.effective_quality(BIG, Capability.REASON), 95.0, delta=0.5)

    def test_too_few_samples_still_use_the_baseline(self):
        store = EvalStore()
        store.record(EvalRecord(Capability.REASON, BIG.name, success=True, latency_ms=100, quality=99))
        store.record(EvalRecord(Capability.REASON, BIG.name, success=True, latency_ms=100, quality=99))
        self.assertEqual(store.effective_quality(BIG, Capability.REASON, min_samples=3), 70.0)

    def test_a_high_failure_rate_pulls_the_observed_quality_down(self):
        store = EvalStore(decay=1.0)
        for _ in range(4):
            store.record(EvalRecord(Capability.REASON, BIG.name, success=False, latency_ms=100, quality=None))
        store.record(EvalRecord(Capability.REASON, BIG.name, success=True, latency_ms=100, quality=100))
        effective = store.effective_quality(BIG, Capability.REASON)
        # 1 successo (qualita' 100) su 5 osservazioni: la qualita' osservata pesa solo per il success_rate (20%),
        # il resto resta la baseline dichiarata - lontano dai 100 di qualita' del singolo successo.
        self.assertAlmostEqual(effective, 100 * 0.2 + 70.0 * 0.8, places=6)
        self.assertLess(effective, 100.0)

    def test_recent_observations_outweigh_old_ones(self):
        store = EvalStore(decay=0.5)
        for _ in range(10):
            store.record(EvalRecord(Capability.REASON, BIG.name, success=True, latency_ms=100, quality=20))
        store.record(EvalRecord(Capability.REASON, BIG.name, success=True, latency_ms=100, quality=90))
        self.assertGreater(store.effective_quality(BIG, Capability.REASON), 50.0)

    def test_stats_are_per_capability_and_per_model(self):
        store = EvalStore()
        store.record(EvalRecord(Capability.REASON, BIG.name, success=True, latency_ms=100, quality=90))
        self.assertIsNone(store.stats(Capability.CODE, BIG.name))
        self.assertIsNone(store.stats(Capability.REASON, SMALL.name))
        self.assertIsNotNone(store.stats(Capability.REASON, BIG.name))

    def test_the_record_buffer_is_bounded(self):
        store = EvalStore(max_records_per_key=5)
        for index in range(20):
            store.record(EvalRecord(Capability.REASON, BIG.name, success=True, latency_ms=100, quality=float(index)))
        self.assertEqual(store.stats(Capability.REASON, BIG.name).samples, 5)


class InventoryTests(unittest.TestCase):
    def test_only_installed_local_models_are_available(self):
        inv = inventory([SMALL, BIG], installed=[SMALL.name])
        self.assertEqual([spec.name for spec in inv.available(Capability.CLASSIFY)], [SMALL.name])
        self.assertEqual(inv.available(Capability.REASON), [])

    def test_cloud_models_are_never_available_unless_explicitly_enabled(self):
        inv = inventory([BIG, CLOUD], installed=[BIG.name])
        self.assertEqual([spec.name for spec in inv.available(Capability.REASON)], [BIG.name])
        enabled = inventory([BIG, CLOUD], installed=[BIG.name], cloud_enabled=True)
        self.assertEqual({spec.name for spec in enabled.available(Capability.REASON)}, {BIG.name, CLOUD.name})

    def test_an_unreachable_local_provider_is_distinct_from_zero_local_models(self):
        inv = inventory([BIG, CLOUD], installed=None, cloud_enabled=True)
        self.assertIsNone(inv.installed_names())
        # il provider locale e' giu': niente modelli locali, ma i cloud abilitati restano visibili
        self.assertEqual([spec.name for spec in inv.available(Capability.REASON)], [CLOUD.name])

    def test_duplicate_names_are_rejected(self):
        with self.assertRaises(ValueError):
            inventory([SMALL, spec(SMALL.name, {Capability.REASON})], installed=[])

    def test_a_capability_nobody_declares_has_no_candidates(self):
        inv = inventory([SMALL], installed=[SMALL.name])
        self.assertEqual(inv.available(Capability.TTS), [])


class RedactionTests(unittest.TestCase):
    def test_emails_and_phone_numbers_are_always_covered(self):
        text = "Scrivimi a mario@example.com o chiamami al +39 333 123 4567."
        redacted = redact_for_upload(text)
        self.assertNotIn("mario@example.com", redacted)
        self.assertNotIn("333 123 4567", redacted)
        self.assertIn("[email]", redacted)
        self.assertIn("[numero]", redacted)

    def test_only_fields_the_caller_declares_are_redacted_never_inferred_from_content(self):
        text = "Il progetto segreto si chiama Fenice e il budget e' 50000."
        redacted = redact_for_upload(text, sensitive_fields={"progetto": "Fenice"})
        self.assertNotIn("Fenice", redacted)
        self.assertIn("[progetto]", redacted)
        self.assertIn("50000", redacted)  # non dichiarato sensibile: il router non lo deduce da solo

    def test_an_empty_declared_value_is_never_replaced(self):
        text = "Testo normale."
        self.assertEqual(redact_for_upload(text, sensitive_fields={"x": ""}), text)


class CloudProviderTests(unittest.TestCase):
    def test_disabled_by_default_and_refuses_to_send(self):
        provider = CloudProvider()
        self.assertFalse(provider.available())
        with self.assertRaises(RuntimeError):
            provider.send("ciao mario@example.com")

    def test_enabled_redacts_before_returning_the_payload(self):
        provider = CloudProvider(enabled=True)
        self.assertTrue(provider.available())
        self.assertNotIn("mario@example.com", provider.send("scrivi a mario@example.com"))


class WindowsAIProviderTests(unittest.TestCase):
    def test_is_a_declared_stub_never_silently_pretending_to_work(self):
        provider = WindowsAIProvider()
        self.assertFalse(provider.available())
        with self.assertRaises(NotImplementedError):
            provider.warm("qualunque")
        with self.assertRaises(NotImplementedError):
            provider.unload("qualunque")


class FakeOllamaClient:
    def __init__(self, available=True):
        self._available = available
        self.chat_calls = []

    def is_available(self):
        return self._available

    def chat(self, model, messages, options=None, timeout=None):
        self.chat_calls.append((model, options))
        return {}


class OllamaAdapterTests(unittest.TestCase):
    def test_available_reflects_the_underlying_client(self):
        self.assertTrue(OllamaProviderAdapter(FakeOllamaClient(True)).available())
        self.assertFalse(OllamaProviderAdapter(FakeOllamaClient(False)).available())

    def test_warm_sends_a_zero_prediction_chat_to_keep_the_model_loaded(self):
        client = FakeOllamaClient()
        OllamaProviderAdapter(client).warm("qwen2.5:7b")
        self.assertEqual(client.chat_calls, [("qwen2.5:7b", {"num_predict": 0})])

    def test_unload_is_a_declared_gap_not_a_silent_no_op(self):
        with self.assertRaises(NotImplementedError):
            OllamaProviderAdapter(FakeOllamaClient()).unload("qwen2.5:7b")


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.inv = inventory([SMALL, BIG, VISION, CLOUD], installed=[SMALL.name, BIG.name, VISION.name])
        self.router = ModelRouter(self.inv)

    def test_picks_the_highest_quality_candidate_for_the_capability(self):
        decision = self.router.select(RouteRequest(Capability.CLASSIFY))
        self.assertEqual(decision.model.name, BIG.name)  # 70 > 40, entrambi hanno CLASSIFY
        self.assertEqual(decision.fallback_chain, (SMALL.name,))

    def test_a_capability_nobody_serves_raises_a_typed_error(self):
        with self.assertRaises(NoModelAvailableError):
            self.router.select(RouteRequest(Capability.TTS))

    def test_min_quality_excludes_weaker_candidates(self):
        decision = self.router.select(RouteRequest(Capability.CLASSIFY, min_quality=60))
        self.assertEqual(decision.model.name, BIG.name)
        with self.assertRaises(NoModelAvailableError):
            self.router.select(RouteRequest(Capability.CLASSIFY, min_quality=71))

    def test_require_local_excludes_cloud_even_when_it_would_score_higher(self):
        enabled = ModelRouter(inventory([BIG, CLOUD], installed=[BIG.name], cloud_enabled=True))
        without_constraint = enabled.select(RouteRequest(Capability.REASON))
        self.assertEqual(without_constraint.model.name, CLOUD.name)  # 90 > 70
        local_only = enabled.select(RouteRequest(Capability.REASON, require_local=True))
        self.assertEqual(local_only.model.name, BIG.name)

    def test_latency_and_battery_constraints_filter_out_heavier_models(self):
        decision = self.router.select(RouteRequest(Capability.CLASSIFY, max_latency=LatencyClass.FAST))
        self.assertEqual(decision.model.name, SMALL.name)
        decision = self.router.select(RouteRequest(Capability.CLASSIFY, max_battery_impact=BatteryImpact.LOW))
        self.assertEqual(decision.model.name, SMALL.name)

    def test_cost_constraint_excludes_paid_models(self):
        enabled = ModelRouter(inventory([BIG, CLOUD], installed=[BIG.name], cloud_enabled=True))
        decision = enabled.select(RouteRequest(Capability.REASON, max_cost_per_1k_tokens=0.0))
        self.assertEqual(decision.model.name, BIG.name)

    def test_vram_constraint_is_only_enforced_when_hardware_is_known(self):
        limited = ModelRouter(inventory([SMALL, BIG], installed=[SMALL.name, BIG.name],
                                        hardware=lambda: HardwareInfo(available_vram_mb=3000)))
        decision = limited.select(RouteRequest(Capability.CLASSIFY, required_vram_mb=1))
        self.assertEqual(decision.model.name, SMALL.name)  # BIG richiede 8000, non ci sta
        unknown_hw = ModelRouter(inventory([SMALL, BIG], installed=[SMALL.name, BIG.name]))
        decision = unknown_hw.select(RouteRequest(Capability.CLASSIFY, required_vram_mb=1))
        self.assertEqual(decision.model.name, BIG.name)  # VRAM sconosciuta: il vincolo non esclude nulla

    def test_prefer_only_breaks_ties_never_overrides_a_hard_filter(self):
        decision = self.router.select(RouteRequest(Capability.CLASSIFY, prefer=SMALL.name))
        self.assertEqual(decision.model.name, BIG.name)  # la qualita' resta il criterio primario
        equal_a = spec("a-model", {Capability.CLASSIFY}, baseline_quality=50)
        equal_b = spec("b-model", {Capability.CLASSIFY}, baseline_quality=50)
        tie = ModelRouter(inventory([equal_a, equal_b], installed=[equal_a.name, equal_b.name]))
        self.assertEqual(tie.select(RouteRequest(Capability.CLASSIFY, prefer="b-model")).model.name, "b-model")

    def test_a_model_installed_for_another_capability_is_not_offered(self):
        decision = self.router.select(RouteRequest(Capability.VISION))
        self.assertEqual(decision.model.name, VISION.name)
        with self.assertRaises(NoModelAvailableError):
            self.router.select(RouteRequest(Capability.EMBEDDING))

    def test_losing_the_primary_model_falls_back_to_local_candidates_not_an_empty_list(self):
        """Criterio di uscita F8.4: la perdita del modello principale non blocca i comandi locali semplici."""
        decision = self.router.select(RouteRequest(Capability.CLASSIFY))
        self.assertEqual(decision.model.name, BIG.name)
        # BIG "scompare" (scaricato, crash del provider): il chiamante prova il prossimo della catena gia' avuta.
        self.assertEqual(decision.fallback_chain, (SMALL.name,))
        retried = ModelRouter(inventory([SMALL, VISION], installed=[SMALL.name, VISION.name]))
        self.assertEqual(retried.select(RouteRequest(Capability.CLASSIFY)).model.name, SMALL.name)


class WarmPlanTests(unittest.TestCase):
    def setUp(self):
        self.clock = [0.0]
        self.router = ModelRouter(inventory([SMALL, BIG, VISION], installed=[SMALL.name, BIG.name, VISION.name]),
                                  max_warm_models=2, clock=lambda: self.clock[0])
        self.provider = _RecordingProvider()

    def test_a_cold_model_is_warmed_and_marked(self):
        plan = self.router.warm_plan(SMALL.name)
        self.assertFalse(plan.already_warm)
        self.assertEqual(plan.to_unload, ())
        self.router.apply_warm_plan(plan, self.provider)
        self.assertEqual(self.provider.warmed, [SMALL.name])
        self.assertEqual(self.router.warm_models(), frozenset({SMALL.name}))

    def test_an_already_warm_model_needs_no_warmup_call(self):
        self.router.apply_warm_plan(self.router.warm_plan(SMALL.name), self.provider)
        self.provider.warmed.clear()
        plan = self.router.warm_plan(SMALL.name)
        self.assertTrue(plan.already_warm)
        self.router.apply_warm_plan(plan, self.provider)
        self.assertEqual(self.provider.warmed, [])

    def test_over_the_limit_the_oldest_used_model_is_unloaded_first(self):
        self.router.apply_warm_plan(self.router.warm_plan(SMALL.name), self.provider)
        self.clock[0] = 10
        self.router.apply_warm_plan(self.router.warm_plan(BIG.name), self.provider)
        self.clock[0] = 20
        plan = self.router.warm_plan(VISION.name)
        self.assertEqual(plan.to_unload, (SMALL.name,))
        self.router.apply_warm_plan(plan, self.provider)
        self.assertEqual(self.provider.unloaded, [SMALL.name])
        self.assertEqual(self.router.warm_models(), frozenset({BIG.name, VISION.name}))

    def test_mark_used_keeps_a_model_from_being_the_next_to_be_evicted(self):
        self.router.apply_warm_plan(self.router.warm_plan(SMALL.name), self.provider)
        self.clock[0] = 10
        self.router.apply_warm_plan(self.router.warm_plan(BIG.name), self.provider)
        self.clock[0] = 20
        self.router.mark_used(SMALL.name)
        self.clock[0] = 30
        plan = self.router.warm_plan(VISION.name)
        self.assertEqual(plan.to_unload, (BIG.name,))

    def test_idle_unload_respects_keep_alive_and_never_fires_early(self):
        self.router.apply_warm_plan(self.router.warm_plan(SMALL.name), self.provider)
        self.assertFalse(self.router.idle_unload_due(SMALL.name, keep_alive_seconds=30))
        self.clock[0] = 29
        self.assertFalse(self.router.idle_unload_due(SMALL.name, keep_alive_seconds=30))
        self.clock[0] = 30
        self.assertTrue(self.router.idle_unload_due(SMALL.name, keep_alive_seconds=30))

    def test_a_never_warmed_model_is_never_due_for_idle_unload(self):
        self.assertFalse(self.router.idle_unload_due("mai-caricato", keep_alive_seconds=1))

    def test_forget_warm_lets_the_caller_reconcile_state_after_an_external_unload(self):
        self.router.apply_warm_plan(self.router.warm_plan(SMALL.name), self.provider)
        self.router.forget_warm(SMALL.name)
        self.assertEqual(self.router.warm_models(), frozenset())
        plan = self.router.warm_plan(SMALL.name)
        self.assertFalse(plan.already_warm)


class _RecordingProvider:
    name = "test"

    def __init__(self):
        self.warmed = []
        self.unloaded = []

    def available(self):
        return True

    def warm(self, model):
        self.warmed.append(model)

    def unload(self, model):
        self.unloaded.append(model)


if __name__ == "__main__":
    unittest.main()
