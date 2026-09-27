#include "GlobalHotkeys.h"

#include <QCoreApplication>

#ifdef Q_OS_WIN
#include <windows.h>
#endif

namespace {
constexpr int kCommandBarHotkey = 0x4A01;
constexpr int kKillSwitchHotkey = 0x4A02;
} // namespace

GlobalHotkeys::GlobalHotkeys(QObject *parent) : QObject(parent) {
#ifdef Q_OS_WIN
    // hWnd = nullptr: WM_HOTKEY arriva alla coda del thread GUI, che Qt passa ai filtri nativi.
    m_commandBarRegistered = RegisterHotKey(nullptr, kCommandBarHotkey, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, 'J');
    m_killSwitchRegistered = RegisterHotKey(nullptr, kKillSwitchHotkey, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_END);
#endif
    QCoreApplication::instance()->installNativeEventFilter(this);
}

GlobalHotkeys::~GlobalHotkeys() {
    QCoreApplication::instance()->removeNativeEventFilter(this);
#ifdef Q_OS_WIN
    if (m_commandBarRegistered) UnregisterHotKey(nullptr, kCommandBarHotkey);
    if (m_killSwitchRegistered) UnregisterHotKey(nullptr, kKillSwitchHotkey);
#endif
}

bool GlobalHotkeys::nativeEventFilter(const QByteArray &eventType, void *message, qintptr *result) {
    Q_UNUSED(result);
#ifdef Q_OS_WIN
    if (eventType != "windows_generic_MSG") return false;
    const MSG *msg = static_cast<const MSG *>(message);
    if (msg->message != WM_HOTKEY) return false;
    if (msg->wParam == kCommandBarHotkey) {
        emit commandBarRequested();
        return true;
    }
    if (msg->wParam == kKillSwitchHotkey) {
        emit killSwitchRequested();
        return true;
    }
#else
    Q_UNUSED(eventType);
    Q_UNUSED(message);
#endif
    return false;
}
