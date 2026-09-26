// F4.1 (criterio d'uscita): il riduttore C++ esegue la STESSA suite di fixture del riferimento
// Python (tests/fixtures/hud_contract.json, tests/test_hud_contract.py). Uso:
//     JakeHudContractTests <percorso di hud_contract.json>
// Esce con 0 solo se ogni scenario produce esattamente i campi attesi.
#include <QCoreApplication>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QTextStream>

#include "HudEventReducer.h"

int main(int argc, char *argv[]) {
    QCoreApplication app(argc, argv);
    QTextStream out(stdout);
    if (argc < 2) {
        out << "uso: JakeHudContractTests <hud_contract.json>\n";
        return 2;
    }
    QFile file(QString::fromLocal8Bit(argv[1]));
    if (!file.open(QIODevice::ReadOnly)) {
        out << "fixture non leggibile: " << file.fileName() << "\n";
        return 2;
    }
    const QJsonArray scenarios = QJsonDocument::fromJson(file.readAll()).object().value("scenarios").toArray();
    if (scenarios.size() < 10) {
        out << "troppo pochi scenari nella fixture: " << scenarios.size() << "\n";
        return 2;
    }

    int failures = 0;
    for (const QJsonValue &scenarioValue : scenarios) {
        const QJsonObject scenario = scenarioValue.toObject();
        HudEventReducer reducer;
        for (const QJsonValue &stepValue : scenario.value("steps").toArray()) {
            const QJsonObject step = stepValue.toObject();
            if (step.value("connect").toBool()) {
                reducer.connectionStarted();
            } else if (step.contains("event")) {
                QJsonObject event = step.value("event").toObject();
                if (!event.contains("schema_version"))
                    event.insert("schema_version", JAKE_PROTOCOL_VERSION);
                reducer.applyLine(QJsonDocument(event).toJson(QJsonDocument::Compact));
            } else {
                reducer.applyLine(step.value("line").toString().toUtf8());
            }
        }
        const QJsonObject snapshot = reducer.view().snapshot();
        const QJsonObject expect = scenario.value("expect").toObject();
        for (auto it = expect.begin(); it != expect.end(); ++it) {
            if (!snapshot.contains(it.key())) {
                out << "FAIL " << scenario.value("name").toString() << ": campo sconosciuto " << it.key() << "\n";
                ++failures;
            } else if (snapshot.value(it.key()) != it.value()) {
                out << "FAIL " << scenario.value("name").toString() << ": " << it.key() << " = "
                    << QJsonDocument(QJsonArray{snapshot.value(it.key())}).toJson(QJsonDocument::Compact)
                    << ", atteso " << QJsonDocument(QJsonArray{it.value()}).toJson(QJsonDocument::Compact) << "\n";
                ++failures;
            }
        }
    }
    out << scenarios.size() << " scenari, " << failures << " differenze\n";
    return failures == 0 ? 0 : 1;
}
