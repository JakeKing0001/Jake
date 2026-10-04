// Presenza ambientale dell'HUD (qml/Presence.qml): mini/nascosto/grande, mini automatico, notifiche, conferme,
// geometria dell'angolo e movimento ridotto. Carica lo STESSO file QML dell'app, senza finestra.
// Uso: JakeHudPresenceTests <percorso di qml/Presence.qml>
#include <QGuiApplication>
#include <QJSValue>
#include <QPointF>
#include <QQmlComponent>
#include <QQmlEngine>
#include <QRectF>
#include <QTextStream>
#include <QVariant>

#include <memory>

namespace {
int failures = 0;
QTextStream out(stdout);

void check(bool ok, const QString &what) {
    if (!ok) {
        out << "FAIL: " << what << "\n";
        ++failures;
    }
}

QVariant call(QObject *object, const char *method, const QVariant &a = {}, const QVariant &b = {}) {
    QVariant result;
    if (!b.isValid())
        QMetaObject::invokeMethod(object, method, Q_RETURN_ARG(QVariant, result), Q_ARG(QVariant, a));
    else
        QMetaObject::invokeMethod(object, method, Q_RETURN_ARG(QVariant, result), Q_ARG(QVariant, a), Q_ARG(QVariant, b));
    return result;
}

QVariant call0(QObject *object, const char *method) {
    QVariant result;
    QMetaObject::invokeMethod(object, method, Q_RETURN_ARG(QVariant, result));
    return result;
}

// Jake fermo da abbastanza: ogni test ne cambia un solo aspetto
QVariantMap idle() {
    return {{"visible", true}, {"state", "IDLE"}, {"recentlyActive", false}, {"typing", false},
            {"confirmationPending", false}, {"pointerOverPanel", false}, {"holdingError", false}, {"toastShown", false}};
}

bool autoMiniDue(QObject *presence, const QVariantMap &inputs) {
    return call(presence, "autoMiniDue", QVariant::fromValue(inputs)).toBool();
}
} // namespace

int main(int argc, char **argv) {
    QGuiApplication app(argc, argv);
    if (argc < 2) {
        out << "uso: JakeHudPresenceTests <Presence.qml>\n";
        return 2;
    }
    QQmlEngine engine;
    QQmlComponent component(&engine, QUrl::fromLocalFile(QString::fromLocal8Bit(argv[1])));
    std::unique_ptr<QObject> presence(component.create());
    if (!presence) {
        out << "FAIL: Presence.qml non caricato: " << component.errorString() << "\n";
        return 1;
    }

    // 1. grande -> mini manuale: finestra piccola, nell'angolo ALTO DESTRO dell'area utile
    check(presence->property("mode").toString() == "expanded", "all'avvio si parte grandi (mai nascosti)");
    check(call(presence.get(), "request", "mini", "manual").toBool(), "mini manuale cambia modo");
    check(presence->property("mini").toBool() && presence->property("reason").toString() == "manual", "mini + causa");
    const int size = presence->property("windowSize").toInt();
    check(size >= 130 && size <= 170, "finestra del mini fra 130 e 170 px");
    check(presence->property("orbSize").toInt() * 3 / 2 <= size, "la scena 3D (1,5 x orb) sta nella finestra del mini");

    // 10. monitor non primario a destra, con barra delle applicazioni in alto: area utile, non lo schermo intero
    const QPointF corner = call(presence.get(), "miniPosition", QRectF(1920, 40, 2560, 1400), size).toPointF();
    check(corner == QPointF(1920 + 2560 - size - 16, 40 + 16), "mini in alto a destra dell'area utile del monitor giusto");
    const QPointF left = call(presence.get(), "miniPosition", QRectF(-1280, 0, 1280, 984), size).toPointF();
    check(left.x() + size <= 0 && left.x() > -1280, "monitor a sinistra (coordinate negative): resta su quel monitor");

    // 8. notifica normale in mini: niente toast, niente ritorno grande, solo il segnale sull'orb
    check(!call0(presence.get(), "notificationShowsToast").toBool(), "in mini nessun toast");
    check(call0(presence.get(), "notificationMarksOrb").toBool(), "in mini l'orb segnala la notifica");
    check(call0(presence.get(), "confirmationRestores").toBool(), "una conferma da mini riporta grande");

    // nascosto: una notifica non lo riapre, una conferma nemmeno (scelta esplicita dell'utente)
    call(presence.get(), "request", "hidden", "manual");
    check(presence->property("hidden").toBool(), "nascosto");
    check(!call0(presence.get(), "notificationShowsToast").toBool() && !call0(presence.get(), "notificationMarksOrb").toBool(),
          "da nascosto una notifica non mostra nulla");
    check(!call0(presence.get(), "confirmationRestores").toBool(), "da nascosto una conferma non riapre");

    // 4. la wake word riporta grande; un valore sconosciuto vale grande
    check(call(presence.get(), "request", "expanded", "wake").toBool() && presence->property("expanded").toBool(),
          "nascosto -> wake -> grande");
    check(!call(presence.get(), "request", "expanded", "wake").toBool(), "gia' grande: nessun cambio (niente transizione)");
    call(presence.get(), "request", "mini", "manual");
    call(presence.get(), "request", "gigante", "x");
    check(presence->property("expanded").toBool(), "modo sconosciuto = grande");

    // 5. mini automatico solo con Jake davvero fermo
    check(autoMiniDue(presence.get(), idle()), "IDLE inattivo -> mini automatico");
    // 6. mai durante stati attivi (WAITING = conferma in attesa nel reducer)
    for (const char *state : {"LISTENING", "TRANSCRIBING", "THINKING", "EXECUTING", "WAITING", "SPEAKING", "ERROR"}) {
        QVariantMap busy = idle();
        busy["state"] = QString::fromLatin1(state);
        check(!autoMiniDue(presence.get(), busy), QStringLiteral("nessun mini automatico in %1").arg(QLatin1String(state)));
    }
    // 7. mai con una conferma, chi scrive, il cursore su un pannello, un errore o un avviso in vista, o attivita' recente
    for (const char *key : {"recentlyActive", "typing", "confirmationPending", "pointerOverPanel", "holdingError", "toastShown"}) {
        QVariantMap blocked = idle();
        blocked[QString::fromLatin1(key)] = true;
        check(!autoMiniDue(presence.get(), blocked), QStringLiteral("nessun mini automatico con %1").arg(QLatin1String(key)));
    }
    QVariantMap invisible = idle();
    invisible["visible"] = false;
    check(!autoMiniDue(presence.get(), invisible), "finestra nascosta: nessun mini automatico");
    call(presence.get(), "request", "mini", "auto_idle");
    check(!autoMiniDue(presence.get(), idle()), "gia' mini: non si ripete");
    check(presence->property("reason").toString() == "auto_idle", "causa del mini automatico");
    check(presence->property("idleMs").toInt() >= 20000 && presence->property("idleMs").toInt() <= 30000,
          "inattivita' fra 20 e 30 s");

    // 11. movimento ridotto: transizione immediata
    check(presence->property("transitionMs").toInt() >= 180 && presence->property("transitionMs").toInt() <= 300,
          "transizione 180-300 ms");
    presence->setProperty("reducedMotion", true);
    check(presence->property("transitionMs").toInt() == 0, "movimento ridotto: transizione immediata");

    if (failures == 0)
        out << "OK presence\n";
    return failures == 0 ? 0 : 1;
}
