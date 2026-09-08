"""Eseguito una sola volta, prima di importare qualunque modulo tests.test_* (Python importa
sempre il pacchetto genitore per primo): reindirizza i log di produzione (core/logger.py,
core/session_recorder.py) verso una cartella temporanea per l'intera sessione di test.

Senza questo, lanciare `python -m unittest discover -s tests` scriverebbe davvero su
data/jake.log, data/jake_actions.jsonl e data/jake_sessions.jsonl del contributore - un effetto
collaterale introdotto (senza volerlo) aggiungendo il logging strutturato F0: TaskAgent.
_log_step/PlanExecutor._log_step chiamano log_action senza chiedersi se sono dentro un test, a
differenza del vecchio self.logger (sempre None nei test, mai passato) che era gia' un no-op per
costruzione. tests/test_risk.py evita di istanziare SkillRegistry() proprio per questo genere di
pesantezza/effetto collaterale; TaskAgent/PlanExecutor nei test usano un FakeRegistry piu'
leggero, ma da quando esiste log_action scrivono comunque su disco a meno di reindirizzarli qui."""
import tempfile
from pathlib import Path

import core.logger
import core.session_recorder

# mkdtemp (non TemporaryDirectory): i RotatingFileHandler di core/logger.py tengono il file
# aperto per tutta la sessione di test, e su Windows un cleanup automatico a fine processo
# (il finalizer di TemporaryDirectory) fallisce con "file usato da un altro processo" perche'
# prova a cancellare mentre l'handle e' ancora aperto. La cartella resta in TEMP - normale per
# una run di test, la ripulisce il sistema operativo.
_tmp_path = Path(tempfile.mkdtemp(prefix="jake_test_data_"))

core.logger.DEFAULT_LOG_PATH = _tmp_path / "jake.log"
core.logger.DEFAULT_ACTION_LOG_PATH = _tmp_path / "jake_actions.jsonl"
core.session_recorder.DEFAULT_PATH = _tmp_path / "jake_sessions.jsonl"
