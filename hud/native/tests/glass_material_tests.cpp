// F4.3/F4.7: regressioni reali del materiale QML, inclusi caricamento, fallback e pixel.
// Renderer software/offscreen: non misura DWM, frame-time GPU o l'aspetto su un monitor fisico.
#include <QColor>
#include <QDir>
#include <QEventLoop>
#include <QGuiApplication>
#include <QHash>
#include <QImage>
#include <QJSValue>
#include <QQmlComponent>
#include <QQmlEngine>
#include <QQmlProperty>
#include <QQuickItem>
#include <QQuickWindow>
#include <QSGRendererInterface>
#include <QTextStream>
#include <QTimer>

#include <algorithm>
#include <cmath>
#include <memory>

namespace {
int failures = 0;
QStringList warnings;
QTextStream out(stdout);

void check(bool ok, const QString &what) {
    if (!ok) {
        out << "FAIL: " << what << "\n";
        ++failures;
    }
}

void messages(QtMsgType type, const QMessageLogContext &, const QString &message) {
    if (type == QtWarningMsg || type == QtCriticalMsg || type == QtFatalMsg)
        warnings.append(message);
}

void settle() {
    QEventLoop loop;
    QTimer::singleShot(50, &loop, &QEventLoop::quit);
    loop.exec();
}

double luminance(const QColor &color) {
    const auto linear = [](double c) { return c <= 0.04045 ? c / 12.92 : std::pow((c + 0.055) / 1.055, 2.4); };
    return 0.2126 * linear(color.redF()) + 0.7152 * linear(color.greenF()) + 0.0722 * linear(color.blueF());
}

double contrast(const QColor &a, const QColor &b) {
    const double first = luminance(a), second = luminance(b);
    return (std::max(first, second) + 0.05) / (std::min(first, second) + 0.05);
}

int panelCount(QObject *theme) {
    return theme->property("panels").value<QJSValue>().property("length").toInt();
}

QImage capture(QQuickWindow &window, const QString &name) {
    window.update();
    settle();
    QImage image = window.grabWindow();
    check(!image.isNull(), name + ": rendering offscreen disponibile");
    if (!image.isNull()) {
        const double dpr = window.devicePixelRatio();
        check(image.size() == QSize(qRound(window.width() * dpr), qRound(window.height() * dpr)),
              name + ": screenshot in pixel fisici alla scala richiesta");
        // Il grab software contiene pixel fisici ma non sempre il metadato DPR: i campioni restano in DIP.
        image.setDevicePixelRatio(dpr);
    }
    const QString artifactDir = qEnvironmentVariable("JAKE_HUD_TEST_ARTIFACT_DIR");
    if (!artifactDir.isEmpty() && !image.isNull()) {
        const QString scaleDir = QDir(artifactDir).filePath(QString("scale-%1").arg(window.devicePixelRatio()));
        check(QDir().mkpath(scaleDir), "cartella degli screenshot");
        check(image.save(QDir(scaleDir).filePath(name + ".png")), "salvataggio " + name);
    }
    return image;
}

// Campiona l'area interna (12 DIP, come ConversationPanel/ActionCenter), non ombra/bordo. Il testo secondario e' il caso
// piu' debole usato nei pannelli: il test fallisce anche se il QML si carica ma il fondo sparisce.
void checkContrast(const QImage &image, const QColor &text, const QString &name) {
    if (image.isNull())
        return;
    const double dpr = image.devicePixelRatio();
    double minimum = 100.0;
    for (int y = 52; y < 208; y += 4) {
        for (int x = 52; x < 348; x += 4) {
            minimum = std::min(minimum, contrast(text, image.pixelColor(qRound(x * dpr), qRound(y * dpr))));
        }
    }
    check(minimum >= 4.5, name + ": contrasto testo secondario >= 4.5 (minimo " + QString::number(minimum, 'f', 2) + ")");
}
} // namespace

