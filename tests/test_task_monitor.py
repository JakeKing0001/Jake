"""F6.7: monitor di compiti lunghi senza spam di notifiche, e housekeeping senza cancellazione autonoma. Il
criterio di uscita ("30 giorni di pilot senza loop di notifica o azione distruttiva autonoma") non si puo'
simulare per 30 giorni: qui si prova la STRUTTURA che lo rende impossibile - idempotenza delle notifiche e un
`apply()` che non puo' mai girare senza un'approvazione legata esattamente al piano."""
import unittest

from core.task_monitor import (
    AutonomyBudgetTracker, BackupRecord, Category, FileAction, FileRecord, Finding, HousekeepingError,
    HousekeepingPlan, MonitorStore, SecurityCheck, Severity, TaskMonitorRegistry, TaskStatus, UnknownTaskError,
    UpdateRecord, apply, approve, available_updates, find_duplicates, propose, scan, security_posture,
    stale_backups,
)


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# ---- F6.7.1/F6.7.2 -----------------------------------------------------------------------------------------------------------


class MonitorLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.registry = TaskMonitorRegistry(clock=self.clock)

    def test_starting_a_task_tracks_it_as_running_with_no_event(self):
        task = self.registry.start("dl-1", "Download aggiornamento")
        self.assertEqual(task.status, TaskStatus.RUNNING)
        self.assertEqual(self.registry.drain_events(), [])

    def test_progress_updates_recency_silently(self):
        self.registry.start("dl-1", "Download")
        self.clock.advance(10)
        self.registry.progress("dl-1", "50%")
        self.assertEqual(self.registry.get("dl-1").message, "50%")
        self.assertEqual(self.registry.drain_events(), [])

    def test_completion_produces_exactly_one_event(self):
        self.registry.start("dl-1", "Download")
        self.registry.complete("dl-1", "fatto")
        events = self.registry.drain_events()
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0].task_id, events[0].status, events[0].message), ("dl-1", TaskStatus.COMPLETED, "fatto"))

    def test_draining_twice_without_a_new_transition_is_idempotent(self):
        """Il 'loop di notifica' che il criterio di uscita vieta: la stessa transizione non genera due eventi."""
        self.registry.start("dl-1", "Download")
        self.registry.complete("dl-1")
        self.assertEqual(len(self.registry.drain_events()), 1)
        self.assertEqual(self.registry.drain_events(), [])
        self.assertEqual(self.registry.drain_events(), [])

    def test_error_and_anomaly_and_decision_each_produce_one_event(self):
        for method, status in (("fail", TaskStatus.ERROR), ("flag_anomaly", TaskStatus.ANOMALY),
                               ("require_decision", TaskStatus.NEEDS_DECISION)):
            registry = TaskMonitorRegistry(clock=self.clock)
            registry.start("t", "task")
            getattr(registry, method)("t", "motivo")
            events = registry.drain_events()
            self.assertEqual(len(events), 1, method)
            self.assertEqual(events[0].status, status, method)

    def test_a_task_already_terminal_refuses_a_new_transition(self):
        self.registry.start("dl-1", "Download")
        self.registry.complete("dl-1")
        with self.assertRaises(ValueError):
            self.registry.complete("dl-1")
        with self.assertRaises(ValueError):
            self.registry.fail("dl-1", "x")
        with self.assertRaises(ValueError):
            self.registry.progress("dl-1")

    def test_an_anomaly_can_return_to_running_without_a_new_event(self):
        self.registry.start("dl-1", "Download")
        self.registry.flag_anomaly("dl-1", "rallentato")
        self.registry.drain_events()
        self.registry.resume_progress("dl-1", "ripreso")
        self.assertEqual(self.registry.get("dl-1").status, TaskStatus.RUNNING)
        self.assertEqual(self.registry.drain_events(), [])

    def test_an_anomaly_can_still_complete_or_fail_afterwards(self):
        self.registry.start("dl-1", "Download")
        self.registry.flag_anomaly("dl-1", "lento")
        self.registry.complete("dl-1")
        self.assertEqual([e.status for e in self.registry.drain_events()], [TaskStatus.ANOMALY, TaskStatus.COMPLETED])

    def test_unknown_task_operations_raise_a_typed_error(self):
        with self.assertRaises(UnknownTaskError):
            self.registry.progress("mai-esistito")
        with self.assertRaises(UnknownTaskError):
            self.registry.complete("mai-esistito")

    def test_duplicate_task_id_is_rejected(self):
        self.registry.start("dl-1", "A")
        with self.assertRaises(ValueError):
            self.registry.start("dl-1", "B")

    def test_is_stalled_only_for_a_running_task_past_the_threshold(self):
        self.registry.start("dl-1", "Download")
        self.assertFalse(self.registry.is_stalled("dl-1", stall_after_seconds=30))
        self.clock.advance(29)
        self.assertFalse(self.registry.is_stalled("dl-1", stall_after_seconds=30))
        self.clock.advance(1)
        self.assertTrue(self.registry.is_stalled("dl-1", stall_after_seconds=30))
        self.registry.complete("dl-1")
        self.assertFalse(self.registry.is_stalled("dl-1", stall_after_seconds=0))

    def test_a_stalled_task_does_not_auto_flag_itself_the_caller_decides(self):
        self.registry.start("dl-1", "Download")
        self.clock.advance(1000)
        self.assertTrue(self.registry.is_stalled("dl-1", stall_after_seconds=1))
        self.assertEqual(self.registry.get("dl-1").status, TaskStatus.RUNNING)
        self.assertEqual(self.registry.drain_events(), [])

    def test_active_lists_only_non_terminal_tasks(self):
        self.registry.start("a", "A")
        self.registry.start("b", "B")
        self.registry.complete("a")
        self.assertEqual([t.task_id for t in self.registry.active()], ["b"])

    def test_close_requires_a_terminal_status(self):
        self.registry.start("a", "A")
        with self.assertRaises(ValueError):
            self.registry.close("a")
        self.registry.complete("a")
        self.registry.close("a")
        with self.assertRaises(UnknownTaskError):
            self.registry.get("a")


