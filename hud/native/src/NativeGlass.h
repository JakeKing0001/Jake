#pragma once

#include <memory>

// F4.3.1: una lastra di vetro nativa - una HWND Win32 senza bitmap di redirezione, riempita con il backdrop sfocato
// che DWM compone dietro la finestra (Windows.UI.Composition, HostBackdropBrush) e ritagliata da una geometria
// arrotondata antialiasata. Niente header Qt qui: C++/WinRT e le macro di Qt non devono incontrarsi.
//
// Perche' non l'accent di DWM (SetWindowCompositionAttribute): provato il 28/09/2026 con un programma di prova sopra
// un motivo a righe - blur e acrilico ignorano la regione della finestra (SetWindowRgn, prima o dopo l'accent): il
// vetro esce squadrato oltre l'angolo curvo del pannello. Gli angoli di DWM arrotondano, ma solo a ~8 px. Il
// backdrop della composizione si ritaglia a qualunque raggio e resta attivo anche su una finestra che non prende mai
// il focus (il system backdrop documentato di DWM diventa invece un colore pieno appena la finestra non e' attiva).
class NativeGlassPane {
public:
    // Windows 11 (il backdrop per finestre Win32 arriva con la build 22000) e composizione disponibile
    static bool available();

    NativeGlassPane();
    ~NativeGlassPane();
    NativeGlassPane(const NativeGlassPane &) = delete;
    NativeGlassPane &operator=(const NativeGlassPane &) = delete;

    bool valid() const;
    // in pixel fisici dello schermo, subito sotto `insertAfter` (la HWND dell'overlay) nello z-order. Una lastra gia'
    // al suo posto non si tocca, salvo `restack`: lo z-order si riafferma di tanto in tanto, non a ogni fotogramma.
    void place(void *insertAfter, int x, int y, int width, int height, float radius, bool restack);
    void hide();

private:
    struct Impl;
    std::unique_ptr<Impl> d;
};
