#include <QCommandLineParser>
#include <QFile>
#include <QGuiApplication>
#include <QJsonDocument>
#include <QJsonObject>
#include <QQmlApplicationEngine>
#include <QQmlContext>
#include <QtGlobal>

#ifdef Q_OS_WIN
#include <windows.h>
#endif

namespace {
// F4.4.5/F4.7.2: "reduced motion" segue l'impostazione di Windows "Mostra animazioni" (Accessibilita').
bool systemPrefersReducedMotion() {
#ifdef Q_OS_WIN
    BOOL animations = TRUE;
    if (SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION, 0, &animations, 0))
        return animations == FALSE;
#endif
    return false;
}
} // namespace

int main(int argc, char *argv[]) {
    QGuiApplication app(argc, argv);
    app.setApplicationName(QStringLiteral("Jake HUD"));
    app.setApplicationVersion(QString::fromLatin1(JAKE_PRODUCT_VERSION));
    app.setOrganizationName(QStringLiteral("Jake"));

    // F4.8.2: il core (core/native_hud.py) passa l'indirizzo del companion server che ha davvero
    // avviato (host/porta da config); senza argomento resta il default di Main.qml.
    QCommandLineParser parser;
    parser.addHelpOption();
    QCommandLineOption jakeUrl(QStringLiteral("jake-url"), QStringLiteral("Indirizzo del companion server di Jake."),
                               QStringLiteral("url"));
    parser.addOption(jakeUrl);
    // F7/F4.8.2: la credenziale per-dispositivo dell'HUD arriva sullo stdin (una riga JSON
    // {"device_id", "token"}), mai nella riga di comando (visibile agli altri processi) ne' in config.
    QCommandLineOption credentialsStdin(QStringLiteral("credentials-stdin"),
                                        QStringLiteral("Legge device_id e token del companion dallo stdin."));
    parser.addOption(credentialsStdin);
    parser.process(app);

    QVariantMap initial;
    initial.insert(QStringLiteral("reducedMotion"),
                   systemPrefersReducedMotion() || qEnvironmentVariable("JAKE_HUD_REDUCED_MOTION") == QLatin1String("1"));
    // JAKE_HUD_QUALITY=low per GPU deboli/batteria (F4.3.5); JAKE_HUD_ORB=2d forza l'orb 2D.
    initial.insert(QStringLiteral("orbQuality"), qEnvironmentVariable("JAKE_HUD_QUALITY") == QLatin1String("low")
                   ? QStringLiteral("low") : QStringLiteral("high"));
#ifdef JAKE_HAS_QUICK3D
    const bool use3d = qEnvironmentVariable("JAKE_HUD_ORB") != QLatin1String("2d");
#else
    const bool use3d = false;
#endif
    initial.insert(QStringLiteral("orb3d"), use3d);
    if (parser.isSet(jakeUrl))
        initial.insert(QStringLiteral("jakeBaseUrl"), parser.value(jakeUrl));
    if (parser.isSet(credentialsStdin)) {
        QFile input;
        if (input.open(stdin, QIODevice::ReadOnly)) {
            const QJsonObject credentials = QJsonDocument::fromJson(input.readLine(8192)).object();
            initial.insert(QStringLiteral("jakeDeviceId"), credentials.value(QStringLiteral("device_id")).toString());
            initial.insert(QStringLiteral("jakeToken"), credentials.value(QStringLiteral("token")).toString());
        }
    }

    QQmlApplicationEngine engine;
    engine.setInitialProperties(initial);
    // JakeClient e' registrato tramite QML_ELEMENT (vedi src/JakeClient.h) nel modulo QML
    // "JakeHud" dichiarato in CMakeLists.txt: da QML basta "import JakeHud" e "JakeClient { }".
    engine.loadFromModule("JakeHud", "Main");

    if (engine.rootObjects().isEmpty())
        return -1;

    return app.exec();
}