# ---- F6.7.7 -------------------------------------------------------------------------------------------------------------------


class MonitorStoreTests(unittest.TestCase):
    def setUp(self):
        import shutil
        import tempfile
        from pathlib import Path
        self.tmp = Path(tempfile.mkdtemp(prefix="jake_task_monitor_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.store = MonitorStore(self.tmp / "monitors.json")

    def test_no_file_means_no_candidates(self):
        self.assertEqual(self.store.resume_candidates(), [])

    def test_only_non_terminal_tasks_survive_a_save(self):
        registry = TaskMonitorRegistry(clock=FakeClock())
        registry.start("running", "In corso")
        registry.start("done", "Finito")
        registry.complete("done")
        self.store.save(registry)
        candidates = self.store.resume_candidates()
        self.assertEqual([c["task_id"] for c in candidates], ["running"])

    def test_a_saved_task_survives_a_fresh_store_instance(self):
        registry = TaskMonitorRegistry(clock=FakeClock())
        registry.start("dl-1", "Download grosso")
        self.store.save(registry)
        reopened = MonitorStore(self.tmp / "monitors.json")
        candidates = reopened.resume_candidates()
        self.assertEqual(candidates[0]["label"], "Download grosso")

    def test_resume_candidates_never_resumes_anything_by_itself(self):
        """F6.7.7: solo dati, nessuna azione - questo test documenta l'assenza di un effetto, non solo una API."""
        registry = TaskMonitorRegistry(clock=FakeClock())
        registry.start("dl-1", "Download")
        self.store.save(registry)
        self.store.resume_candidates()
        self.store.resume_candidates()  # ripetibile senza side effect
        self.assertEqual(len(self.store.resume_candidates()), 1)

    def test_clear_removes_the_file(self):
        registry = TaskMonitorRegistry(clock=FakeClock())
        registry.start("dl-1", "Download")
        self.store.save(registry)
        self.store.clear()
        self.assertEqual(self.store.resume_candidates(), [])

    def test_a_corrupted_store_file_never_crashes_the_reader(self):
        self.store.path.parent.mkdir(parents=True, exist_ok=True)
        self.store.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(self.store.resume_candidates(), [])


# ---- F6.7.3 --------------------------------------------------------------------------------------------------------------------


class ScannerTests(unittest.TestCase):
    def test_duplicates_need_the_same_hash_and_size(self):
        files = [FileRecord("a.jpg", 100, "h1"), FileRecord("b.jpg", 100, "h1"), FileRecord("c.jpg", 100, "h2")]
        findings = find_duplicates(files)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].category, Category.DUPLICATE)
        self.assertEqual(findings[0].affected_paths, ("a.jpg", "b.jpg"))
        self.assertEqual(findings[0].estimated_bytes_reclaimed, 100)

    def test_a_single_file_is_never_a_duplicate(self):
        self.assertEqual(find_duplicates([FileRecord("a.jpg", 100, "h1")]), [])

    def test_same_hash_different_size_is_not_a_duplicate_group(self):
        files = [FileRecord("a", 100, "h"), FileRecord("b", 200, "h")]
        self.assertEqual(find_duplicates(files), [])

    def test_stale_backups_use_severity_by_how_stale(self):
        now = 1_000_000.0
        backups = [BackupRecord("nas", now - 8 * 86400), BackupRecord("cloud", now - 30 * 86400),
                  BackupRecord("recente", now - 86400)]
        findings = stale_backups(backups, now, max_age_seconds=7 * 86400)
        self.assertEqual({f.description.split("'")[1] for f in findings}, {"nas", "cloud"})
        by_name = {f.description.split("'")[1]: f.severity for f in findings}
        self.assertEqual(by_name["nas"], Severity.MEDIUM)
        self.assertEqual(by_name["cloud"], Severity.HIGH)

    def test_updates_flag_security_relevant_ones_as_high_severity(self):
        updates = [UpdateRecord("app", "1.0", "1.0"), UpdateRecord("lib", "1.0", "1.1"),
                  UpdateRecord("openssl", "1.0", "1.1", security_relevant=True)]
        findings = available_updates(updates)
        self.assertEqual(len(findings), 2)
        by_name = {f.description.split("'")[1]: f.severity for f in findings}
        self.assertEqual(by_name["lib"], Severity.LOW)
        self.assertEqual(by_name["openssl"], Severity.HIGH)

    def test_only_failed_security_checks_become_findings(self):
        checks = [SecurityCheck("firewall", True), SecurityCheck("antivirus", False, "disattivato", Severity.HIGH)]
        findings = security_posture(checks)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].category, Category.SECURITY_ISSUE)
        self.assertEqual(findings[0].severity, Severity.HIGH)

    def test_scan_combines_every_category_and_omits_unprovided_ones(self):
        findings = scan(files=[FileRecord("a", 1, "h"), FileRecord("b", 1, "h")], now=1_000_000.0)
        self.assertEqual({f.category for f in findings}, {Category.DUPLICATE})

    def test_scan_with_nothing_returns_nothing(self):
        self.assertEqual(scan(), [])

    def test_finding_ids_are_deterministic_and_stable(self):
        files = [FileRecord("a", 1, "h"), FileRecord("b", 1, "h")]
        self.assertEqual(find_duplicates(files)[0].id, find_duplicates(files)[0].id)


