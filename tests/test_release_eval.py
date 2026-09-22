"""F8.6: golden set/security set, confronto con la stabile, canary, rollback, canali firmati, report. Firme Ed25519
VERE (`cryptography`); il criterio di uscita ("una release regressiva non raggiunge stable") e' provato end-to-end
attraverso `ReleaseRegistry.publish`, non solo sulla funzione di confronto isolata."""
import time
import unittest

from core.release_eval import (
    CaseResult, EvalReport, GoldenCase, GoldenSetError, ReleaseError, ReleaseManifest, ReleaseRegistry,
    ReleaseSigningKey, ReleaseTrustStore, RollbackPolicy, Suite, canary_sample, compare, decide_rollback,
    render_report, require_golden_set, run_eval, summarize, validate_golden_set,
)

ALWAYS_TRUE = lambda output: True  # noqa: E731
IS_TRUE = lambda output: output is True  # noqa: E731


def case(case_id, suite=Suite.NLU, check=ALWAYS_TRUE, security=False, input_=None) -> GoldenCase:
    return GoldenCase(case_id, suite, input_, check, security)


def full_coverage_set(**overrides) -> list[GoldenCase]:
    cases = [case(f"{suite.value}-1", suite) for suite in Suite]
    cases.append(case("sec-1", Suite.NLU, security=True))
    return cases


class GoldenSetValidationTests(unittest.TestCase):
    def test_a_set_covering_all_five_suites_with_a_security_case_is_valid(self):
        self.assertEqual(validate_golden_set(full_coverage_set()), [])
        require_golden_set(full_coverage_set())  # non solleva

    def test_missing_suite_coverage_is_reported(self):
        cases = [c for c in full_coverage_set() if c.suite != Suite.VOICE]
        errors = validate_golden_set(cases)
        self.assertTrue(any("voice" in e for e in errors))

    def test_zero_security_cases_is_invalid_even_with_full_suite_coverage(self):
        cases = [c for c in full_coverage_set() if not c.security]
        errors = validate_golden_set(cases)
        self.assertTrue(any(e.startswith("empty_security_set") for e in errors))

    def test_duplicate_and_empty_ids_are_reported(self):
        cases = full_coverage_set() + [case("nlu-1", Suite.MEMORY)]
        self.assertTrue(any("duplicate_case_id" in e for e in validate_golden_set(cases)))
        self.assertTrue(any("empty_case_id" in e for e in validate_golden_set([case("")])))

    def test_an_empty_set_is_invalid(self):
        self.assertTrue(validate_golden_set([]))

    def test_require_raises_a_typed_error_with_every_problem(self):
        with self.assertRaises(GoldenSetError) as ctx:
            require_golden_set([])
        self.assertTrue(ctx.exception.errors)


