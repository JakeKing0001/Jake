#pragma once

#include <QObject>
#include <QQmlEngine>
#include <QString>
#include <QTimer>

// Impostazioni di Windows che l'HUD deve seguire (F4.3.5, F4.4.5, F4.7.2-F4.7.4). Prima erano lette una volta sola da
// main.cpp: staccare l'alimentatore, cambiare "Dimensioni testo", "Effetti animazione", "Effetti di trasparenza" o il
// contrasto elevato non cambiava nulla fino al riavvio dell'HUD. Qui si rileggono ogni pochi secondi (chiamate Win32
// da microsecondi) e ogni cambio arriva al QML come proprieta'.
namespace SystemSettings {
bool prefersReducedMotion();
double textScale();         // 1.0 - 2.25
bool onBattery();
bool highContrast();
bool transparencyEffects();

// La qualita' dell'orb e del vetro: una scelta esplicita (JAKE_HUD_QUALITY) vince sempre; altrimenti bassa a batteria.
QString quality(const QString &explicitChoice, bool onBattery);
} // namespace SystemSettings

class SystemWatch : public QObject {
    Q_OBJECT
    QML_ELEMENT
    Q_PROPERTY(bool onBattery READ onBattery NOTIFY changed)
    Q_PROPERTY(double textScale READ textScale NOTIFY changed)
    Q_PROPERTY(bool reducedMotion READ reducedMotion NOTIFY changed)
    Q_PROPERTY(bool highContrast READ highContrast NOTIFY changed)
    Q_PROPERTY(bool transparencyEffects READ transparencyEffects NOTIFY changed)

public:
    explicit SystemWatch(QObject *parent = nullptr);

    bool onBattery() const { return m_onBattery; }
    double textScale() const { return m_textScale; }
    bool reducedMotion() const { return m_reducedMotion; }
    bool highContrast() const { return m_highContrast; }
    bool transparencyEffects() const { return m_transparency; }

    static constexpr int PollMs = 3000;

signals:
    void changed();

private:
    void poll();

    QTimer m_timer;
    bool m_onBattery = false;
    double m_textScale = 1.0;
    bool m_reducedMotion = false;
    bool m_highContrast = false;
    bool m_transparency = true;
};