int main(int argc, char *argv[]) {
    QGuiApplication app(argc, argv);
    QQuickWindow::setGraphicsApi(QSGRendererInterface::Software);
    const auto oldHandler = qInstallMessageHandler(messages);
    {
        QQmlEngine engine;
        engine.addImportPath(QStringLiteral("qrc:/qt/qml"));
        QQmlComponent component(&engine);
        component.loadFromModule("JakeHud", "GlassPanel");
        check(component.isReady(), "GlassPanel si carica: " + component.errorString());
        if (!component.isReady()) {
            qInstallMessageHandler(oldHandler);
            return 1;
        }
        auto *theme = engine.singletonInstance<QObject *>(qmlTypeId("JakeHud", 1, 0, "Theme"));
        check(theme != nullptr, "Theme e' disponibile");
        if (!theme) {
            qInstallMessageHandler(oldHandler);
            return 1;
        }
        QQuickWindow window;
        window.resize(400, 260);
        std::unique_ptr<QObject> object(component.create());
        auto *panel = qobject_cast<QQuickItem *>(object.get());
        check(panel != nullptr, "GlassPanel viene istanziato");
        if (!panel) {
            qInstallMessageHandler(oldHandler);
            return 1;
        }
        panel->setParentItem(window.contentItem());
        panel->setPosition(QPointF(40, 40));
        panel->setSize(QSizeF(320, 180));
        window.show();
        settle();
        check(panelCount(theme) == 1, "il pannello si registra una sola volta per il click-through");
        auto *surface = panel->findChild<QObject *>("glassSurface");
        auto *tint = panel->findChild<QQuickItem *>("glassTint");
        auto *border = panel->findChild<QObject *>("glassBorder");
        check(surface && tint && border, "strati del materiale disponibili");
        if (!surface || !tint || !border) {
            qInstallMessageHandler(oldHandler);
            return 1;
        }

        const QList<QColor> backgrounds = {QColor("#ffffff"), QColor("#171a24"), QColor("#e869b3")};
        const QList<QColor> states = {QColor("#3ddc84"), QColor("#facc15"), QColor("#f87171"), QColor("#4fd1ff")};
        QHash<QString, QColor> richCorners;
        for (bool desktop : {false, true}) {
            theme->setProperty("desktopGlass", desktop);
            for (bool rich : {true, false}) {
                theme->setProperty("richGlass", rich);
                for (const auto &background : backgrounds) {
                    window.setColor(background);
                    for (const auto &state : states) {
                        theme->setProperty("stateColor", state);
                        const QString name = QString("%1-%2-bg%3-state%4")
                            .arg(desktop ? "desktop" : "hud", rich ? "rich" : "low",
                                 background.name().mid(1), state.name().mid(1));
                        const QImage image = capture(window, name);
                        check(tint->isVisible(), name + ": tinta sempre presente");
                        check(surface->property("active").toBool() == rich, name + ": effetti solo in qualita' alta");
                        check((surface->property("item").value<QObject *>() != nullptr) == rich,
                              name + ": texture rilasciata nel fallback");
                        checkContrast(image, theme->property("textMuted").value<QColor>(), name);
                        if (!image.isNull()) {
                            const QString key = QString("%1-%2-%3").arg(desktop).arg(background.name(), state.name());
                            const double dpr = image.devicePixelRatio();
                            const QColor corner = image.pixelColor(qRound(41 * dpr), qRound(41 * dpr));
                            if (rich)
                                richCorners.insert(key, corner);
                            else
                                check(richCorners.value(key) == corner, name + ": effetti ritagliati agli angoli");
                        }
                    }
                }
            }
        }

        // Il passaggio a contrasto elevato deve funzionare anche con effetti gia' caricati e accento nero.
        theme->setProperty("richGlass", true);
        settle();
        panel->setProperty("accentColor", QColor(Qt::black));
        theme->setProperty("highContrast", true);
        window.setColor(Qt::white);
        const QImage highContrast = capture(window, "high-contrast");
        check(!surface->property("active").toBool(), "contrasto elevato: niente effetti anche se richGlass=true");
        check(!panel->property("refractionEnabled").toBool(), "contrasto elevato: niente rifrazione");
        check(QQmlProperty::read(border, "border.color").value<QColor>() == QColor(Qt::white),
              "contrasto elevato: bordo bianco prevale sull'accento scuro");
        if (!highContrast.isNull()) {
            const double dpr = highContrast.devicePixelRatio();
            check(highContrast.pixelColor(qRound(200 * dpr), qRound(130 * dpr)) == QColor(Qt::black),
                  "contrasto elevato: fondo nero pieno, non trasparente");
            check(highContrast.pixelColor(qRound(41 * dpr), qRound(41 * dpr)) == QColor(Qt::white),
                  "angolo arrotondato: nessuno strato quadrato fuori dal pannello");
        }

        theme->setProperty("highContrast", false);
        theme->setProperty("desktopGlass", false);
        // Lo ShaderEffectSource e il timer si spengono senza fondo o quando il pannello scompare.
        check(!panel->property("refractionEnabled").toBool(), "nessuna cattura quando manca il backdrop");
        theme->setProperty("backdrop", QVariant::fromValue(window.contentItem()));
        check(panel->property("refractionEnabled").toBool(), "rifrazione attiva solo con un backdrop");
        panel->setVisible(false);
        settle();
        check(!panel->property("refractionEnabled").toBool(), "pannello nascosto: niente cattura del backdrop");
        check(surface->property("item").value<QObject *>() == nullptr, "pannello nascosto: texture liberata");
        theme->setProperty("backdrop", QVariant::fromValue<QQuickItem *>(nullptr));
        panel->setVisible(true);
        settle();
        check(surface->property("item").value<QObject *>() != nullptr, "pannello mostrato: effetti ripristinati");

        // Transizioni di layout: anche raggi maggiori della dimensione e pannelli quasi nulli non rompono Canvas.
        for (const auto &size : {QSizeF(1, 1), QSizeF(80, 320), QSizeF(320, 72), QSizeF(320, 180)}) {
            panel->setSize(size);
            panel->setProperty("radius", 90.0);
            capture(window, QString("resize-%1x%2").arg(size.width()).arg(size.height()));
        }
        object.reset();
        check(panelCount(theme) == 0, "la distruzione rimuove il pannello dal registro click-through");
    }
    qInstallMessageHandler(oldHandler);
    for (const auto &warning : warnings)
        check(false, "warning QML/rendering: " + warning);
    out << (failures == 0 ? "OK" : "FALLITO") << " (" << failures << " errori)\n";
    return failures == 0 ? 0 : 1;
}
