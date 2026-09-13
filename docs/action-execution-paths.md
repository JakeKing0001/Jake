# Inventario dei percorsi di esecuzione azione (F1.1.1)

Questo documento risponde al primo passo di F1.1 — Action Contract 2.0
(vedi [ROADMAP_EXECUTION.md](../ROADMAP_EXECUTION.md)): enumerare tutti i percorsi con cui
un intent puo' arrivare a eseguire davvero, cosa decide se puo' farlo, e dove ne resta prova nel
ledger (`core/action_ledger.py::ActionReceipt`, vedi `tests/test_action_contract.py`).

Verificato leggendo il codice, non la prosa della roadmap (12/09/2026). `tests/
test_action_paths_inventory.py` controlla che i simboli citati esistano ancora: un rinominamento
o una rimozione fa fallire il test invece di lasciare questo documento silenziosamente non
aggiornato.

## Percorsi

| # | Percorso | Punto d'ingresso | Passa da PolicyEngine? | Ricevuta nel ledger |
|---|---|---|---|---|
| 1 | Comando diretto dell'utente e ripresa del consenso | `JakeCore._resolve_and_execute` / `_finalize_pending_action` (`core/jake_core.py`) | Si', `_authorize_command` usa `decide_interactive` anche dopo il consenso | `JakeCore._log_action_outcome`/`_log_denied_action` |
| 2 | Agente a passi (general/coding/research) | `TaskAgent.run` (`core/agent.py`), tramite l'`executor` iniettato = `JakeCore._resolve_and_execute` | Si', stesso gate del percorso 1 (nessuna logica propria) | `TaskAgent._log_step` |
| 3 | Piano automatico (ripiego del planner, `RUN_WORKFLOW`, trigger) | `PlanExecutor.execute` (`core/plan_executor.py`), chiamato da `JakeCore._try_plan`, `skills/workflow.py::RunWorkflowSkill`, `core/trigger_scheduler.py::TriggerScheduler` | Si', `decide_automated`, purche' il chiamante passi `policy_engine` (vedi nota sotto) | `PlanExecutor._log_step` |
| 4 | Companion server (rete locale) | `core/companion_server.py::_Handler._handle_command` | Si', delega a `command_handler` = `JakeCore.answer`, nessuna logica di esecuzione propria | Stessa del percorso 1/2 (qualunque cosa `answer()` risolva) |
| 5 | Registrazione skill (plugin loader, Skill Forge) | `core/plugin_loader.py`, `core/skill_forge.py` → `SkillRegistry.register_skill` | N/A: registra soltanto, non esegue | N/A |
| 6 | Rollback di un passo gia' riuscito | `core/execution_safety.py::rollback_effect`, chiamato da `TaskAgent._rollback` e `PlanExecutor._rollback` | Parziale: rifiuta se l'intent compensatorio e' in `blocked_intents` (F1.2.5, 11/09/2026) - da 13/09/2026 (F1.2.1) `policy_engine=None` e' FAIL-CLOSED (nessun rollback) invece di "nessun controllo", stesso principio del percorso 3; non passa da `decide_automated`/`decide_interactive` per intero, ne' da `CONFIRM`/`REQUIRE_AUTH` (nessun utente pronto a rispondere durante un rollback automatico) | Nessuna propria: il rollback stesso non produce un `ActionReceipt` separato, solo l'esecuzione del passo originale che l'ha innescato |
| 7 | Dispatch grezzo | `SkillRegistry.execute` (`core/skill_registry.py`) | **No**: dispatcher senza alcun controllo di policy proprio. Sicuro solo perche' oggi tutti i chiamanti reali (percorsi 1-3, rollback) lo invocano dopo una decisione gia' presa altrove - non e' pero' impedito strutturalmente che un futuro chiamante lo invochi direttamente, saltando ogni gate (`F1.2.1`, ancora aperto) | Nessuna: non e' un chokepoint del ledger |

## Ripresa del consenso (percorsi 1, 2 e 4)

`JakeCore._finalize_pending_action` rivaluta la policy vigente tramite `_authorize_command`,
lo stesso gate usato da `_resolve_and_execute`. Non applica rewrite o fallback: esegue solo il
bersaglio approvato. I marcatori `confirmed`/`authenticated`/`authenticated_via` della busta
vengono rimossi; il consenso deriva dalla risposta positiva e l'autenticazione dalla passphrase
controllata da `_handle_confirmation` o da una verifica Windows Hello effettiva. Un nuovo blocco
impedisce l'esecuzione, una nuova richiesta di autenticazione riapre l'attesa, senza perdere il
`trace_id`. Anche dopo il prompt Windows Hello viene ricontrollata la policy. Un blocco dopo
il consenso produce una ricevuta `authorization=blocked`; in modalita' privata non viene scritta.

Prove: `PendingActionPolicyTests` e `WindowsHelloAuthTests` in
`tests/test_jake_core_permissions.py`, `PendingPolicyIntegrationTests` in
`tests/test_jake_core_pipeline.py` (file temporaneo reale, ripresa agente, autenticazione e
conferma a due stadi). Restano aperti ownership della sessione e race tra valutazione ed effetto
(`F1.8.1`/`F1.8.7`): questa rivalutazione non e' una transazione globale.

## Nota sul percorso 3 (piano automatico)

