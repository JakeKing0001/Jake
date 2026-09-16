#include "JakeClient.h"

#include <QJsonDocument>
#include <QJsonObject>
#include <QNetworkRequest>
#include <QTimer>
#include <QUuid>
#include <QUrl>
#include <algorithm>
#include <cmath>

#include "HudEventTypes.h" // generato da tools/generate_hud_event_types.py, vedi CMakeLists.txt

namespace {
// F4.1.3 (lato C++, seconda fetta): ritardo FISSO (non backoff esponenziale, "prima deve
// funzionare" - vedi hud/native/README.md) - un assistente personale con un solo server locale
// non ha lo stesso rischio di "thundering herd" di un servizio con molti client concorrenti.
constexpr int kReconnectDelayMs = 3000;

bool isIntegerInRange(const QJsonValue &value, qint64 maximum) {
    const auto integer = value.toInteger(-1);
    return value.isDouble() && integer >= 0 && integer <= maximum;
}

bool isValidEvent(const QJsonObject &object, const QString &type, const QJsonObject &payload) {
    if (!std::any_of(JakeHudEventType::ALL.begin(), JakeHudEventType::ALL.end(),
                     [&](const char *name) { return type == QLatin1String(name); })) return false;
    const auto rawPayload = object.value("payload");
    if (!rawPayload.isUndefined() && !rawPayload.isNull() && !rawPayload.isObject()) return false;
    const auto at = object.value("at");
    if (!at.isUndefined() && (!at.isDouble() || !std::isfinite(at.toDouble()) || at.toDouble() < 0)) return false;
    const auto sequence = object.value("sequence_id");
    if (!sequence.isUndefined() && !isIntegerInRange(sequence, JakeHudContract::SEQUENCE_ID_MAX)) return false;
    const auto trace = object.value("trace_id");
    if (!trace.isUndefined() && !trace.isNull() && !trace.isString()) return false;
    for (const auto &rule : JakeHudContract::PAYLOAD_RULES) {
        if (QLatin1String(rule.event) != QLatin1String("*") && type != QLatin1String(rule.event)) continue;
        const auto value = payload.value(QLatin1String(rule.key));
        if (value.isUndefined()) continue;
        const auto kind = QLatin1String(rule.kind);
        if (kind == QLatin1String("string")) {
            if (!value.isString()) return false;
        } else if (kind == QLatin1String("step")) {
            if (!isIntegerInRange(value, JakeHudContract::STEP_MAX)) return false;
        } else if (!value.isString() || !std::any_of(
                       JakeHudContract::VERIFICATION_VALUES.begin(), JakeHudContract::VERIFICATION_VALUES.end(),
                       [&](const char *status) { return value.toString() == QLatin1String(status); })) {
            return false;
        }
    }
    return true;
}
}

JakeClient::JakeClient(QObject *parent)
    : QObject(parent), m_manager(new QNetworkAccessManager(this)) {
    // Un id stabile per la durata del processo: basta per identificare "questo HUD" nel
    // passaggio di consegne tra dispositivi (v5.9, DeviceRegistry in core/device_registry.py).
    m_deviceId = QStringLiteral("native-hud-%1").arg(QUuid::createUuid().toString(QUuid::Id128).left(8));
}

void JakeClient::setConnected(bool value) {
    if (m_connected == value) return;
    m_connected = value;
    emit connectedChanged();
}

void JakeClient::setState(const QString &value) {
    if (m_state == value) return;
    m_state = value;
    emit stateChanged();
}

void JakeClient::setActiveDevice(const QString &value) {
    if (m_activeDevice == value) return;
    m_activeDevice = value;
    emit activeDeviceChanged();
}

