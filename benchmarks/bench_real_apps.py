"""Gate F3 — almeno 5 app reali, ciascuna con un flusso definito eseguito 3 volte di fila.

    python -m benchmarks.bench_real_apps              # 5 app x 3 esecuzioni
    python -m benchmarks.bench_real_apps --apps calculator paint --runs 3

Regole (docs della roadmap, Gate F3): ambienti isolati e dati temporanei; mai le finestre personali
gia' aperte dell'utente (ogni finestra e' riconosciuta come NUOVA o con un titolo univoco generato qui);
l'effetto finale e' verificato in modo INDIPENDENTE dall'azione (file su disco, pixel del file, display
dell'app, contenuto della pagina); pulizia completa dopo ogni esecuzione, anche se fallisce. La
tastiera viene usata solo quando la finestra in primo piano e' esattamente quella del flusso.

| app | flusso | verifica indipendente |
|---|---|---|
| calculator | 17 + 25 = con Invoke UIA sui tasti | il display dell'app mostra 42 |
| paint | PNG temporaneo con un quadrato rosso: Ctrl+A, Canc, Ctrl+S | nel file salvato non resta un pixel rosso |
| explorer | cartella temporanea: Ctrl+Maiusc+N, nome, Invio | la cartella esiste su disco |
| terminal | finestra cmd isolata: comando digitato che scrive un file | il file temporaneo contiene il testo |
| edge | profilo temporaneo, pagina locale: scrive e preme Aggiungi | la pagina contiene la voce aggiunta |
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks._report import save_report  # noqa: E402


class ForeignFocusError(RuntimeError):
    """La finestra in primo piano non e' quella del flusso: nessun tasto inviato."""


@dataclass
class RunResult:
    app: str
    run: int
    success: bool
    seconds: float
    evidence: str = ""
    diagnosis: list[str] = field(default_factory=list)
    cleanup_ok: bool = True


def _hwnd(element) -> int:
    return int(element.CurrentNativeWindowHandle)


def keys_to(hwnd: int, send) -> None:
    """Invia tasti SOLO se `hwnd` e' in primo piano (dopo averlo chiesto a Windows)."""
    import win32gui

    from core.computer_use.ui_automation_adapter import force_foreground_window

    force_foreground_window(hwnd)
    time.sleep(0.2)
    if win32gui.GetForegroundWindow() != hwnd:
        raise ForeignFocusError("la finestra del flusso non e' in primo piano: nessun tasto inviato")
    send()


def file_version(path) -> str | None:
    try:
        import win32api

        info = win32api.GetFileVersionInfo(str(path), "\\")
        ms, ls = info["FileVersionMS"], info["FileVersionLS"]
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:
        return None


def package_version_from_path(path: str) -> str | None:
    match = re.search(r"_(\d+\.\d+\.\d+\.\d+)_", path or "")
    return match.group(1) if match else None


# ---- flussi ------------------------------------------------------------------------------------


class Calculator:
    name = "calculator"
    workflow = "17 + 25 = tramite Invoke UIA sui tasti; verifica sul display dell'app"

    def __init__(self):
        self.version = None

    def run_once(self, adapter, index: int) -> RunResult:
        import psutil

        from core.computer_use.executor import ActionExecutor
        from core.computer_use.selector import ElementSelector, SelectorEngine

        result = RunResult(self.name, index, False, 0.0)
        before = adapter.snapshot_top_level_window_handles()
        subprocess.Popen(["calc.exe"])
        window = adapter.wait_for_new_top_level_window(before, timeout_seconds=20.0)  # solo la NOSTRA finestra
        try:
            if self.version is None:
                for proc in psutil.process_iter(["name", "exe"]):
                    if proc.info["name"] == "CalculatorApp.exe":
                        self.version = package_version_from_path(proc.info["exe"] or "")
            engine, executor = SelectorEngine(adapter), ActionExecutor()
            for button in ("clearButton", "num1Button", "num7Button", "plusButton", "num2Button", "num5Button", "equalButton"):
                executor.invoke(engine.wait_for_unique_element(window, ElementSelector(automation_id=button), timeout_seconds=10.0))
            display = engine.wait_for_unique_element(window, ElementSelector(automation_id="CalculatorResults"), timeout_seconds=5.0)
            text = display.CurrentName
            result.evidence = f"display: {text!r}"
            result.success = bool(re.search(r"\b42\b", text))
            if not result.success:
                result.diagnosis.append("il display non mostra 42")
        finally:
            ActionExecutor().close_window(window)
            time.sleep(1.0)
            result.cleanup_ok = _hwnd_gone(adapter, window)
        return result


def _hwnd_gone(adapter, window) -> bool:
    import win32gui

    try:
        hwnd = _hwnd(window)
    except Exception:
        return True
    return not win32gui.IsWindow(hwnd)


