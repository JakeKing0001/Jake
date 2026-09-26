#include "HudEventReducer.h"

#include <QJsonDocument>
#include <QSet>

#include "HudEventTypes.h" // generato da tools/generate_hud_event_types.py, vedi CMakeLists.txt

namespace {
const QSet<QString> &knownTypes() {
    static const QSet<QString> types = [] {
        QSet<QString> all;
        for (const char *name : JakeHudEventType::ALL)
            all.insert(QString::fromLatin1(name));
        return all;
    }();
    return types;
}

const QSet<QString> &stateEvents() {
    static const QSet<QString> states = {
        QString::fromLatin1(JakeHudEventType::IDLE), QString::fromLatin1(JakeHudEventType::LISTENING),
        QString::fromLatin1(JakeHudEventType::THINKING), QString::fromLatin1(JakeHudEventType::EXECUTING),
        QString::fromLatin1(JakeHudEventType::DICTATION), QString::fromLatin1(JakeHudEventType::PAUSED),
    };
    return states;
}

// Come _text/_int in core/hud_view_state.py: un campo del tipo sbagliato vale "vuoto", mai un crash.
QString text(const QJsonObject &payload, const char *key) {
    const QJsonValue value = payload.value(QLatin1String(key));
    return value.isString() ? value.toString() : QString();
}

int integer(const QJsonObject &payload, const char *key) {
    const QJsonValue value = payload.value(QLatin1String(key));
    if (!value.isDouble()) return 0;
    const double number = value.toDouble();
    return number == static_cast<double>(static_cast<qint64>(number)) ? static_cast<int>(number) : 0;
}

bool isType(const QString &type, const char *name) { return type == QLatin1String(name); }
} // namespace

QJsonObject HudViewState::snapshot() const {
    return QJsonObject{
        {"state", state},
        {"visible", visible},
        {"mic_open", micOpen},
        {"mic_discarding", micDiscarding},
        {"mic_reason", micReason},
        {"transcript_utterance", transcriptUtterance},
        {"transcript_revision", transcriptRevision},
        {"transcript_text", transcriptText},
        {"transcript_final", transcriptFinal},
        {"messages", messages},
        {"notifications", notifications},
        {"last_error", lastError},
        {"step_index", stepIndex},
        {"step_description", stepDescription},
        {"evidence", evidence},
        {"inspection_verdict", inspectionVerdict},
        {"inspection_reason", inspectionReason},
        {"active_device", activeDevice},
        {"confirmation_pending", confirmationPending},
        {"confirmation_intent", confirmationIntent},
        {"confirmation_reason", confirmationReason},
        {"confirmation_risk", confirmationRisk},
        {"confirmation_external", confirmationExternal},
        {"confirmation_trace_id", confirmationTraceId},
        {"activities", activities},
        {"last_outcome", lastOutcome},
        {"last_sequence_id", lastSequenceId},
        {"ignored", ignored},
        {"incompatible", incompatible},
    };
}

void HudEventReducer::connectionStarted() {
    m_freshConnection = true;
    m_view.incompatible = false;
}

HudEventReducer::Result HudEventReducer::ignore() {
    m_view.ignored += 1;
    return Result::Ignored;
}

HudEventReducer::Result HudEventReducer::applyLine(const QByteArray &line) {
    QJsonParseError error{};
    const QJsonDocument doc = QJsonDocument::fromJson(line, &error);
    if (error.error != QJsonParseError::NoError || !doc.isObject())
        return ignore();
    return apply(doc.object());
}

