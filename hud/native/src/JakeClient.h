#pragma once

#include <QObject>
#include <QQmlEngine>
#include <QNetworkAccessManager>
#include <QNetworkReply>
#include <QString>
#include <QStringList>

#include "HudEventReducer.h"

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
    // F4.1/F4.5: il resto della vista viene dal riduttore condiviso (HudEventReducer), non da
    // stringhe interpretate qui: microfono (F2.3.5), trascrizione live (F2.2.7), passo in corso,
    // ultima prova (UNDO/VERIFICATION, F1.3.8), diagnosi del selettore (F3.3.6), ultimo errore.
    Q_PROPERTY(bool micOpen READ micOpen NOTIFY viewChanged)
    Q_PROPERTY(bool micDiscarding READ micDiscarding NOTIFY viewChanged)
    Q_PROPERTY(QString transcriptText READ transcriptText NOTIFY viewChanged)
    Q_PROPERTY(bool transcriptFinal READ transcriptFinal NOTIFY viewChanged)
    Q_PROPERTY(QString stepDescription READ stepDescription NOTIFY viewChanged)
    Q_PROPERTY(QString lastError READ lastError NOTIFY viewChanged)
    Q_PROPERTY(QString evidenceSummary READ evidenceSummary NOTIFY viewChanged)
    Q_PROPERTY(QString inspectionReason READ inspectionReason NOTIFY viewChanged)
    // F4.5.3: permission card (azione, rischio, motivo, fonte esterna) - solo metadati.
    Q_PROPERTY(bool confirmationPending READ confirmationPending NOTIFY viewChanged)
    Q_PROPERTY(QString confirmationIntent READ confirmationIntent NOTIFY viewChanged)
    Q_PROPERTY(QString confirmationRisk READ confirmationRisk NOTIFY viewChanged)
    Q_PROPERTY(bool confirmationAuth READ confirmationAuth NOTIFY viewChanged)
    Q_PROPERTY(bool confirmationExternal READ confirmationExternal NOTIFY viewChanged)
    // F4.6.1/F4.6.3: ultima attivita' dell'action center e scadenza del suo undo (epoch s, 0 = no).
    Q_PROPERTY(QString lastActivitySummary READ lastActivitySummary NOTIFY viewChanged)
    Q_PROPERTY(qint64 lastUndoExpiresAt READ lastUndoExpiresAt NOTIFY viewChanged)

public:
    explicit JakeClient(QObject *parent = nullptr);

    bool connected() const { return m_connected; }
    QString state() const { return m_state; }
    QString activeDevice() const { return m_activeDevice; }
    QString deviceId() const { return m_deviceId; }
    bool micOpen() const { return m_reducer.view().micOpen; }
    bool micDiscarding() const { return m_reducer.view().micDiscarding; }
    QString transcriptText() const { return m_reducer.view().transcriptText; }
    bool transcriptFinal() const { return m_reducer.view().transcriptFinal; }
    QString stepDescription() const { return m_reducer.view().stepDescription; }
    QString lastError() const { return m_reducer.view().lastError; }
    QString evidenceSummary() const;
    QString inspectionReason() const { return m_reducer.view().inspectionReason; }
    bool confirmationPending() const { return m_reducer.view().confirmationPending; }
    QString confirmationIntent() const { return m_reducer.view().confirmationIntent; }
    QString confirmationRisk() const { return m_reducer.view().confirmationRisk; }
    bool confirmationAuth() const { return m_reducer.view().confirmationReason == QLatin1String("auth_required"); }
    bool confirmationExternal() const { return m_reducer.view().confirmationExternal; }
    QString lastActivitySummary() const;
    qint64 lastUndoExpiresAt() const;

    // baseUrl es. "http://127.0.0.1:8765" (vedi companion_server_port in config.json).
    Q_INVOKABLE void connectToJake(const QString &baseUrl);
    Q_INVOKABLE void sendCommand(const QString &text);
    Q_INVOKABLE void claimSession();

signals:
    void connectedChanged();
    void stateChanged();
    void viewChanged();
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
    // F4.1: regole di interpretazione degli eventi, condivise con core/hud_view_state.py e
    // verificate dalla stessa suite di fixture. Tiene anche l'ultimo sequence_id visto, mandato
    // come Last-Event-ID alla riconnessione (F4.1.3) e usato per scartare duplicati (F4.4.6).
    HudEventReducer m_reducer;
    // F4.1.6 ("definire compatibility window tra core e HUD"): senza questo, ogni singolo evento
    // con schema_version incompatibile emetteva un errorOccurred proprio - su uno stream SSE che
    // puo' ricevere piu' eventi al secondo, un core disallineato (es. HUD non ricompilato dopo un
    // cambio di schema) inonderebbe l'interfaccia con lo stesso errore ripetuto invece di
    // segnalarlo UNA volta e interrompere lo stream. Azzerato ad ogni nuova connectToJake().
    bool m_protocolMismatchReported = false;
};
