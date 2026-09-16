#include "JakeClient.h"

#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QPointer>
#include <QSignalSpy>
#include <QTcpServer>
#include <QTcpSocket>
#include <QtTest>

// Nessuna finestra, nessun server/dato reale: esercita il parser e i segnali VERI del client.
class JakeClientContractTests : public QObject {
    Q_OBJECT

private slots:
    void fixtures_data() {
        QTest::addColumn<QString>("raw");
        QTest::addColumn<bool>("accepted");
        QFile file(QStringLiteral(JAKE_HUD_CONTRACT_FIXTURES));
        QVERIFY2(file.open(QIODevice::ReadOnly), qPrintable(file.errorString()));
        const auto doc = QJsonDocument::fromJson(file.readAll());
        QVERIFY(doc.isArray());
        QVERIFY(!doc.array().isEmpty());
        for (const auto &value : doc.array()) {
            const auto row = value.toObject();
            QTest::newRow(row.value("name").toString().toUtf8().constData())
                << row.value("raw").toString() << row.value("accepted").toBool();
        }
    }

    void fixtures() {
        QFETCH(QString, raw);
        QFETCH(bool, accepted);
        JakeClient client;
        client.setState(QStringLiteral("BASELINE"));
        client.m_lastSequenceId = 987;
        QSignalSpy states(&client, &JakeClient::stateChanged);
        QSignalSpy messages(&client, &JakeClient::messageReceived);
        QSignalSpy steps(&client, &JakeClient::agentStep);
        QSignalSpy notifications(&client, &JakeClient::notification);
        QSignalSpy errors(&client, &JakeClient::errorOccurred);
        QSignalSpy handoffs(&client, &JakeClient::deviceHandoff);
        QSignalSpy visibility(&client, &JakeClient::visibilityRequested);
        client.handleEventLine(raw);
        if (!accepted) {
            QCOMPARE(client.state(), QStringLiteral("BASELINE"));
            QCOMPARE(client.m_lastSequenceId, qint64(987));
            QCOMPARE(client.activeDevice(), QString());
            QCOMPARE(states.size() + messages.size() + steps.size() + notifications.size()
                     + handoffs.size() + visibility.size(), qsizetype(0));
            return; // Un diagnostico generico e' ammesso; MAI testo/payload coercizzato.
        }
        const auto event = QJsonDocument::fromJson(raw.toUtf8()).object();
        const auto payload = event.value("payload").toObject();
        const QString type = event.value("type").toString();
        const auto sequence = event.value("sequence_id").toInteger(0);
        QCOMPARE(client.m_lastSequenceId, sequence > 0 ? sequence : qint64(987));
        QString expectedState = type;
        if (type == "USER_MESSAGE" || type == "JAKE_MESSAGE") {
            QCOMPARE(messages.size(), qsizetype(1));
            QCOMPARE(messages[0][0].toString(), type == "USER_MESSAGE" ? QString("user") : QString("jake"));
            QCOMPARE(messages[0][1].toString(), payload.value("text").toString());
            expectedState = type == "JAKE_MESSAGE" ? "IDLE" : "BASELINE";
        } else if (type == "AGENT_STEP") {
            QCOMPARE(steps.size(), qsizetype(1));
            QCOMPARE(steps[0][0].toInt(), payload.value("step").toInt());
            QCOMPARE(steps[0][1].toString(), payload.value("description").toString());
            expectedState = "EXECUTING";
        } else if (type == "NOTIFICATION") {
            QCOMPARE(notifications.size(), qsizetype(1));
            QCOMPARE(notifications[0][0].toString(), payload.value("kind").toString());
            QCOMPARE(notifications[0][1].toString(), payload.value("text").toString());
            expectedState = "BASELINE";
        } else if (type == "DEVICE_HANDOFF") {
            QCOMPARE(handoffs.size(), qsizetype(1));
            QCOMPARE(handoffs[0][0].toString(), payload.value("from").toString());
            QCOMPARE(handoffs[0][1].toString(), payload.value("to").toString());
            QCOMPARE(client.activeDevice(), payload.value("to").toString());
            expectedState = "BASELINE";
        } else if (type == "HUD_SHOW" || type == "HUD_HIDE") {
            QCOMPARE(visibility.size(), qsizetype(1));
            QCOMPARE(visibility[0][0].toBool(), type == "HUD_SHOW");
            expectedState = "BASELINE";
        } else if (type == "ERROR") {
            QCOMPARE(errors.size(), qsizetype(1));
            QCOMPARE(errors[0][0].toString(), payload.value("detail").toString());
        }
        if (type != "ERROR") QCOMPARE(errors.size(), qsizetype(0));
        QCOMPARE(client.state(), expectedState);
    }