HudEventReducer::Result HudEventReducer::apply(const QJsonObject &event) {
    const QJsonValue schema = event.value(QStringLiteral("schema_version"));
    if (!schema.isDouble() || schema.toDouble() != JAKE_PROTOCOL_VERSION) {
        m_view.incompatible = true;
        return Result::Incompatible;
    }
    const QJsonValue typeValue = event.value(QStringLiteral("type"));
    QJsonValue payloadValue = event.value(QStringLiteral("payload"));
    if (payloadValue.isUndefined() || payloadValue.isNull())
        payloadValue = QJsonObject();
    if (!typeValue.isString() || !knownTypes().contains(typeValue.toString()) || !payloadValue.isObject())
        return ignore();
    const QString type = typeValue.toString();
    const QJsonObject payload = payloadValue.toObject();

    qint64 sequenceId = 0;
    const QJsonValue sequenceValue = event.value(QStringLiteral("sequence_id"));
    if (sequenceValue.isDouble()) {
        const double number = sequenceValue.toDouble();
        if (number == static_cast<double>(static_cast<qint64>(number)))
            sequenceId = static_cast<qint64>(number);
    }
    const bool fresh = m_freshConnection;
    m_freshConnection = false;
    if (sequenceId > 0) {
        if (sequenceId <= m_view.lastSequenceId && !fresh)
            return ignore(); // duplicato o fuori ordine nella stessa connessione
        m_view.lastSequenceId = sequenceId; // su una connessione nuova: server ripartito da capo
    }
    const QJsonValue traceValue = event.value(QStringLiteral("trace_id"));
    reduce(type, payload, traceValue.isString() ? traceValue.toString() : QString());
    m_lastType = type;
    m_lastPayload = payload;
    return Result::Applied;
}

void HudEventReducer::push(QJsonArray &items, const QJsonValue &item) {
    items.append(item);
    while (items.size() > kMaxItems)
        items.removeFirst();
}

void HudEventReducer::reduce(const QString &type, const QJsonObject &payload, const QString &traceId) {
    using namespace JakeHudEventType;
    if (stateEvents().contains(type)) {
        m_view.state = type;
    } else if (isType(type, USER_MESSAGE)) {
        push(m_view.messages, QJsonArray{QStringLiteral("user"), text(payload, "text")});
    } else if (isType(type, JAKE_MESSAGE)) {
        const QString message = text(payload, "text");
        if (!message.isEmpty()) {
            push(m_view.messages, QJsonArray{QStringLiteral("jake"), message});
            m_view.state = QStringLiteral("IDLE");
        } else {
            m_view.state = QStringLiteral("SPEAKING");
        }
    } else if (isType(type, AGENT_STEP)) {
        m_view.stepIndex = integer(payload, "step");
        m_view.stepDescription = text(payload, "description");
        m_view.state = QStringLiteral("EXECUTING");
    } else if (isType(type, ERROR)) {
        m_view.lastError = text(payload, "detail");
        m_view.state = QStringLiteral("ERROR");
    } else if (isType(type, NOTIFICATION)) {
        push(m_view.notifications, QJsonArray{text(payload, "kind"), text(payload, "text")});
    } else if (isType(type, HUD_SHOW)) {
        m_view.visible = true;
    } else if (isType(type, HUD_HIDE)) {
        m_view.visible = false;
    } else if (isType(type, DEVICE_HANDOFF)) {
        m_view.activeDevice = text(payload, "to");
    } else if (isType(type, MIC_STATE)) {
        m_view.micOpen = payload.value(QStringLiteral("open")) == QJsonValue(true);
        m_view.micDiscarding = payload.value(QStringLiteral("discarding")) == QJsonValue(true);
        m_view.micReason = text(payload, "reason");
    } else if (isType(type, TRANSCRIPT)) {
        reduceTranscript(payload);
    } else if (isType(type, UNDO) || isType(type, VERIFICATION)) {
        push(m_view.evidence, QJsonObject{
            {"kind", type.toLower()}, {"intent", text(payload, "intent")}, {"verified", text(payload, "verified")},
            {"trace_id", traceId}, // F4.1.1: correla la prova alle ricevute del ledger
        });
    } else if (isType(type, CONFIRMATION)) {
        if (payload.value(QStringLiteral("pending")) == QJsonValue(true)) {
            m_view.confirmationPending = true;
            m_view.confirmationIntent = text(payload, "intent");
            m_view.confirmationReason = text(payload, "reason");
            m_view.confirmationRisk = text(payload, "risk");
            m_view.confirmationExternal = payload.value(QStringLiteral("external_source")) == QJsonValue(true);
            m_view.confirmationTraceId = traceId;
            m_view.state = QStringLiteral("WAITING");
        } else {
            m_view.confirmationPending = false;
            m_view.confirmationIntent.clear();
            m_view.confirmationReason.clear();
            m_view.confirmationRisk.clear();
            m_view.confirmationExternal = false;
            m_view.confirmationTraceId.clear();
            if (m_view.state == QLatin1String("WAITING"))
                m_view.state = QStringLiteral("IDLE");
        }
    } else if (isType(type, ACTION_RECEIPT) || isType(type, UNDO_AVAILABLE)) {
        reduceActivity(type, payload, traceId);
    } else if (isType(type, SELECTOR_INSPECTION)) {
        const QJsonValue inspectionValue = payload.value(QStringLiteral("inspection"));
        const QJsonObject inspection = inspectionValue.isObject() ? inspectionValue.toObject() : QJsonObject();
        m_view.inspectionVerdict = text(inspection, "verdict");
        const QString reason = text(inspection, "reason");
        m_view.inspectionReason = reason.isEmpty() ? text(payload, "error") : reason;
    }
}

