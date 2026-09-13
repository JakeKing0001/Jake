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
| 7 | Dispatch grezzo | `SkillRegistry.execute` (`core/skill_registry.py`) | Parziale: da 13/09/2026 (F1.2.1) `policy_engine=None` e' FAIL-CLOSED (`POLICY_BLOCKED`, nessuna skill eseguita) invece di "nessun controllo"; solo `blocked_intents` viene ricontrollato qui (non una decisione interattiva/automatica completa - vedi nota sotto) | Nessuna: non e' un chokepoint del ledger |

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

## Nota sul percorso 7 (dispatch grezzo)

**Chiuso il 13/09/2026 (F1.2.1).** `SkillRegistry.execute()` non controllava MAI la policy da
solo - un chiamante che lo invocava direttamente, saltando `JakeCore._authorize_command()` (o
`PlanExecutor`/`decide_automated`), eseguiva la skill senza alcun controllo su `blocked_intents`.
Nei chiamanti di produzione reali (percorsi 1/2, rollback) questo non era gia' sfruttabile -
tutti passano gia' da una decisione di policy PRIMA di arrivare qui - ma era un default pericoloso
per un futuro chiamante che se lo dimenticasse, stesso principio "nega per default" gia' applicato
ai percorsi 3/6. Ora `policy_engine=None` e' **FAIL-CLOSED** (`SkillResult(success=False,
error="POLICY_BLOCKED")`, la skill non viene nemmeno chiamata): solo `blocked_intents` viene
ricontrollato (non una decisione interattiva/automatica completa - `CONFIRM`/`REQUIRE_AUTH` non
hanno senso in un dispatcher sincrono senza un utente pronto a rispondere), stesso identico
principio minimale gia' applicato a `rollback_effect()` (percorso 6). I chiamanti reali (percorsi
1/2 via `JakeCore._resolve_and_execute`/`_run_confirmed_action`, il rollback via i tre handler in
`core/execution_safety.py`, `PlanExecutor._execute_step`, `tools/replay_session.py::replay_one`,
il ripiego di default di `TaskAgent.__init__`) passano tutti ora il proprio `policy_engine`
esplicitamente. Prova: `tests/test_skill_registry.py::PolicyGateTests`,
`tests/test_action_paths_inventory.py::DocumentedSymbolsStillExistTests::
test_raw_dispatch_path_is_now_fail_closed_too`.

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

- `F1.2.1`: i tre "percorso N" dichiarati aperti sono ora tutti chiusi (percorso 3, percorso 6,
  percorso 7 - vedi le note sopra). Resta parziale nel senso gia' dichiarato per il percorso 7: il
  controllo li' e' solo su `blocked_intents`, non una decisione interattiva/automatica completa
  (CONFIRM/REQUIRE_AUTH non hanno senso in un dispatcher sincrono).
- `F1.2.5` (resto): sotto-azioni generate da workflow e autorizzazione completa dei rollback
  ancora aperte. Il retry ha la protezione conservativa di `F1.3.6`, non capability per risorsa.
- `F1.8.1` (parte "ownership della sessione" chiusa, 13/09/2026): `ConversationStateManager` (`core/conversation_state.py`) usa
  ora un dizionario `{canale: azione}` (`_pending_actions`, chiave `current_device_id()`) invece
  di un unico slot globale - due dispositivi con una propria conferma pendente nello stesso
  istante hanno ciascuno il proprio, senza sovrascriversi a vicenda (vedi
  `tests/test_conversation_state.py::PerChannelPendingActionTests` e, end-to-end con un vero
  `JakeCore.answer()`, `tests/test_jake_core_pipeline.py::PerChannelPendingActionIntegrationTests`).
  Resta aperta l'altra meta' del testo originale di F1.8.1, "una coda per azioni concorrenti": un
  meccanismo generale per serializzare azioni concorrenti non legate a una conferma pendente, mai
  affrontato.
- `F1.2.3` (prima capability - dispositivo - chiusa, 13/09/2026): `PolicyEngine` ora accetta
  `device_blocked_intents: {device_id: {intent, ...}}` (config.json, vuoto per default),
  controllato su ENTRAMBI i percorsi (interattivo e automatico) PRIMA della capability filesystem
  e di CONFIRM - un intent bloccato per un dispositivo si ferma sempre per quel dispositivo,
  "vince il piu' restrittivo" rispetto a cio' che permetterebbe la sola policy utente, senza
  toccare la voce locale o altri dispositivi (vedi `tests/test_policy_engine.py::
  DeviceCapabilityTests`). Resta aperta l'intersezione con le altre dimensioni (agente/skill/
  sessione) e le altre capability elencate in ROADMAP.md (app/contatto/dominio web/HA/rete/durata).
- Questo inventario copre "chi puo' eseguire", non ancora "chi costruisce un `ActionProposal`"
  (`F1.1.2`, `F1.1.6`, `F1.1.7`): `_authorize_command` costruisce/valida `ActionProposal`,
  mentre le skill restituiscono ancora `SkillResult` (vedi `core/skill_result.py`). La ricevuta viene sintetizzata
  solo ai 4 chokepoint della tabella sopra (righe 1-3, gia' coperti da
  `tests/test_action_contract.py`).