void JakeClient::connectToJake(const QString &baseUrl) {
    m_baseUrl = baseUrl;
    if (m_baseUrl.endsWith('/')) m_baseUrl.chop(1);

    if (m_eventStream != nullptr) {
        m_eventStream->abort();
        m_eventStream = nullptr;
    }
    m_eventBuffer.clear();
    m_protocolMismatchReported = false;

    QNetworkRequest request(QUrl(m_baseUrl + QStringLiteral("/events")));
    request.setRawHeader("Accept", "text/event-stream");
    // F4.1.3 (lato C++, seconda fetta): sfrutta per davvero il meccanismo di resume gia'
    // costruito lato server (core/event_bus.py::EventBus.subscribe_with_replay, PR #133) -
    // m_lastSequenceId=0 (mai connesso prima) non manda l'header affatto, stesso comportamento
    // di sempre per la primissima connessione.
    if (m_lastSequenceId > 0)
        request.setRawHeader("Last-Event-ID", QByteArray::number(m_lastSequenceId));
    m_eventStream = m_manager->get(request);
    connect(m_eventStream, &QIODevice::readyRead, this, &JakeClient::onEventStreamReadyRead);
    connect(m_eventStream, &QNetworkReply::finished, this, &JakeClient::onEventStreamFinished);

    fetchStatus();
}

void JakeClient::fetchStatus() {
    QNetworkRequest request(QUrl(m_baseUrl + QStringLiteral("/status")));
    QNetworkReply *reply = m_manager->get(request);
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        if (reply->error() == QNetworkReply::NoError) {
            const auto doc = QJsonDocument::fromJson(reply->readAll());
            const auto object = doc.object();
            if (object.value("protocol_version").toInt(-1) != JAKE_PROTOCOL_VERSION) {
                setConnected(false);
                emit errorOccurred(QStringLiteral("Versione protocollo Jake non compatibile"));
                reply->deleteLater();
                return;
            }
            if (m_protocolMismatchReported) {
                reply->deleteLater();
                return;
            }
            setConnected(true);
            if (object.value("active_device").isString())
                setActiveDevice(object.value("active_device").toString());
        } else {
            setConnected(false);
            emit errorOccurred(reply->errorString());
        }
        reply->deleteLater();
    });
}

void JakeClient::onEventStreamReadyRead() {
    auto *stream = qobject_cast<QNetworkReply *>(sender());
    if (stream == nullptr || stream != m_eventStream || m_protocolMismatchReported) return;
    m_eventBuffer += stream->readAll();
    // Un evento SSE e' un blocco terminato da una riga vuota (\n\n): puo' arrivare a pezzi,
    // quindi si processano solo i blocchi completi gia' arrivati, tenendo il resto in coda.
    qsizetype separatorIndex;
    while ((separatorIndex = m_eventBuffer.indexOf("\n\n")) != -1) {
        const QByteArray block = m_eventBuffer.left(separatorIndex);
        m_eventBuffer.remove(0, separatorIndex + 2);
        for (const QByteArray &line : block.split('\n')) {
            if (line.startsWith("data: "))
                handleEventLine(QString::fromUtf8(line.mid(6)));
            if (m_protocolMismatchReported) return; // abort non deve essere seguito da connected=true
            // Le righe che iniziano con ":" sono keep-alive SSE (vedi companion_server.py,
            // SSE_KEEPALIVE_SECONDS): si ignorano, servono solo a tenere viva la connessione.
        }
    }
    setConnected(true);
}

