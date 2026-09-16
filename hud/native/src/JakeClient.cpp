#include "JakeClient.h"

#include <QJsonDocument>
#include <QJsonObject>
#include <QNetworkRequest>
#include <QUuid>
#include <QUrl>

#include "HudEventTypes.h" // generato da tools/generate_hud_event_types.py, vedi CMakeLists.txt

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

    QNetworkRequest request(QUrl(m_baseUrl + QStringLiteral("/events")));
    request.setRawHeader("Accept", "text/event-stream");
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
    m_eventBuffer += QString::fromUtf8(m_eventStream->readAll());
    // Un evento SSE e' un blocco terminato da una riga vuota (\n\n): puo' arrivare a pezzi,
    // quindi si processano solo i blocchi completi gia' arrivati, tenendo il resto in coda.
    int separatorIndex;
    while ((separatorIndex = m_eventBuffer.indexOf(QStringLiteral("\n\n"))) != -1) {
        const QString block = m_eventBuffer.left(separatorIndex);
        m_eventBuffer.remove(0, separatorIndex + 2);
        for (const QString &line : block.split('\n')) {
            if (line.startsWith(QStringLiteral("data: ")))
                handleEventLine(line.mid(6));
            // Le righe che iniziano con ":" sono keep-alive SSE (vedi companion_server.py,
            // SSE_KEEPALIVE_SECONDS): si ignorano, servono solo a tenere viva la connessione.
        }
    }
    setConnected(true);
}

void JakeClient::onEventStreamFinished() {
    setConnected(false);
    if (m_eventStream != nullptr) {
        if (m_eventStream->error() != QNetworkReply::NoError)
            emit errorOccurred(m_eventStream->errorString());
        m_eventStream->deleteLater();
        m_eventStream = nullptr;
    }
}

void JakeClient::handleEventLine(const QString &jsonLine) {
    const auto doc = QJsonDocument::fromJson(jsonLine.toUtf8());
    if (!doc.isObject()) return;
    const auto object = doc.object();
    if (object.value("schema_version").toInt(-1) != JAKE_PROTOCOL_VERSION) {
        emit errorOccurred(QStringLiteral("Evento Jake con versione protocollo non compatibile"));
        return;
    }
    const QString type = object.value("type").toString();
    const auto payload = object.value("payload").toObject();

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