void HudEventReducer::reduceActivity(const QString &type, const QJsonObject &payload, const QString &traceId) {
    const QString actionId = text(payload, "action_id");
    if (actionId.isEmpty()) {
        m_view.ignored += 1;
        return;
    }
    int index = -1;
    for (int i = 0; i < m_view.activities.size(); ++i) {
        if (m_view.activities.at(i).toObject().value("action_id").toString() == actionId) {
            index = i;
            break;
        }
    }
    QJsonObject item = index >= 0 ? m_view.activities.at(index).toObject() : QJsonObject{
        {"action_id", actionId}, {"intent", QString()}, {"outcome", QString()}, {"verified", QString()},
        {"trace_id", QString()}, {"undo_intent", QString()}, {"undo_expires_at", 0},
    };
    if (type == QLatin1String(JakeHudEventType::ACTION_RECEIPT)) {
        item.insert("intent", text(payload, "intent"));
        item.insert("outcome", text(payload, "outcome"));
        item.insert("verified", text(payload, "verified"));
        item.insert("trace_id", traceId);
        if (item.value("outcome").toString() != QLatin1String("success"))
            m_view.lastOutcome = QStringLiteral("error");
        else
            m_view.lastOutcome = item.value("verified").toString() == QLatin1String("verified")
                ? QStringLiteral("success") : QStringLiteral("warning");
    } else {
        item.insert("undo_intent", text(payload, "compensating_intent"));
        const QJsonValue expires = payload.value(QStringLiteral("expires_at"));
        item.insert("undo_expires_at", expires.isDouble() ? static_cast<qint64>(expires.toDouble()) : 0);
    }
    if (index >= 0)
        m_view.activities.replace(index, item);
    else
        push(m_view.activities, item);
}

void HudEventReducer::reduceTranscript(const QJsonObject &payload) {
    const QString kind = text(payload, "kind");
    const QString utterance = text(payload, "utterance_id");
    const int revision = integer(payload, "revision");
    if ((kind != QLatin1String("partial") && kind != QLatin1String("final")) || utterance.isEmpty()) {
        m_view.ignored += 1;
        return;
    }
    if (utterance == m_view.transcriptUtterance && (m_view.transcriptFinal || revision <= m_view.transcriptRevision))
        return; // revisione vecchia, o partial arrivato dopo il final
    m_view.transcriptUtterance = utterance;
    m_view.transcriptRevision = revision;
    m_view.transcriptText = text(payload, "text");
    m_view.transcriptFinal = kind == QLatin1String("final");
}