class Paint:
    name = "paint"
    workflow = "PNG temporaneo con un quadrato rosso: Ctrl+A, Canc, Ctrl+S; verifica sui pixel del file"

    def __init__(self):
        self.version = None

    def run_once(self, adapter, index: int) -> RunResult:
        import psutil
        import pyautogui
        from PIL import Image

        from core.computer_use.executor import ActionExecutor

        result = RunResult(self.name, index, False, 0.0)
        folder = Path(tempfile.mkdtemp(prefix="jake_paint_gate_"))
        marker = f"jake_paint_{uuid.uuid4().hex[:8]}"
        image_path = folder / f"{marker}.png"
        image = Image.new("RGB", (200, 150), "white")
        image.paste((255, 0, 0), (50, 40, 150, 110))
        image.save(image_path)
        process_pids = set()
        window = None
        try:
            subprocess.Popen(["mspaint.exe", str(image_path)])
            window = adapter.find_window_by_title_containing(marker, timeout_seconds=25.0)  # titolo univoco
            pid = adapter.process_id_of(window)
            process_pids.add(pid)
            if self.version is None:
                self.version = package_version_from_path(psutil.Process(pid).exe())
            hwnd = _hwnd(window)
            time.sleep(1.0)
            keys_to(hwnd, lambda: pyautogui.hotkey("ctrl", "a"))
            time.sleep(0.4)
            keys_to(hwnd, lambda: pyautogui.press("delete"))
            time.sleep(0.4)
            keys_to(hwnd, lambda: pyautogui.hotkey("ctrl", "s"))
            deadline = time.monotonic() + 10
            red = None
            while time.monotonic() < deadline:
                time.sleep(0.5)
                try:
                    import numpy as np

                    with Image.open(image_path) as saved:
                        rgb = np.asarray(saved.convert("RGB")).astype(int)
                    red = int(((rgb[..., 0] > 200) & (rgb[..., 1] < 60) & (rgb[..., 2] < 60)).sum())
                except OSError:
                    continue
                if red == 0:
                    break
            result.evidence = f"pixel rossi nel file salvato: {red}"
            result.success = red == 0
            if not result.success:
                result.diagnosis.append("il file salvato contiene ancora il quadrato rosso")
        except ForeignFocusError as exc:
            result.diagnosis.append(str(exc))
        finally:
            if window is not None:
                try:
                    ActionExecutor().close_window(window)
                except Exception as exc:
                    result.diagnosis.append(f"chiusura: {exc}")
            time.sleep(1.5)
            for pid in process_pids:
                if psutil.pid_exists(pid):
                    result.cleanup_ok = False
                    result.diagnosis.append("Paint ancora aperto dopo la chiusura")
            shutil.rmtree(folder, ignore_errors=True)
            result.cleanup_ok = result.cleanup_ok and not folder.exists()
        return result


class Explorer:
    name = "explorer"
    workflow = "cartella temporanea: Ctrl+Maiusc+N, nome univoco, Invio; verifica su disco"

    def __init__(self):
        self.version = file_version(Path(r"C:\Windows\explorer.exe"))

    def run_once(self, adapter, index: int) -> RunResult:
        import pyautogui

        from core.computer_use.file_explorer_adapter import close_explorer_window, find_explorer_window, open_explorer_window

        result = RunResult(self.name, index, False, 0.0)
        folder = Path(tempfile.mkdtemp(prefix=f"jake_explorer_gate_{uuid.uuid4().hex[:6]}_"))
        created = f"creata_da_jake_{index}"
        try:
            open_explorer_window(folder)
            window = find_explorer_window(adapter, folder.name, timeout_seconds=15.0)
            hwnd = _hwnd(window)
            time.sleep(1.0)
            keys_to(hwnd, lambda: pyautogui.hotkey("ctrl", "shift", "n"))
            time.sleep(1.0)
            keys_to(hwnd, lambda: (pyautogui.hotkey("ctrl", "a"), pyautogui.write(created, interval=0.02), pyautogui.press("enter")))
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline and not (folder / created).is_dir():
                time.sleep(0.3)
            result.success = (folder / created).is_dir()
            result.evidence = f"cartella su disco: {(folder / created).is_dir()}"
            if not result.success:
                result.diagnosis.append(f"la cartella {created!r} non esiste su disco: {sorted(p.name for p in folder.iterdir())}")
        except ForeignFocusError as exc:
            result.diagnosis.append(str(exc))
        finally:
            try:
                close_explorer_window(adapter, folder.name, timeout_seconds=10.0)
            except Exception as exc:
                result.diagnosis.append(f"chiusura: {exc}")
            time.sleep(0.5)
            shutil.rmtree(folder, ignore_errors=True)
            result.cleanup_ok = not folder.exists()
        return result


