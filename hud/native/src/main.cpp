#include <QGuiApplication>
#include <QQmlApplicationEngine>
#include <QQmlContext>

int main(int argc, char *argv[]) {
    QGuiApplication app(argc, argv);
    app.setApplicationName(QStringLiteral("Jake HUD"));
    app.setApplicationVersion(QString::fromLatin1(JAKE_PRODUCT_VERSION));
    app.setOrganizationName(QStringLiteral("Jake"));

    QQmlApplicationEngine engine;
    // JakeClient e' registrato tramite QML_ELEMENT (vedi src/JakeClient.h) nel modulo QML
    // "JakeHud" dichiarato in CMakeLists.txt: da QML basta "import JakeHud" e "JakeClient { }".
    engine.loadFromModule("JakeHud", "Main");

    if (engine.rootObjects().isEmpty())
        return -1;

    return app.exec();
}