class RunEvalTests(unittest.TestCase):
    def test_passing_and_failing_checks_are_recorded_without_crashing(self):
        cases = [case("a", check=IS_TRUE, input_=True), case("b", check=IS_TRUE, input_=False)]
        report = run_eval("1.0.0", lambda x: x, cases)
        self.assertEqual([(r.case_id, r.passed, r.crashed) for r in report.results],
                         [("a", True, False), ("b", False, False)])

    def test_a_runner_exception_becomes_a_crashed_result_and_does_not_stop_the_run(self):
        def flaky(value):
            if value == "boom":
                raise RuntimeError("segreto interno di debug")
            return value

        cases = [case("crash", check=IS_TRUE, input_="boom"), case("after", check=IS_TRUE, input_=True)]
        report = run_eval("1.0.0", flaky, cases)
        crashed, after = report.results
        self.assertEqual((crashed.passed, crashed.crashed), (False, True))
        self.assertIn("RuntimeError", crashed.error)
        self.assertEqual((after.passed, after.crashed), (True, False))

    def test_a_broken_check_function_is_also_a_crash_not_a_silent_pass(self):
        def bad_check(output):
            raise ValueError("controllo rotto")

        report = run_eval("1.0.0", lambda x: x, [case("bad", check=bad_check, input_=1)])
        self.assertEqual((report.results[0].passed, report.results[0].crashed), (False, True))

    def test_report_aggregates_are_correct(self):
        cases = [case("a", Suite.NLU, IS_TRUE, input_=True), case("b", Suite.NLU, IS_TRUE, input_=False),
                 case("c", Suite.MEMORY, IS_TRUE, input_=True), case("sec", Suite.NLU, IS_TRUE, True, input_=True)]
        report = run_eval("1.0.0", lambda x: x, cases)
        self.assertAlmostEqual(report.suite_pass_rate(Suite.NLU), 2 / 3)
        self.assertEqual(report.suite_pass_rate(Suite.MEMORY), 1.0)
        self.assertIsNone(report.suite_pass_rate(Suite.VOICE))
        self.assertAlmostEqual(report.overall_pass_rate(), 3 / 4)
        self.assertTrue(report.all_security_passed())
        self.assertEqual({r.case_id for r in report.failures()}, {"b"})

    def test_zero_security_cases_means_security_is_not_passed(self):
        report = run_eval("1.0.0", lambda x: x, [case("a", check=IS_TRUE, input_=True)])
        self.assertFalse(report.all_security_passed())

    def test_crash_rate_counts_only_crashes_not_ordinary_failures(self):
        def runner(value):
            if value == "crash":
                raise RuntimeError()
            return value
        cases = [case("a", check=IS_TRUE, input_="crash"), case("b", check=IS_TRUE, input_=False), case("c", check=IS_TRUE, input_=True)]
        report = run_eval("1.0.0", runner, cases)
        self.assertAlmostEqual(report.crash_rate(), 1 / 3)


class CompareTests(unittest.TestCase):
    def report(self, release, rates: dict) -> EvalReport:
        results = []
        for suite, rate in rates.items():
            total = 10
            passed = round(total * rate)
            for index in range(total):
                results.append(CaseResult(f"{suite.value}-{index}", suite, False, index < passed, False, 1.0))
        return EvalReport(release, tuple(results), time.time(), 1.0)

    def test_no_regression_when_candidate_matches_or_beats_stable(self):
        stable = self.report("stable", {Suite.NLU: 0.9})
        candidate = self.report("cand", {Suite.NLU: 0.95})
        self.assertEqual(compare(stable, candidate), [])

    def test_a_drop_beyond_the_threshold_is_a_regression(self):
        stable = self.report("stable", {Suite.NLU: 0.9})
        candidate = self.report("cand", {Suite.NLU: 0.8})
        regressions = compare(stable, candidate, max_regression=0.05)
        self.assertEqual([(r.suite, round(r.delta, 2)) for r in regressions], [(Suite.NLU, -0.1)])

    def test_a_drop_within_the_threshold_is_not_a_regression(self):
        stable = self.report("stable", {Suite.NLU: 0.9})
        candidate = self.report("cand", {Suite.NLU: 0.87})
        self.assertEqual(compare(stable, candidate, max_regression=0.05), [])

    def test_a_suite_missing_from_the_candidate_counts_as_zero(self):
        stable = self.report("stable", {Suite.MEMORY: 0.9})
        candidate = self.report("cand", {Suite.NLU: 1.0})
        regressions = compare(stable, candidate)
        self.assertEqual(len(regressions), 1)
        self.assertEqual(regressions[0].candidate_pass_rate, 0.0)

    def test_only_worsened_suites_are_reported_not_improved_ones(self):
        stable = self.report("stable", {Suite.NLU: 0.9, Suite.MEMORY: 0.5})
        candidate = self.report("cand", {Suite.NLU: 0.5, Suite.MEMORY: 0.9})
        regressions = compare(stable, candidate)
        self.assertEqual([r.suite for r in regressions], [Suite.NLU])


