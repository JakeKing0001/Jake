#include <QCommandLineParser>
#include <QGuiApplication>
#include <QQmlApplicationEngine>
#include <QQmlContext>

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
    parser.process(app);

    QQmlApplicationEngine engine;
    if (parser.isSet(jakeUrl))
        engine.setInitialProperties({{QStringLiteral("jakeBaseUrl"), parser.value(jakeUrl)}});
    // JakeClient e' registrato tramite QML_ELEMENT (vedi src/JakeClient.h) nel modulo QML
    // "JakeHud" dichiarato in CMakeLists.txt: da QML basta "import JakeHud" e "JakeClient { }".
    engine.loadFromModule("JakeHud", "Main");

    if (engine.rootObjects().isEmpty())
        return -1;

    return app.exec();
}
