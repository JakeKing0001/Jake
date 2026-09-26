#pragma once

#include <QJsonArray>
#include <QJsonObject>
#include <QString>
#include <QStringList>

// F4.1 (contratto HUD): come il client interpreta il flusso di eventi, SENZA rete ne' QML.
// Stesse regole di core/hud_view_state.py (il riferimento Python) e stessa suite di fixture
// (tests/fixtures/hud_contract.json, eseguita da hud/native/tests/contract_tests.cpp).
// JakeClient lo usa per ogni riga SSE; le proprieta' QML leggono da qui.
struct HudViewState {
    QString state = QStringLiteral("IDLE");
    bool visible = true;
    bool micOpen = false;
    bool micDiscarding = false;
    QString micReason;
    QString transcriptUtterance;
    int transcriptRevision = 0;
    QString transcriptText;
    bool transcriptFinal = false;
    QJsonArray messages;       // [[role, text], ...]
    QJsonArray notifications;  // [[kind, text], ...]
    QString lastError;
    int stepIndex = 0;
    QString stepDescription;
    QJsonArray evidence;       // [{kind, intent, verified}, ...]
    QString inspectionVerdict;
    QString inspectionReason;
    QString activeDevice;
    bool confirmationPending = false;
    QString confirmationIntent;
    QString confirmationReason;
    QString confirmationRisk;
    bool confirmationExternal = false;
    QString confirmationTraceId;
    QJsonArray activities;     // [{action_id, intent, outcome, verified, trace_id, undo_intent, undo_expires_at}, ...]
    qint64 lastSequenceId = 0;
    int ignored = 0;
    bool incompatible = false;

    // Stessi nomi dei campi di HudViewState.snapshot() in Python: le fixture valgono per entrambi.
    QJsonObject snapshot() const;
};

class HudEventReducer {
public:
    enum class Result { Applied, Ignored, Incompatible };

    static constexpr int kMaxItems = 20;

    void connectionStarted();
    Result applyLine(const QByteArray &line);
    Result apply(const QJsonObject &event);

    const HudViewState &view() const { return m_view; }
    // Tipo e payload dell'ultimo evento applicato (per i segnali di JakeClient).
    const QString &lastType() const { return m_lastType; }
    const QJsonObject &lastPayload() const { return m_lastPayload; }

private:
    Result ignore();
    void reduce(const QString &type, const QJsonObject &payload, const QString &traceId);
    void reduceTranscript(const QJsonObject &payload);
    void reduceActivity(const QString &type, const QJsonObject &payload, const QString &traceId);
    static void push(QJsonArray &items, const QJsonValue &item);

    HudViewState m_view;
    bool m_freshConnection = false;
    QString m_lastType;
    QJsonObject m_lastPayload;
};
