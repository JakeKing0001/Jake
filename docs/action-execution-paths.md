# Inventario dei percorsi di esecuzione azione (F1.1.1)

Questo documento risponde al primo passo di F1.1 — Action Contract 2.0
(vedi [ROADMAP_EXECUTION.md](../ROADMAP_EXECUTION.md)): enumerare tutti i percorsi con cui
un intent puo' arrivare a eseguire davvero, cosa decide se puo' farlo, e dove ne resta prova nel
ledger (`core/action_ledger.py::ActionReceipt`, vedi `tests/test_action_contract.py`).

Verificato leggendo il codice, non la prosa della roadmap (11/09/2026). `tests/
test_action_paths_inventory.py` controlla che i simboli citati esistano ancora: un rinominamento
o una rimozione fa fallire il test invece di lasciare questo documento silenziosamente non
aggiornato.

## Percorsi

| # | Percorso | Punto d'ingresso | Passa da PolicyEngine? | Ricevuta nel ledger |
|---|---|---|---|---|
| 1 | Comando diretto dell'utente | `JakeCore._resolve_and_execute` (`core/jake_core.py`) | Si', `decide_interactive` | `JakeCore._log_action_outcome`/`_log_denied_action` |
| 2 | Agente a passi (general/coding/research) | `TaskAgent.run` (`core/agent.py`), tramite l'`executor` iniettato = `JakeCore._resolve_and_execute` | Si', stesso gate del percorso 1 (nessuna logica propria) | `TaskAgent._log_step` |
| 3 | Piano automatico (ripiego del planner, `RUN_WORKFLOW`, trigger) | `PlanExecutor.execute` (`core/plan_executor.py`), chiamato da `JakeCore._try_plan`, `skills/workflow.py::RunWorkflowSkill`, `core/trigger_scheduler.py::TriggerScheduler` | Si', `decide_automated`, purche' il chiamante passi `policy_engine` (vedi nota sotto) | `PlanExecutor._log_step` |
| 4 | Companion server (rete locale) | `core/companion_server.py::_Handler._handle_command` | Si', delega a `command_handler` = `JakeCore.answer`, nessuna logica di esecuzione propria | Stessa del percorso 1/2 (qualunque cosa `answer()` risolva) |
| 5 | Registrazione skill (plugin loader, Skill Forge) | `core/plugin_loader.py`, `core/skill_forge.py` → `SkillRegistry.register_skill` | N/A: registra soltanto, non esegue | N/A |
| 6 | Rollback di un passo gia' riuscito | `core/execution_safety.py::rollback_effect`, chiamato da `TaskAgent._rollback` e `PlanExecutor._rollback` | Parziale: rifiuta solo se l'intent compensatorio e' in `blocked_intents` (F1.2.5, 11/09/2026); non passa da `decide_automated`/`decide_interactive` per intero, ne' da `CONFIRM`/`REQUIRE_AUTH` (nessun utente pronto a rispondere durante un rollback automatico) | Nessuna propria: il rollback stesso non produce un `ActionReceipt` separato, solo l'esecuzione del passo originale che l'ha innescato |
| 7 | Dispatch grezzo | `SkillRegistry.execute` (`core/skill_registry.py`) | **No**: dispatcher senza alcun controllo di policy proprio. Sicuro solo perche' oggi tutti i chiamanti reali (percorsi 1-3, rollback) lo invocano dopo una decisione gia' presa altrove - non e' pero' impedito strutturalmente che un futuro chiamante lo invochi direttamente, saltando ogni gate (`F1.2.1`, ancora aperto) | Nessuna: non e' un chokepoint del ledger |

## Nota sul percorso 3 (piano automatico)

`PlanExecutor.execute(plan, policy_engine=None, ...)` tratta `policy_engine=None` come "nessun
controllo" (`PolicyDecision.ALLOW` per ogni passo) - un default sicuro solo se OGNI chiamante
reale passa davvero il proprio `policy_engine`. Oggi lo fanno tutti e tre
(`JakeCore._try_plan`, `RunWorkflowSkill` dopo che `JakeCore.__init__` glielo assegna,
`TriggerScheduler`), ma nulla nel tipo lo garantisce: un quarto chiamante futuro che se ne
dimenticasse eseguirebbe silenziosamente senza policy. Stesso principio per
`TaskAgent.policy_engine`/rollback (percorso 6): opzionale, collegato esplicitamente da
`JakeCore.__init__` dopo aver creato `self.policy_engine`.

## Cosa resta aperto

- `F1.2.1`: rendere impossibile, non solo evitato per convenzione, chiamare `SkillRegistry.
  execute()` senza una decisione di `PolicyEngine` gia' presa (percorso 7).
- `F1.2.5` (resto): retry (`execute_with_retry`) e sotto-azioni generate da workflow non ancora
  passati in rassegna con lo stesso livello di dettaglio del rollback.
- Questo inventario copre "chi puo' eseguire", non ancora "chi costruisce un `ActionProposal`"
  (`F1.1.2`, `F1.1.6`, `F1.1.7`): oggi nessuna skill costruisce il contratto target, tutte
  restituiscono `SkillResult` (vedi `core/skill_result.py`) e la ricevuta viene sintetizzata
  solo ai 4 chokepoint della tabella sopra (righe 1-3, gia' coperti da
  `tests/test_action_contract.py`).