void JakeClient::onEventStreamFinished() {
    // F4.1.3 (lato C++, seconda fetta): usa sender() invece del membro m_eventStream - corregge
    // un buco reale preesistente, mai raggiunto finche' connectToJake() veniva chiamata una sola
    // volta all'avvio (Main.qml::Component.onCompleted), ora raggiungibile per davvero con la
    // riconnessione automatica sotto. Lo stream VECCHIO abortito da un connectToJake() successivo
    // (es. proprio questa riconnessione) finisce comunque con finished() emesso in modo
    // asincrono - se questo slot leggesse il MEMBRO m_eventStream invece del mittente reale del
    // segnale, troverebbe gia' il nuovo stream (connectToJake lo riassegna subito) e lo
    // cancellerebbe/nullerebbe per errore, scambiando "il vecchio stream e' finito" con "il
    // nuovo stream e' finito".
    auto *finishedStream = qobject_cast<QNetworkReply *>(sender());
    if (finishedStream == nullptr) return;
    const bool isCurrentStream = (finishedStream == m_eventStream);
    if (isCurrentStream) {
        setConnected(false);
        if (finishedStream->error() != QNetworkReply::NoError && !m_protocolMismatchReported)
            emit errorOccurred(finishedStream->errorString());
        m_eventStream = nullptr;
    }
    finishedStream->deleteLater();
    // F4.1.3: riconnessione automatica dopo un ritardo fisso, mandando l'ultimo sequence_id
    // visto (m_lastSequenceId, aggiornato in handleEventLine() sotto) come Last-Event-ID - il
    // meccanismo di resume lato server (EventBus.subscribe_with_replay, PR #133) recupera cosi'
    // gli eventi persi durante l'interruzione invece di farli sparire silenziosamente. Solo per
    // lo stream CORRENTE: un vecchio stream abortito da una riconnessione deliberata non deve
    // programmarne una seconda.
    if (isCurrentStream && !m_baseUrl.isEmpty()) {
        const QString baseUrl = m_baseUrl;
        QTimer::singleShot(kReconnectDelayMs, this, [this, baseUrl]() { connectToJake(baseUrl); });
    }
}

void JakeClient::handleEventLine(const QString &jsonLine) {
    if (m_protocolMismatchReported) return;
    const auto doc = QJsonDocument::fromJson(jsonLine.toUtf8());
    if (!doc.isObject()) return;
    const auto object = doc.object();
    const auto version = object.value("schema_version");
    if (!version.isUndefined() && (!isIntegerInRange(version, JakeHudContract::SEQUENCE_ID_MAX)
                                  || version.toInteger(-1) != JAKE_PROTOCOL_VERSION)) {
        // F4.1.6 ("definire compatibility window"): finestra ZERO - nessuna tolleranza tra
        // versioni diverse, per costruzione (JAKE_PROTOCOL_VERSION e' generato dallo stesso
        // config/release.json letto da core/version.py, vedi CMakeLists.txt - le due parti sono
        // sempre build-compatibili quando ricompilate insieme; uno scarto e' sempre un binario
        // HUD non ricompilato dopo un cambio di schema, mai una versione "abbastanza vicina" da
        // tollerare). Segnalato UNA sola volta per connessione (non un errorOccurred per ogni
        // evento sullo stesso stream, vedi m_protocolMismatchReported) e lo stream si interrompe
        // subito: onEventStreamFinished() programmera' comunque una riconnessione automatica fra
        // kReconnectDelayMs, che fallira' di nuovo nello stesso modo finche' l'HUD non viene
        // ricompilato - lo stesso comportamento gia' definito per /status (fetchStatus()).
        if (!m_protocolMismatchReported) {
            m_protocolMismatchReported = true;
            setConnected(false);
            emit errorOccurred(QStringLiteral("Evento Jake con versione protocollo non compatibile"));
        }
        if (m_eventStream != nullptr)
            m_eventStream->abort();
        return;
    }
    const QString type = object.value("type").toString();
    const auto payload = object.value("payload").toObject();
    // F4.1.4: rifiuto PRIMA di qualsiasi effetto/segnale o avanzamento di Last-Event-ID.
    // Le regole sono generate dalla fonte Python; niente coercizioni a stringa/oggetto vuoto.
    if (!object.value("type").isString() || !isValidEvent(object, type, payload)) return;
    // F4.1.3: aggiornato per OGNI evento riuscito, indipendentemente dal tipo - e' quello che
    // connectToJake() manda come Last-Event-ID alla prossima riconnessione.
    const qint64 sequenceId = object.value("sequence_id").toInteger(0);
    if (sequenceId > 0)
        m_lastSequenceId = sequenceId;

    // F4.1.2: JakeHudEventType::* (generato da tools/generate_hud_event_types.py DALLA fonte
    // vera, core/hud_protocol.py::EventType) invece di stringhe letterali scritte qui a mano -
    // un tipo aggiunto/rinominato lato Python fa fallire questa build invece di disallinearsi in
    // silenzio.
    if (type == QLatin1String(JakeHudEventType::USER_MESSAGE)) {
        emit messageReceived(QStringLiteral("user"), payload.value("text").toString());
    } else if (type == QLatin1String(JakeHudEventType::JAKE_MESSAGE)) {
        emit messageReceived(QStringLiteral("jake"), payload.value("text").toString());
        setState(QLatin1String(JakeHudEventType::IDLE));
    } else if (type == QLatin1String(JakeHudEventType::AGENT_STEP)) {
        emit agentStep(payload.value("step").toInt(), payload.value("description").toString());
        setState(QLatin1String(JakeHudEventType::EXECUTING));
    } else if (type == QLatin1String(JakeHudEventType::NOTIFICATION)) {
        emit notification(payload.value("kind").toString(), payload.value("text").toString());
    } else if (type == QLatin1String(JakeHudEventType::ERROR)) {
        emit errorOccurred(payload.value("detail").toString());
        setState(QLatin1String(JakeHudEventType::ERROR));
    } else if (type == QLatin1String(JakeHudEventType::DEVICE_HANDOFF)) {
        setActiveDevice(payload.value("to").toString());
        emit deviceHandoff(payload.value("from").toString(), payload.value("to").toString());
    } else if (type == QLatin1String(JakeHudEventType::HUD_SHOW)) {
        emit visibilityRequested(true);
    } else if (type == QLatin1String(JakeHudEventType::HUD_HIDE)) {
        emit visibilityRequested(false);
    } else {
        // LISTENING/THINKING/EXECUTING/IDLE/DICTATION/PAUSED: il nome dell'evento coincide gia'
        // con lo stato da mostrare, nessuna traduzione necessaria.
        setState(type);
    }
}