    void protocolMismatchIsReportedOnce() {
        JakeClient client;
        QSignalSpy errors(&client, &JakeClient::errorOccurred);
        const auto raw = QStringLiteral("{\"schema_version\":999,\"type\":\"IDLE\"}");
        client.handleEventLine(raw);
        client.handleEventLine(raw);
        QCOMPARE(errors.size(), qsizetype(1));
        QCOMPARE(client.m_lastSequenceId, qint64(0));
    }

    void incompatibleStreamDisconnectsWithoutDuplicateErrors() {
        QTcpServer server;
        QVERIFY(server.listen(QHostAddress::LocalHost));
        QPointer<QTcpSocket> stream;
        connect(&server, &QTcpServer::newConnection, this, [&]() {
            while (server.hasPendingConnections()) {
                auto *socket = server.nextPendingConnection();
                connect(socket, &QTcpSocket::readyRead, socket, [&, socket]() {
                    const auto request = socket->readAll();
                    if (request.startsWith("GET /events ")) {
                        stream = socket;
                        socket->write("HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n");
                    } else if (request.startsWith("GET /status ")) {
                        const QByteArray body = "{\"protocol_version\":" + QByteArray::number(JAKE_PROTOCOL_VERSION) + "}";
                        socket->write("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                                      + QByteArray::number(body.size()) + "\r\nConnection: close\r\n\r\n" + body);
                        socket->disconnectFromHost();
                    }
                });
            }
        });
        JakeClient client;
        QSignalSpy errors(&client, &JakeClient::errorOccurred);
        client.connectToJake(QStringLiteral("http://127.0.0.1:%1").arg(server.serverPort()));
        QTRY_VERIFY(stream && client.connected());
        stream->write("data: {\"schema_version\":999,\"type\":\"IDLE\"}\n\n"
                      "data: {\"schema_version\":999,\"type\":\"IDLE\"}\n\n");
        QTRY_VERIFY(!errors.isEmpty());
        QTest::qWait(20); // lascia consegnare finished(), non il timer di reconnect (3 s)
        QCOMPARE(errors.size(), qsizetype(1));
        QVERIFY(!client.connected());
        QVERIFY(client.m_eventStream == nullptr);
    }

    void unicodeSplitAcrossTcpChunksIsNotCorrupted() {
        QTcpServer server;
        QVERIFY(server.listen(QHostAddress::LocalHost));
        QPointer<QTcpSocket> stream;
        connect(&server, &QTcpServer::newConnection, this, [&]() {
            while (server.hasPendingConnections()) {
                auto *socket = server.nextPendingConnection();
                connect(socket, &QTcpSocket::readyRead, socket, [&, socket]() {
                    const auto request = socket->readAll();
                    if (request.startsWith("GET /events ")) {
                        stream = socket;
                        socket->write("HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n");
                    } else {
                        const QByteArray body = "{\"protocol_version\":" + QByteArray::number(JAKE_PROTOCOL_VERSION) + "}";
                        socket->write("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                                      + QByteArray::number(body.size()) + "\r\nConnection: close\r\n\r\n" + body);
                        socket->disconnectFromHost();
                    }
                });
            }
        });
        QFile file(QStringLiteral(JAKE_HUD_CONTRACT_FIXTURES));
        QVERIFY(file.open(QIODevice::ReadOnly));
        QByteArray raw;
        for (const auto &value : QJsonDocument::fromJson(file.readAll()).array()) {
            const auto row = value.toObject();
            if (row.value("name").toString() == "user-unicode") raw = row.value("raw").toString().toUtf8();
        }
        QVERIFY(!raw.isEmpty());
        const QByteArray block = "data: " + raw + "\n\n";
        const auto split = block.indexOf(QByteArray::fromHex("c3a8")) + 1; // fra i due byte di è
        QVERIFY(split > 0);
        JakeClient client;
        QSignalSpy messages(&client, &JakeClient::messageReceived);
        client.connectToJake(QStringLiteral("http://127.0.0.1:%1").arg(server.serverPort()));
        QTRY_VERIFY(stream && client.connected());
        stream->write(block.left(split));
        QTRY_VERIFY(!client.m_eventBuffer.isEmpty()); // prima meta' DAVVERO letta prima della seconda
        QCOMPARE(messages.size(), qsizetype(0));
        stream->write(block.mid(split));
        QTRY_COMPARE(messages.size(), qsizetype(1));
        QCOMPARE(messages[0][1].toString(), QJsonDocument::fromJson(raw).object().value("payload").toObject().value("text").toString());
    }
};

QTEST_GUILESS_MAIN(JakeClientContractTests)
#include "JakeClientContractTests.moc"