class CanaryTests(unittest.TestCase):
    def test_security_cases_are_never_included(self):
        cases = full_coverage_set()
        sample = canary_sample(cases, fraction=1.0)
        self.assertFalse(any(c.security for c in sample))

    def test_the_fraction_controls_the_sample_size(self):
        cases = [case(f"n{i}") for i in range(20)]
        self.assertEqual(len(canary_sample(cases, 0.0)), 0)
        self.assertEqual(len(canary_sample(cases, 1.0)), 20)
        self.assertEqual(len(canary_sample(cases, 0.5)), 10)

    def test_the_same_seed_gives_the_same_sample_a_different_seed_may_differ(self):
        cases = [case(f"n{i}") for i in range(30)]
        first = canary_sample(cases, 0.4, seed=42)
        second = canary_sample(cases, 0.4, seed=42)
        self.assertEqual([c.id for c in first], [c.id for c in second])
        third = canary_sample(cases, 0.4, seed=1)
        self.assertNotEqual([c.id for c in first], [c.id for c in third])

    def test_an_out_of_range_fraction_is_rejected(self):
        with self.assertRaises(ValueError):
            canary_sample([case("a")], 1.5)
        with self.assertRaises(ValueError):
            canary_sample([case("a")], -0.1)

    def test_an_empty_pool_gives_an_empty_sample(self):
        self.assertEqual(canary_sample([case("sec", security=True)], 1.0), [])


class RollbackTests(unittest.TestCase):
    def good_report(self, release="1.1.0") -> EvalReport:
        cases = full_coverage_set()
        return run_eval(release, lambda x: x, cases)

    def test_a_clean_release_does_not_roll_back(self):
        decision = decide_rollback(self.good_report())
        self.assertEqual(decision, decide_rollback(self.good_report()))
        self.assertFalse(decision.should_rollback)

    def test_any_security_failure_alone_triggers_rollback(self):
        cases = [case("sec-1", security=True, check=IS_TRUE, input_=False), case("n-1", Suite.AGENTS),
                 case("n-2", Suite.MEMORY), case("n-3", Suite.VOICE), case("n-4", Suite.COMPUTER_USE)]
        report = run_eval("1.1.0", lambda x: x, cases)
        decision = decide_rollback(report)
        self.assertTrue(decision.should_rollback)
        self.assertTrue(any("security_failure" in r for r in decision.reasons))

    def test_a_crash_rate_above_the_threshold_triggers_rollback_alone(self):
        def crasher(value):
            raise RuntimeError()
        report = run_eval("1.1.0", crasher, full_coverage_set())
        decision = decide_rollback(report, RollbackPolicy(max_crash_rate=0.0))
        self.assertTrue(decision.should_rollback)
        self.assertTrue(any("crash_rate" in r for r in decision.reasons))

    def test_a_regression_against_stable_triggers_rollback_alone(self):
        stable_cases = full_coverage_set() + [case(f"extra-{i}", Suite.NLU, IS_TRUE, input_=True) for i in range(20)]
        stable = run_eval("1.0.0", lambda x: x, stable_cases)
        degraded = [case(f"extra-{i}", Suite.NLU, IS_TRUE, input_=(i < 5)) for i in range(20)] + [
            case(s.value, s, ALWAYS_TRUE, security=(s == Suite.NLU)) for s in Suite]
        candidate = run_eval("1.1.0", lambda x: x, degraded)
        decision = decide_rollback(candidate, stable=stable)
        self.assertTrue(decision.should_rollback)
        self.assertTrue(any("regression" in r for r in decision.reasons))

    def test_reasons_can_stack_independently(self):
        def crasher(value):
            raise RuntimeError()
        cases = [case("sec-1", security=True, check=IS_TRUE, input_=False)] + [case(f"n-{s.value}", s) for s in Suite]
        report = run_eval("1.1.0", crasher, cases)
        decision = decide_rollback(report)
        self.assertGreaterEqual(len(decision.reasons), 2)


class SummarizeTests(unittest.TestCase):
    def test_summary_matches_the_report(self):
        report = run_eval("1.2.3", lambda x: x, full_coverage_set())
        summary = summarize(report)
        self.assertEqual(summary["release"], "1.2.3")
        self.assertEqual(summary["security_passed"], report.all_security_passed())
        self.assertIn("nlu", summary["suites"])


