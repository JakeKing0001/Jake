"""Test unitari per KillProcessByPortSkill (skills/dev_tools.py): nessuna suite esisteva ancora
per questo modulo. Copre in particolare il buco reale trovato e corretto in questa sessione
(F1.3.2, "prove forti per... processi"): prima, success=True veniva restituito subito dopo aver
solo CHIESTO la terminazione (psutil.Process.terminate()), senza aspettare che il processo fosse
davvero morto - terminate() invia la richiesta, non garantisce che sia gia' avvenuta quando la
chiamata ritorna."""
import subprocess
import sys
import unittest
import unittest.mock

import psutil

from skills.dev_tools import KillProcessByPortSkill

PORT = 54321


class _FakeAddr:
    def __init__(self, port):
        self.port = port


class _FakeConnection:
    def __init__(self, port, pid, status="LISTEN"):
        self.laddr = _FakeAddr(port)
        self.status = status
        self.pid = pid


def _patch_net_connections(pid, port=PORT):
    return unittest.mock.patch(
        "psutil.net_connections", return_value=[_FakeConnection(port, pid)],
    )


class MissingParameterTests(unittest.TestCase):
    def test_missing_port_fails(self):
        result = KillProcessByPortSkill().execute({})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "MISSING_PARAMETERS")

    def test_non_integer_port_fails(self):
        result = KillProcessByPortSkill().execute({"port": "not-a-number"})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "MISSING_PARAMETERS")


class NotFoundTests(unittest.TestCase):
    def test_no_listener_on_the_port_reports_not_found(self):
        with unittest.mock.patch("psutil.net_connections", return_value=[]):
            result = KillProcessByPortSkill().execute({"port": PORT})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "NOT_FOUND")


class ConfirmationTests(unittest.TestCase):
    def test_first_call_without_confirmed_asks_for_confirmation(self):
        with _patch_net_connections(pid=999999):
            with unittest.mock.patch("psutil.Process") as mock_process_cls:
                mock_process_cls.return_value.name.return_value = "processo_finto"
                result = KillProcessByPortSkill().execute({"port": PORT})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "CONFIRMATION_REQUIRED")
        self.assertEqual(result.data["confirm_parameters"], {"port": PORT, "confirmed": True})
        mock_process_cls.return_value.terminate.assert_not_called()


class RealProcessTerminationTests(unittest.TestCase):
    """Un processo VERO (non un doppio), per verificare il comportamento reale di
    terminate()+wait() - non solo che il codice chiami le funzioni giuste."""

    def _spawn_sleeper(self) -> subprocess.Popen:
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.addCleanup(lambda: process.poll() is None and process.kill())
        return process

    def test_successful_kill_waits_for_real_death_before_reporting_success(self):
        """Buco reale corretto: prima di questa correzione il test seguente sarebbe comunque
        passato per caso (il processo VERO alla fine muore), ma solo perche' terminate() su
        Windows e' gia' forzato (TerminateProcess) - questo test verifica il comportamento
        osservabile che conta per l'utente: quando la skill dice 'fatto', il processo e' GIA'
        morto, non 'forse morira' a breve'."""
        process = self._spawn_sleeper()
        self.assertTrue(psutil.pid_exists(process.pid))

        with _patch_net_connections(pid=process.pid):
            result = KillProcessByPortSkill().execute({"port": PORT, "confirmed": True})

        self.assertTrue(result.success)
        self.assertEqual(result.data["pid"], process.pid)
        self.assertFalse(psutil.pid_exists(process.pid), "il processo deve essere gia' morto quando execute() ritorna")

    def test_process_already_gone_between_terminate_and_wait_is_still_a_success(self):
        """Se il processo muore da solo (o per un'altra causa) proprio nella finestra tra
        terminate() e wait(), psutil.NoSuchProcess non deve essere trattato come un fallimento:
        l'effetto voluto (il processo non c'e' piu') e' comunque avvenuto."""
        process = self._spawn_sleeper()

        with _patch_net_connections(pid=process.pid):
            with unittest.mock.patch("psutil.Process") as mock_process_cls:
                mock_process_cls.return_value.name.return_value = "processo_finto"
                mock_process_cls.return_value.terminate.return_value = None
                mock_process_cls.return_value.wait.side_effect = psutil.NoSuchProcess(process.pid)
                result = KillProcessByPortSkill().execute({"port": PORT, "confirmed": True})

        self.assertTrue(result.success)
        self.assertEqual(result.data["pid"], process.pid)


class ProcessRefusesToDieTests(unittest.TestCase):
    """psutil.Process mockato per simulare un processo che non muore in tempo: non serve un
    processo reale per verificare che la skill riporti onestamente il fallimento invece di
    dichiarare un successo mai confermato."""

    def test_process_not_dead_within_the_timeout_is_reported_as_a_failure_not_a_success(self):
        with _patch_net_connections(pid=4242):
            with unittest.mock.patch("psutil.Process") as mock_process_cls:
                mock_process_cls.return_value.name.return_value = "processo_zombie"
                mock_process_cls.return_value.terminate.return_value = None
                mock_process_cls.return_value.wait.side_effect = psutil.TimeoutExpired(seconds=3, pid=4242)
                result = KillProcessByPortSkill().execute({"port": PORT, "confirmed": True})

        self.assertFalse(result.success, "non deve dichiarare successo se il processo non e' confermato morto")
        self.assertEqual(result.error, "OPERATION_FAILED")
        self.assertEqual(result.data["pid"], 4242)

    def test_terminate_itself_raising_is_reported_as_a_failure(self):
        with _patch_net_connections(pid=4242):
            with unittest.mock.patch("psutil.Process") as mock_process_cls:
                mock_process_cls.return_value.name.return_value = "processo_protetto"
                mock_process_cls.return_value.terminate.side_effect = psutil.AccessDenied(4242)
                result = KillProcessByPortSkill().execute({"port": PORT, "confirmed": True})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "OPERATION_FAILED")


if __name__ == "__main__":
    unittest.main()
