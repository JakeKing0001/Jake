#include "OverlayStyler.h"

#ifdef Q_OS_WIN
#include <windows.h>
#endif

void OverlayStyler::makeNoActivate(QQuickWindow *window) {
#ifdef Q_OS_WIN
    if (!window)
        return;
    HWND hwnd = reinterpret_cast<HWND>(window->winId());
    LONG_PTR exStyle = GetWindowLongPtr(hwnd, GWL_EXSTYLE);
    SetWindowLongPtr(hwnd, GWL_EXSTYLE, exStyle | WS_EX_NOACTIVATE);
#else
    Q_UNUSED(window);
#endif
}

void OverlayStyler::setClickThrough(QQuickWindow *window, bool enabled) {
#ifdef Q_OS_WIN
    if (!window)
        return;
    HWND hwnd = reinterpret_cast<HWND>(window->winId());
    LONG_PTR exStyle = GetWindowLongPtr(hwnd, GWL_EXSTYLE);
    // Solo WS_EX_TRANSPARENT (mai WS_EX_LAYERED): la trasparenza VISIVA di questa finestra e'
    // gia' ottenuta da Qt/DWM tramite `color: "transparent"` su una finestra senza bordi, non dal
    // meccanismo classico UpdateLayeredWindow - aggiungere WS_EX_LAYERED a mano rischierebbe di
    // interferire con la composizione che Qt gestisce gia' da solo. WS_EX_TRANSPARENT da solo
    // basta per il click-through (influenza solo l'hit-test dei messaggi del mouse, non il
    // rendering).
    if (enabled)
        exStyle |= WS_EX_TRANSPARENT;
    else
        exStyle &= ~WS_EX_TRANSPARENT;
    SetWindowLongPtr(hwnd, GWL_EXSTYLE, exStyle);
#else
    Q_UNUSED(window);
    Q_UNUSED(enabled);
#endif
}

void OverlayStyler::forceVisibility(QQuickWindow *window, bool visible) {
#ifdef Q_OS_WIN
    if (!window)
        return;
    HWND hwnd = reinterpret_cast<HWND>(window->winId());
    ShowWindow(hwnd, visible ? SW_SHOWNOACTIVATE : SW_HIDE);
#else
    Q_UNUSED(window);
    Q_UNUSED(visible);
#endif
}