# ---- F6.7.4/F6.7.5 ---------------------------------------------------------------------------------------------------------------


class PlanTests(unittest.TestCase):
    def duplicate_finding(self) -> Finding:
        return find_duplicates([FileRecord("a.jpg", 1000, "h"), FileRecord("b.jpg", 1000, "h"), FileRecord("c.jpg", 1000, "h")])[0]

    def test_a_plan_with_no_opt_in_has_no_actions_even_with_duplicate_findings(self):
        plan = propose([self.duplicate_finding()])
        self.assertEqual(plan.actions, ())
        self.assertIn("0 azioni", plan.preview())

    def test_opting_in_deletes_every_copy_but_the_first(self):
        plan = propose([self.duplicate_finding()], delete_duplicates=True)
        self.assertEqual([a.path for a in plan.actions], ["b.jpg", "c.jpg"])
        self.assertTrue(all(a.kind == "delete" for a in plan.actions))

    def test_backup_and_update_and_security_findings_never_produce_an_action(self):
        backup_finding = stale_backups([BackupRecord("nas", 0)], now=1_000_000, max_age_seconds=1)[0]
        plan = propose([backup_finding], delete_duplicates=True)
        self.assertEqual(plan.actions, ())

    def test_the_digest_changes_when_the_actions_change(self):
        plan_a = propose([self.duplicate_finding()], delete_duplicates=True)
        plan_b = propose([self.duplicate_finding()], delete_duplicates=False)
        self.assertNotEqual(plan_a.digest, plan_b.digest)

    def test_preview_names_every_path_and_never_executes_anything(self):
        plan = propose([self.duplicate_finding()], delete_duplicates=True)
        text = plan.preview()
        self.assertIn("b.jpg", text)
        self.assertIn("c.jpg", text)
        self.assertIn("delete", text)

    def test_file_action_validates_its_own_shape(self):
        with self.assertRaises(ValueError):
            FileAction("wipe", "x", 0)
        with self.assertRaises(ValueError):
            FileAction("move", "x", 0)  # manca destination
        FileAction("move", "x", 0, destination="y")  # non solleva


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.executed: list[FileAction] = []
        self.plan = propose(find_duplicates([FileRecord("a", 10, "h"), FileRecord("b", 10, "h")]), delete_duplicates=True)

    def executor(self, action: FileAction) -> None:
        self.executed.append(action)

    def test_apply_without_approval_never_calls_the_executor(self):
        with self.assertRaises(HousekeepingError) as ctx:
            apply(self.plan, None, self.executor)
        self.assertEqual(ctx.exception.code, "approval_required")
        self.assertEqual(self.executed, [])

    def test_apply_with_an_approval_for_a_different_plan_is_refused(self):
        other_plan = propose([], delete_duplicates=True)
        approval = approve(other_plan, "davide")
        with self.assertRaises(HousekeepingError) as ctx:
            apply(self.plan, approval, self.executor)
        self.assertEqual(ctx.exception.code, "approval_required")
        self.assertEqual(self.executed, [])

    def test_a_valid_approval_runs_every_action_through_the_injected_executor(self):
        approval = approve(self.plan, "davide")
        applied = apply(self.plan, approval, self.executor)
        self.assertEqual(applied, list(self.plan.actions))
        self.assertEqual(self.executed, list(self.plan.actions))

    def test_approve_requires_a_named_actor(self):
        with self.assertRaises(ValueError):
            approve(self.plan, "  ")

    def test_altering_the_plan_after_approval_invalidates_it(self):
        approval = approve(self.plan, "davide")
        bigger_plan = HousekeepingPlan(self.plan.findings, self.plan.actions + (FileAction("delete", "extra", 5),), self.plan.created_at)
        with self.assertRaises(HousekeepingError):
            apply(bigger_plan, approval, self.executor)

    def test_the_module_never_imports_a_real_filesystem_deletion_primitive(self):
        import ast
        from pathlib import Path
        tree = ast.parse(Path("core/task_monitor.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertNotIn("shutil", imported)


# ---- F6.7.6 --------------------------------------------------------------------------------------------------------------------


class BudgetTests(unittest.TestCase):
    def test_a_plan_within_the_budget_applies_and_is_recorded(self):
        clock = FakeClock()
        budget = AutonomyBudgetTracker({"actions": 10, "bytes": 10_000}, window_seconds=3600, clock=clock)
        plan = propose(find_duplicates([FileRecord("a", 100, "h"), FileRecord("b", 100, "h")]), delete_duplicates=True)
        applied = apply(plan, approve(plan, "davide"), lambda action: None, budget=budget)
        self.assertEqual(len(applied), 1)
        self.assertEqual(budget.used(), {"actions": 1, "bytes": 100})

    def test_a_plan_over_budget_is_refused_before_a_single_action_runs(self):
        clock = FakeClock()
        budget = AutonomyBudgetTracker({"bytes": 50}, clock=clock)
        plan = propose(find_duplicates([FileRecord("a", 100, "h"), FileRecord("b", 100, "h")]), delete_duplicates=True)
        executed = []
        with self.assertRaises(HousekeepingError) as ctx:
            apply(plan, approve(plan, "davide"), executed.append, budget=budget)
        self.assertEqual(ctx.exception.code, "budget_exceeded")
        self.assertEqual(executed, [])
        self.assertEqual(budget.used(), {"bytes": 0})

    def test_usage_expires_outside_the_window(self):
        clock = FakeClock()
        budget = AutonomyBudgetTracker({"actions": 1}, window_seconds=60, clock=clock)
        budget.record({"actions": 1})
        self.assertEqual(budget.would_exceed({"actions": 1}), ["actions"])
        clock.advance(61)
        self.assertEqual(budget.would_exceed({"actions": 1}), [])

    def test_multiple_dimensions_are_tracked_independently(self):
        budget = AutonomyBudgetTracker({"actions": 100, "network": 1}, clock=FakeClock())
        budget.record({"actions": 5})
        self.assertEqual(budget.would_exceed({"network": 2}), ["network"])
        self.assertEqual(budget.would_exceed({"actions": 50}), [])

    def test_an_unbudgeted_dimension_never_blocks_anything(self):
        budget = AutonomyBudgetTracker({"actions": 1}, clock=FakeClock())
        self.assertEqual(budget.would_exceed({"mystery": 1_000_000}), [])


if __name__ == "__main__":
    unittest.main()