**Chiuso parzialmente il 12/09/2026 (F1.2.1).** `PlanExecutor.execute(plan, policy_engine=None,
...)` trattava `policy_engine=None` come "nessun controllo" (`PolicyDecision.ALLOW` per ogni
passo) - un default sicuro solo perche' OGNI chiamante reale passava davvero il proprio
`policy_engine`, non perche' fosse strutturalmente impedito non farlo. Ora `policy_engine=None`
e' **FAIL-CLOSED**: ogni passo si ferma con `POLICY_BLOCKED` invece di eseguire (vedi
`core/plan_executor.py::execute`, `tests/test_plan_executor.py::
MissingPolicyEngineFailsClosedTests`). I tre chiamanti reali (`JakeCore._try_plan`,
`RunWorkflowSkill` dopo che `JakeCore.__init__` glielo assegna, `TriggerScheduler`) passano gia'
tutti un `policy_engine` vero, quindi il loro comportamento non cambia; cambia solo l'esito di
un quarto chiamante futuro che se ne dimenticasse, da "esegue tutto senza policy" a "si ferma su
ogni passo" - coerente con "minimo privilegio"/"negare per default" (`ROADMAP_EXECUTION.md`,
F1.2.4).

## Nota sul percorso 6 (rollback)

**Chiuso il 13/09/2026 (F1.2.1).** Stesso principio applicato a `core/execution_safety.py::
rollback_effect`: `policy_engine=None` trattava `blocked_intents` come "niente da controllare"
(il rollback eseguiva comunque) invece di FAIL-CLOSED. In produzione questo non era gia'
sfruttabile - `JakeCore.__init__` collega `policy_engine` a tutti e tre i `TaskAgent` (F1.2.5,
assegnato esplicitamente DOPO la creazione, perche' `PolicyEngine` non esiste ancora quando i tre
`TaskAgent` vengono costruiti) - ma un `TaskAgent`/chiamata a `rollback_effect` senza quel
collegamento esplicito (un test, uno strumento, un futuro chiamante) ora si ferma invece di
eseguire senza nessun controllo (vedi `tests/test_execution_safety.py::RollbackEdgeCaseTests::
test_policy_engine_none_is_fail_closed_not_no_restriction`).

## Identita' del dispositivo mittente (F1.2.3/F1.8.1, fondamenta)

**Aggiunto il 13/09/2026.** I quattro chokepoint del ledger (`JakeCore._log_action_outcome`/
`_log_denied_action`, `TaskAgent._log_step`, `PlanExecutor._log_step`) ora popolano un campo
`ActionReceipt.device_id` opzionale (`None` per un comando vocale locale o un'automazione in
background) con l'id del dispositivo companion che ha originato la richiesta - vedi
`core/request_context.py`. Propagato per THREAD (`contextvars.ContextVar`), non come parametro
esplicito lungo la catena `answer` → `_process` → ... → i quattro chokepoint: aggiungere un
parametro a ~10 firme intermedie solo per farlo arrivare a 4 punti finali avrebbe reso il
cambiamento molto piu' invasivo per lo stesso risultato. `core/companion_server.py::_Handler.
_handle_command` imposta il contesto per la durata della chiamata a `command_handler` se il body
include `device_id` (lo stesso id gia' usato per `/claim`); `ThreadingHTTPServer` gestisce ogni
richiesta sul proprio thread, quindi due richieste concorrenti da dispositivi diversi non si
vedono mai a vicenda il valore (verificato empiricamente, non solo assunto dalla documentazione di
`contextvars`, in `tests/test_request_context.py::ThreadIsolationTests` e end-to-end attraverso
l'intero server HTTP in `tests/test_companion_server.py`).

Solo VISIBILITA' nel ledger per ora, nessuna decisione di policy: `PolicyEngine` non legge ancora
questo valore per nessuna decisione - e' la fondamenta che F1.2.3 (intersezione permessi per
dispositivo) e F1.8.1 (identita' di canale per conferme concorrenti distinte) hanno bisogno prima
di poter esistere, non l'una o l'altra funzionalita' completa.

## Cosa resta aperto

- `F1.2.1` (parziale): il fail-open silenzioso del percorso 3 e' chiuso (vedi sopra). Resta
  aperto rendere impossibile, non solo evitato per convenzione, chiamare `SkillRegistry.
  execute()` senza una decisione di `PolicyEngine` gia' presa (percorso 7): oggi resta un
  dispatcher a basso livello usato anche da centinaia di test di skill in isolamento e da
  `tools/replay_session.py`, quindi renderlo fail-closed per costruzione richiede prima
  distinguere "chiamata di test/tool fidata" da "chiamata di produzione", non ancora deciso.
- `F1.2.5` (resto): sotto-azioni generate da workflow e autorizzazione completa dei rollback
  ancora aperte. Il retry ha la protezione conservativa di `F1.3.6`, non capability per risorsa.
- `F1.2.3`/`F1.8.1` (resto, ora che l'identita' del dispositivo esiste - vedi sopra): `PolicyEngine`
  non usa ancora `current_device_id()` per nessuna decisione (nessuna capability per-dispositivo,
  nessuna intersezione con quelle utente/agente/skill/sessione); `ConversationStateManager`
  (`core/conversation_state.py`) ha ancora UN solo slot di azione in sospeso globale, non uno per
  dispositivo/canale - due dispositivi con una propria conferma pendente nello stesso istante si
  sovrascrivono ancora a vicenda.
- Questo inventario copre "chi puo' eseguire", non ancora "chi costruisce un `ActionProposal`"
  (`F1.1.2`, `F1.1.6`, `F1.1.7`): `_authorize_command` costruisce/valida `ActionProposal`,
  mentre le skill restituiscono ancora `SkillResult` (vedi `core/skill_result.py`). La ricevuta viene sintetizzata
  solo ai 4 chokepoint della tabella sopra (righe 1-3, gia' coperti da
  `tests/test_action_contract.py`).