void JakeClient::sendCommand(const QString &text) {
    if (text.trimmed().isEmpty()) return;
    QNetworkRequest request(QUrl(m_baseUrl + QStringLiteral("/command")));
    request.setHeader(QNetworkRequest::ContentTypeHeader, "application/json");
    QJsonObject body{{"text", text}};
    QNetworkReply *reply = m_manager->post(request, QJsonDocument(body).toJson());
    connect(reply, &QNetworkReply::finished, this, &JakeClient::onCommandFinished);
    setState(QStringLiteral("THINKING"));
}

void JakeClient::onCommandFinished() {
    auto *reply = qobject_cast<QNetworkReply *>(sender());
    if (reply == nullptr) return;
    if (reply->error() != QNetworkReply::NoError)
        emit errorOccurred(reply->errorString());
    // La risposta vera arriva via SSE come evento JAKE_MESSAGE (companion_server.py non la
    // pubblica lui stesso, lo fa JakeCore.answer sullo stesso event_bus): qui basta sapere che
    // la richiesta e' stata accettata dal server.
    reply->deleteLater();
}

void JakeClient::claimSession() {
    QNetworkRequest request(QUrl(m_baseUrl + QStringLiteral("/devices/") + m_deviceId + QStringLiteral("/claim")));
    request.setHeader(QNetworkRequest::ContentTypeHeader, "application/json");
    QJsonObject body{{"name", QStringLiteral("HUD nativo")}};
    QNetworkReply *reply = m_manager->post(request, QJsonDocument(body).toJson());
    connect(reply, &QNetworkReply::finished, reply, &QObject::deleteLater);
}
