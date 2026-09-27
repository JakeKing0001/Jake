#pragma once

#include <QAbstractNativeEventFilter>
#include <QObject>
#include <QQmlEngine>

// F4.6.5/F4.7.1: scorciatoie GLOBALI (funzionano anche quando l'HUD non ha il focus, che per scelta non
// prende mai da solo): Ctrl+Shift+J porta la tastiera sulla barra comandi, Ctrl+Alt+Fine ferma tutto
// (kill switch). RegisterHotKey di Win32: se un'altra app ha gia' preso la combinazione, `registered`
// resta false per quella voce e l'HUD lo mostra invece di fingere che funzioni.
class GlobalHotkeys : public QObject, public QAbstractNativeEventFilter {
    Q_OBJECT
    QML_ELEMENT
    Q_PROPERTY(bool commandBarRegistered READ commandBarRegistered NOTIFY registrationChanged)
    Q_PROPERTY(bool killSwitchRegistered READ killSwitchRegistered NOTIFY registrationChanged)

public:
    explicit GlobalHotkeys(QObject *parent = nullptr);
    ~GlobalHotkeys() override;

    bool commandBarRegistered() const { return m_commandBarRegistered; }
    bool killSwitchRegistered() const { return m_killSwitchRegistered; }

    bool nativeEventFilter(const QByteArray &eventType, void *message, qintptr *result) override;

signals:
    void commandBarRequested();
    void killSwitchRequested();
    void registrationChanged();

private:
    bool m_commandBarRegistered = false;
    bool m_killSwitchRegistered = false;
};