class SigningTests(unittest.TestCase):
    def setUp(self):
        self.key = ReleaseSigningKey.generate()
        self.trust = ReleaseTrustStore()
        self.trust.add(self.key)

    def manifest(self, **overrides) -> ReleaseManifest:
        base = {"channel": "dev", "version": "1.0.0", "build_digest": "a" * 64,
                "eval_summary": {"security_passed": True, "crash_rate": 0.0, "overall_pass_rate": 1.0, "suites": {}},
                "previous_version": None, "created_at": 1_000_000.0}
        base.update(overrides)
        return ReleaseManifest(**base)

    def test_a_signature_from_a_trusted_key_verifies(self):
        manifest = self.manifest()
        self.trust.verify(manifest, self.key.sign(manifest))  # non solleva

    def test_an_altered_manifest_field_invalidates_the_signature(self):
        manifest = self.manifest()
        signature = self.key.sign(manifest)
        altered = self.manifest(version="9.9.9")
        with self.assertRaises(ReleaseError) as ctx:
            self.trust.verify(altered, signature)
        self.assertEqual(ctx.exception.code, "bad_signature")

    def test_an_unknown_or_revoked_signer_is_rejected(self):
        manifest = self.manifest()
        stranger = ReleaseSigningKey.generate()
        with self.assertRaises(ReleaseError) as ctx:
            self.trust.verify(manifest, stranger.sign(manifest))
        self.assertEqual(ctx.exception.code, "unknown_signer")
        self.trust.revoke(self.key.key_id)
        with self.assertRaises(ReleaseError) as ctx:
            self.trust.verify(manifest, self.key.sign(manifest))
        self.assertEqual(ctx.exception.code, "signer_revoked")

    def test_malformed_signatures_are_rejected(self):
        manifest = self.manifest()
        for bad in (None, {}, {"v": 2, "key_id": self.key.key_id, "signature": "AA=="}, "x"):
            with self.assertRaises(ReleaseError):
                self.trust.verify(manifest, bad)


