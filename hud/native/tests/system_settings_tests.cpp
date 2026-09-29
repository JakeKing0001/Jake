// F4.3.5/F4.7: la regola della qualita' (scelta esplicita > batteria) e letture di sistema entro i limiti. Esce con 0 se
// ogni caso torna.
#include <QCoreApplication>
#include <QTextStream>

#include "SystemWatch.h"

int main(int argc, char *argv[]) {
    QCoreApplication app(argc, argv);
    QTextStream out(stdout);
    int failures = 0;
    auto check = [&](bool ok, const char *what) {
        if (!ok) {
            out << "FAIL: " << what << "\n";
            ++failures;
        }
    };
    check(SystemSettings::quality(QString(), true) == QLatin1String("low"), "a batteria senza scelta: bassa");
    check(SystemSettings::quality(QString(), false) == QLatin1String("high"), "in carica senza scelta: alta");
    check(SystemSettings::quality(QStringLiteral("high"), true) == QLatin1String("high"), "scelta esplicita vince sulla batteria");
    check(SystemSettings::quality(QStringLiteral("low"), false) == QLatin1String("low"), "scelta esplicita vince in carica");
    check(SystemSettings::quality(QStringLiteral("boh"), false) == QLatin1String("high"), "valore non valido ignorato");
    const double scale = SystemSettings::textScale();
    check(scale >= 1.0 && scale <= 2.25, "dimensioni testo di Windows fra 100% e 225%");
    SystemWatch watch;
    check(qFuzzyCompare(watch.textScale(), scale), "SystemWatch legge subito le impostazioni");
    out << (failures == 0 ? "OK" : "FALLITO") << " (" << failures << " errori)\n";
    return failures == 0 ? 0 : 1;
}
