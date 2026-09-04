import queue
import threading


def _build_icon_image():
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (64, 64), "#1e1e2e")
    draw = ImageDraw.Draw(image)
    draw.ellipse((4, 4, 60, 60), fill="#89b4fa")
    draw.text((24, 20), "J", fill="#1e1e2e")
    return image


def run_tray(jake_core):
    """Avvia Jake nella system tray: icona con menu (pannello di stato, uscita) e notifiche.

    La tray icon gira su un thread separato (pystray su Windows non richiede il thread
    principale); Tkinter invece lo richiede, quindi il pannello di stato occupa il thread
    principale e riceve i comandi dalla tray via una coda thread-safe."""
    import pystray

    from core.gui.status_panel import StatusPanel

    command_queue = queue.Queue()
    panel = StatusPanel(jake_core)

    def on_open(icon, item):
        command_queue.put("show")

    def on_quit(icon, item):
        command_queue.put("quit")
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Apri pannello", on_open, default=True),
        pystray.MenuItem("Esci", on_quit),
    )
    icon = pystray.Icon("jake", _build_icon_image(), "Jake", menu)

    def _on_setup(i):
        # pystray mostra l'icona in automatico solo col setup di default: passando un setup
        # personalizzato (qui serve per la notifica), va impostata esplicitamente visible=True.
        i.visible = True
        i.notify("Jake e' attivo nella system tray.", "Jake")

    def run_icon():
        icon.run(setup=_on_setup)

    tray_thread = threading.Thread(target=run_icon, daemon=True)
    tray_thread.start()

    panel.run_forever(command_queue)
