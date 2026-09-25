"""Eseguito una sola volta, prima di importare qualunque modulo tests.test_* (Python importa
sempre il pacchetto genitore per primo): reindirizza i log di produzione (core/logger.py,
core/session_recorder.py, core/action_ledger.py) verso una cartella temporanea per l'intera
sessione di test.

Senza questo, lanciare `python -m unittest discover -s tests` scriverebbe davvero su
data/jake.log, data/jake_actions.jsonl, data/jake_sessions.jsonl e data/jake_ledger.jsonl del
contributore - un effetto collaterale introdotto (senza volerlo) aggiungendo il logging
strutturato F0: TaskAgent._log_step/PlanExecutor._log_step chiamano log_action senza chiedersi
se sono dentro un test, a differenza del vecchio self.logger (sempre None nei test, mai passato)
che era gia' un no-op per costruzione. tests/test_risk.py evita di istanziare SkillRegistry()
proprio per questo genere di pesantezza/effetto collaterale; TaskAgent/PlanExecutor nei test
usano un FakeRegistry piu' leggero, ma da quando esistono log_action/ActionLedger scrivono
comunque su disco a meno di reindirizzarli qui - lo stesso vale per action_ledger.py (F1),
aggiunto dopo session_recorder con lo stesso identico problema, stavolta anticipato."""
import tempfile
from pathlib import Path

import core.action_ledger
import core.logger
import core.session_recorder

# Ogni cartella temporanea "jake*" creata con mkdtemp durante la sessione di test viene rimossa
# all'uscita del processo. Windows NON pulisce %TEMP% da solo: il 25/09/2026 c'erano 37.591
# cartelle jake_* lasciate da test che usano mkdtemp senza cancellarle (e da questa stessa cartella
# di log). Il registro vive qui, prima di qualunque tests.test_*, cosi' copre sia unittest (CI) sia
# pytest senza toccare ogni singolo test.
import atexit
import logging
import shutil

_created_temp_dirs: list[str] = []
_original_mkdtemp = tempfile.mkdtemp


def _tracking_mkdtemp(suffix=None, prefix=None, dir=None):
    path = _original_mkdtemp(suffix, prefix, dir)
    if (prefix or "").lower().startswith("jake"):
        _created_temp_dirs.append(path)
    return path


tempfile.mkdtemp = _tracking_mkdtemp


def _make_writable_and_retry(function, path, _exc_info) -> None:
    # Gli oggetti di un repository git creati dai test sono in sola lettura su Windows.
    import os
    import stat

    try:
        os.chmod(path, stat.S_IWRITE)
        function(path)
    except OSError:
        pass


@atexit.register
def _remove_test_temp_dirs() -> None:
    import sys

    logging.shutdown()  # i RotatingFileHandler tengono aperti i log: chiusi prima di cancellare
    for path in reversed(_created_temp_dirs):
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=_make_writable_and_retry)
        else:
            shutil.rmtree(path, onerror=_make_writable_and_retry)


# mkdtemp (non TemporaryDirectory): i RotatingFileHandler di core/logger.py tengono il file
# aperto per tutta la sessione di test; la cartella viene rimossa dall'hook qui sopra all'uscita.
_tmp_path = Path(tempfile.mkdtemp(prefix="jake_test_data_"))

core.logger.DEFAULT_LOG_PATH = _tmp_path / "jake.log"
core.logger.DEFAULT_ACTION_LOG_PATH = _tmp_path / "jake_actions.jsonl"
core.session_recorder.DEFAULT_PATH = _tmp_path / "jake_sessions.jsonl"
core.action_ledger.DEFAULT_LEDGER_PATH = _tmp_path / "jake_ledger.jsonl"