class Terminal:
    name = "terminal"
    workflow = "finestra cmd isolata con titolo univoco: comando digitato che scrive un file temporaneo"

    def __init__(self):
        self.version = file_version(Path(r"C:\Windows\System32\cmd.exe"))

    def run_once(self, adapter, index: int) -> RunResult:
        import pyautogui

        from core.computer_use.terminal_adapter import close_terminal_window, find_terminal_window, launch_isolated_terminal

        result = RunResult(self.name, index, False, 0.0)
        folder = Path(tempfile.mkdtemp(prefix="jake_terminal_gate_"))
        title = f"jake_terminal_{uuid.uuid4().hex[:8]}"
        out = folder / "out.txt"
        expected = f"jake_ok_{index}"
        try:
            launch_isolated_terminal(title)
            window = find_terminal_window(adapter, title, timeout_seconds=15.0)
            hwnd = _hwnd(window)
            time.sleep(0.8)
            keys_to(hwnd, lambda: (pyautogui.write(f'echo {expected}> "{out}"', interval=0.01), pyautogui.press("enter")))
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline and not out.exists():
                time.sleep(0.3)
            content = out.read_text(encoding="utf-8", errors="replace").strip() if out.exists() else None
            result.success = content == expected
            result.evidence = f"contenuto del file: {content!r}"
            if not result.success:
                result.diagnosis.append("il file non contiene il testo atteso")
        except ForeignFocusError as exc:
            result.diagnosis.append(str(exc))
        finally:
            try:
                close_terminal_window(adapter, title, timeout_seconds=10.0)
            except Exception as exc:
                result.diagnosis.append(f"chiusura: {exc}")
            time.sleep(0.5)
            shutil.rmtree(folder, ignore_errors=True)
            result.cleanup_ok = not folder.exists()
        return result


class Edge:
    name = "edge"
    workflow = "Edge con profilo temporaneo e pagina locale: scrive nel campo e preme Aggiungi (UIA)"

    def __init__(self):
        from core.computer_use.browser_adapter import find_edge_executable

        self.version = file_version(find_edge_executable())

    def run_once(self, adapter, index: int) -> RunResult:
        from core.computer_agent import ComputerAgent
        from core.computer_use.browser_adapter import find_page_document, launch_isolated_browser, read_page_text

        import psutil

        result = RunResult(self.name, index, False, 0.0)
        url = (ROOT / "benchmarks" / "browser_fixture.html").as_uri()
        browser = launch_isolated_browser(url)
        text = f"voce edge {index} {uuid.uuid4().hex[:4]}"
        try:
            window = adapter.find_window_by_process_id(browser.process.pid, timeout_seconds=25.0)  # solo la NOSTRA istanza
            document = find_page_document(adapter, window, timeout_seconds=15.0)
            agent = ComputerAgent()
            typed = agent.type_into_element(text, root=document, name="Campo di testo", control_type="Edit")
            clicked = agent.click_element(root=document, name="Aggiungi", control_type="Button")
            deadline = time.monotonic() + 5
            page = ""
            while time.monotonic() < deadline:
                page = read_page_text(adapter, find_page_document(adapter, window, timeout_seconds=5.0))
                if text in page:
                    break
                time.sleep(0.3)
            result.success = typed.success and clicked.success and text in page
            result.evidence = f"voce nella pagina: {text in page}; strategie: {typed.strategy}, {clicked.strategy}"
            if not result.success:
                result.diagnosis.append(f"type={typed.error or typed.attempts} click={clicked.error or clicked.attempts}")
        finally:
            try:
                browser.terminate_and_cleanup()
            except Exception as exc:
                result.diagnosis.append(f"chiusura: {exc}")
            time.sleep(0.5)
            result.cleanup_ok = not psutil.pid_exists(browser.process.pid) and not Path(browser.user_data_dir).exists()
        return result


APPS = {cls.name: cls for cls in (Calculator, Paint, Explorer, Terminal, Edge)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Gate F3: app reali")
    parser.add_argument("--apps", nargs="*", choices=sorted(APPS), default=list(APPS))
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args(argv)

    from core.win_dpi import ensure_dpi_aware

    ensure_dpi_aware()
    from core.computer_use.ui_automation_adapter import UIAutomationAdapter

    adapter = UIAutomationAdapter()
    report = {"runs_per_app": args.runs, "apps": {}}
    for name in args.apps:
        flow = APPS[name]()
        runs = []
        for index in range(1, args.runs + 1):
            started = time.perf_counter()
            try:
                outcome = flow.run_once(adapter, index)
            except Exception as exc:  # un'eccezione e' una diagnosi, non un crash del gate
                outcome = RunResult(name, index, False, 0.0, diagnosis=[f"{type(exc).__name__}: {exc}"])
            outcome.seconds = round(time.perf_counter() - started, 2)
            runs.append(outcome.__dict__)
            print(f"[{'OK' if outcome.success and outcome.cleanup_ok else 'KO'}] {name} #{index} "
                  f"{outcome.evidence} pulizia={outcome.cleanup_ok} {'; '.join(outcome.diagnosis)}")
        consecutive = all(r["success"] and r["cleanup_ok"] for r in runs)
        report["apps"][name] = {"workflow": flow.workflow, "version": flow.version, "runs": runs,
                                "passed_consecutively": consecutive}
    passed = [name for name, app in report["apps"].items() if app["passed_consecutively"]]
    report["passed_apps"] = passed
    report["gate"] = "PASS" if len(passed) >= 5 else "FAIL"
    path = save_report("real_apps", report)
    print(f"\nApp verdi ({args.runs}/{args.runs} di fila): {len(passed)} {passed} -> {report['gate']}")
    print(f"Report: {path}")
    return 0 if report["gate"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