class ReleaseRegistryTests(unittest.TestCase):
    def setUp(self):
        self.key = ReleaseSigningKey.generate()
        self.trust = ReleaseTrustStore()
        self.trust.add(self.key)
        self.registry = ReleaseRegistry(self.trust)

    def manifest(self, channel, version, previous=None, security_passed=True, crash_rate=0.0, suites=None) -> ReleaseManifest:
        return ReleaseManifest(channel, version, "d" * 64,
                               {"security_passed": security_passed, "crash_rate": crash_rate,
                                "overall_pass_rate": 1.0, "suites": suites or {}}, previous, 1_000_000.0)

    def publish(self, manifest):
        self.registry.publish(manifest, self.key.sign(manifest))

    def test_publishing_to_dev_has_no_quality_gate(self):
        self.publish(self.manifest("dev", "0.1.0", security_passed=False, crash_rate=1.0))
        self.assertEqual(self.registry.current("dev").version, "0.1.0")

    def test_stable_rejects_a_release_with_security_failures(self):
        with self.assertRaises(ReleaseError) as ctx:
            self.publish(self.manifest("stable", "1.0.0", security_passed=False))
        self.assertEqual(ctx.exception.code, "blocked")
        self.assertIsNone(self.registry.current("stable"))

    def test_stable_rejects_a_release_above_the_crash_threshold(self):
        with self.assertRaises(ReleaseError):
            self.publish(self.manifest("stable", "1.0.0", crash_rate=0.1))

    def test_a_regressive_release_never_reaches_stable(self):
        """Criterio di uscita di F8.6, provato end-to-end su publish()."""
        self.publish(self.manifest("stable", "1.0.0", suites={"nlu": 0.9}))
        with self.assertRaises(ReleaseError) as ctx:
            self.publish(self.manifest("stable", "1.1.0", previous="1.0.0", suites={"nlu": 0.7}))
        self.assertEqual(ctx.exception.code, "blocked")
        self.assertEqual(self.registry.current("stable").version, "1.0.0")  # ancora la vecchia

    def test_a_non_regressive_release_reaches_stable(self):
        self.publish(self.manifest("stable", "1.0.0", suites={"nlu": 0.9}))
        self.publish(self.manifest("stable", "1.1.0", previous="1.0.0", suites={"nlu": 0.91}))
        self.assertEqual(self.registry.current("stable").version, "1.1.0")

    def test_publishing_requires_declaring_the_exact_previous_version(self):
        self.publish(self.manifest("beta", "1.0.0"))
        with self.assertRaises(ReleaseError) as ctx:
            self.publish(self.manifest("beta", "1.1.0", previous="0.9.0"))
        self.assertEqual(ctx.exception.code, "previous_version_mismatch")

    def test_an_untrusted_signature_is_rejected_before_touching_the_channel(self):
        stranger = ReleaseSigningKey.generate()
        manifest = self.manifest("dev", "0.1.0")
        with self.assertRaises(ReleaseError):
            self.registry.publish(manifest, stranger.sign(manifest))
        self.assertIsNone(self.registry.current("dev"))

    def test_unknown_channel_is_rejected(self):
        with self.assertRaises(ReleaseError):
            self.publish(self.manifest("nightly", "0.1.0"))

    def test_rollback_returns_to_the_previous_release(self):
        self.publish(self.manifest("beta", "1.0.0"))
        self.publish(self.manifest("beta", "1.1.0", previous="1.0.0"))
        restored = self.registry.rollback("beta")
        self.assertEqual(restored.version, "1.0.0")
        self.assertEqual(self.registry.current("beta").version, "1.0.0")

    def test_rollback_without_a_previous_release_is_refused(self):
        self.publish(self.manifest("beta", "1.0.0"))
        with self.assertRaises(ReleaseError) as ctx:
            self.registry.rollback("beta")
        self.assertEqual(ctx.exception.code, "no_previous_release")

    def test_promote_builds_a_manifest_from_the_source_channel_and_only_moves_forward(self):
        self.publish(self.manifest("dev", "2.0.0", suites={"nlu": 0.95}))
        to_beta = self.registry.promote("dev", "beta")
        self.assertEqual((to_beta.channel, to_beta.version, to_beta.previous_version), ("beta", "2.0.0", None))
        self.publish(to_beta)
        to_stable = self.registry.promote("beta", "stable")
        self.publish(to_stable)
        self.assertEqual(self.registry.current("stable").version, "2.0.0")
        with self.assertRaises(ReleaseError):
            self.registry.promote("dev", "stable")  # salta beta
        with self.assertRaises(ReleaseError):
            self.registry.promote("stable", "dev")  # a ritroso

    def test_promoting_with_nothing_published_on_the_source_is_refused(self):
        with self.assertRaises(ReleaseError) as ctx:
            self.registry.promote("dev", "beta")
        self.assertEqual(ctx.exception.code, "no_release_to_promote")

    def test_history_keeps_every_published_manifest_per_channel(self):
        self.publish(self.manifest("dev", "0.1.0"))
        self.publish(self.manifest("dev", "0.2.0", previous="0.1.0"))
        self.assertEqual([m.version for m in self.registry.history("dev")], ["0.1.0", "0.2.0"])
        self.assertEqual(self.registry.history("stable"), [])


class RenderReportTests(unittest.TestCase):
    def test_the_report_names_every_failure_and_is_deterministic(self):
        cases = [case("ok", Suite.NLU, IS_TRUE, input_=True), case("broken", Suite.MEMORY, IS_TRUE, input_=False),
                 case("sec", Suite.NLU, IS_TRUE, True, input_=True)]
        report = run_eval("1.0.0", lambda x: x, cases)
        text = render_report(report)
        self.assertIn("Release 1.0.0", text)
        self.assertIn("broken", text)
        self.assertIn("TUTTI superati", text)
        self.assertEqual(text, render_report(report))

    def test_a_crashed_case_is_marked_distinctly_from_an_ordinary_failure(self):
        def crasher(value):
            raise RuntimeError("interno")
        report = run_eval("1.0.0", crasher, [case("boom", input_=1)])
        self.assertIn("CRASH", render_report(report))


if __name__ == "__main__":
    unittest.main()
