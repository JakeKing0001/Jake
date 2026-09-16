#pragma once

#include <QObject>
#include <QQmlEngine>
#include <QNetworkAccessManager>
#include <QNetworkReply>
#include <QString>
#include <QStringList>

// Client per il server companion di Jake (core/companion_server.py, v4.9.1/5.8/5.9):
// GET /status, GET /events (Server-Sent Events), POST /command, POST /devices/<id>/claim.
// Nessuna logica di Jake qui dentro (niente Ollama, niente agenti): solo il protocollo
// eventi (core/hud_protocol.py), esattamente come previsto dalla fase "UI separation".
class JakeClient : public QObject {
    Q_OBJECT
    QML_ELEMENT
    Q_PROPERTY(bool connected READ connected NOTIFY connectedChanged)
    Q_PROPERTY(QString state READ state NOTIFY stateChanged)
    Q_PROPERTY(QString activeDevice READ activeDevice NOTIFY activeDeviceChanged)
    Q_PROPERTY(QString deviceId READ deviceId CONSTANT)

public:
    explicit JakeClient(QObject *parent = nullptr);

    bool connected() const { return m_connected; }
    QString state() const { return m_state; }
    QString activeDevice() const { return m_activeDevice; }
    QString deviceId() const { return m_deviceId; }

    // baseUrl es. "http://127.0.0.1:8765" (vedi companion_server_port in config.json).
    Q_INVOKABLE void connectToJake(const QString &baseUrl);
    Q_INVOKABLE void sendCommand(const QString &text);
    Q_INVOKABLE void claimSession();

signals:
    void connectedChanged();
    void stateChanged();
    void activeDeviceChanged();
    // role: "user" o "jake" (da USER_MESSAGE/JAKE_MESSAGE, vedi core/hud_protocol.py).
    void messageReceived(const QString &role, const QString &text);
    void agentStep(int step, const QString &description);
    void notification(const QString &kind, const QString &text);
    void errorOccurred(const QString &detail);
    void deviceHandoff(const QString &fromDevice, const QString &toDevice);
    // F4.2.2 ("gestire show/hide senza rubare focus"): HUD_SHOW/HUD_HIDE (core/hud_protocol.py)
    // controllano DAVVERO la visibilita' della finestra, non solo lo stato mostrato nell'Orb -
    // prima di questo segnale cadevano nel ramo generico di handleEventLine() che si limita a
    // setState(type), lasciando la finestra sempre visibile con lo stato scritto alla lettera
    // "HUD_SHOW"/"HUD_HIDE" (non riconosciuto da Orb.qml, quindi mostrato col colore di default).
    void visibilityRequested(bool visible);

private slots:
    void onEventStreamReadyRead();
    void onEventStreamFinished();
    void onCommandFinished();

private:
    void setConnected(bool value);
    void setState(const QString &value);
    void setActiveDevice(const QString &value);
    void handleEventLine(const QString &jsonLine);
    void fetchStatus();

    QNetworkAccessManager *m_manager;
    QNetworkReply *m_eventStream = nullptr;
    QString m_baseUrl;
    QString m_eventBuffer;
    QString m_deviceId;
    bool m_connected = false;
    QString m_state = QStringLiteral("IDLE");
    QString m_activeDevice;
};
