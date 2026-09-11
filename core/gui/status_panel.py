import os
import tkinter as tk
from pathlib import Path
from tkinter import scrolledtext


class StatusPanel:
    """Finestra di stato/comando di Jake: cronologia, invio comandi testuali, impostazioni.

    Resta nascosta finche' non viene richiesta dal menu della tray icon (vedi tray_app.py)."""

    def __init__(self, jake_core, root: tk.Tk | tk.Toplevel | None = None):
        self.jake_core = jake_core
        self.root = root if root is not None else tk.Tk()
        self.root.title("Jake - Pannello di stato")
        self.root.geometry("560x480")
        self.root.protocol("WM_DELETE_WINDOW", self.hide)

        self._build_widgets()
        self._load_history()
        self._refresh_status()
        self.hide()

    def _load_history(self):
        for turn in self.jake_core.memory_manager.get_recent_history(limit=20):
            label = "Tu" if turn["role"] == "user" else "Jake"
            self._append(f"{label} > {turn['text']}")

    def _build_widgets(self):
        status_frame = tk.Frame(self.root)
        status_frame.pack(fill="x", padx=8, pady=(8, 0))
        self.status_label = tk.Label(status_frame, text="", justify="left", anchor="w")
        self.status_label.pack(fill="x")

        self.output = scrolledtext.ScrolledText(self.root, state="disabled", wrap="word")
        self.output.pack(fill="both", expand=True, padx=8, pady=8)

        entry_frame = tk.Frame(self.root)
        entry_frame.pack(fill="x", padx=8, pady=(0, 8))
        self.entry = tk.Entry(entry_frame)
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", self._on_send)
        tk.Button(entry_frame, text="Invia", command=self._on_send).pack(side="left", padx=(4, 0))

        button_frame = tk.Frame(self.root)
        button_frame.pack(fill="x", padx=8, pady=(0, 8))
        tk.Button(button_frame, text="Impostazioni", command=self._open_settings).pack(side="left")
        tk.Button(button_frame, text="Aggiorna", command=self._refresh_status).pack(side="left", padx=(4, 0))

    def _on_send(self, event=None):
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        self._append(f"Tu > {text}")
        response = self.jake_core.answer(text)
        self._append(f"Jake > {response}")
        self._refresh_status()

    def _append(self, line: str):
        self.output.configure(state="normal")
        self.output.insert("end", line + "\n")
        self.output.configure(state="disabled")
        self.output.see("end")

    def _refresh_status(self):
        history = self.jake_core.conversation_state.get_short_term_history()
        total_memories = self.jake_core.memory_manager.count_memories()
        self.status_label.config(
            text=f"Memorie salvate: {total_memories}   |   Turni in questa sessione: {len(history)}"
        )

    def _open_settings(self):
        settings_path = Path(self.jake_core.skill_registry.config.path)
        if not settings_path.is_file():
            example = settings_path.parent / "settings.example.json"
            settings_path = example if example.is_file() else settings_path
        if settings_path.is_file():
            os.startfile(str(settings_path))

    def show(self):
        self._refresh_status()
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def hide(self):
        self.root.withdraw()

    def run_forever(self, command_queue):
        """Ciclo Tkinter sul thread principale: legge i comandi arrivati dalla tray icon
        (che gira su un thread separato) via coda, cosi' i widget restano thread-safe."""

        def poll_queue():
            while not command_queue.empty():
                command = command_queue.get_nowait()
                if command == "show":
                    self.show()
                elif command == "quit":
                    self.root.quit()
                    return
            self.root.after(150, poll_queue)

        self.root.after(150, poll_queue)
        self.root.mainloop()
