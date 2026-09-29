#include "SystemWatch.h"

#ifdef Q_OS_WIN
#include <windows.h>
#endif

namespace SystemSettings {

// F4.4.5/F4.7.2: "reduced motion" segue l'impostazione di Windows "Mostra animazioni" (Accessibilita').
bool prefersReducedMotion() {
#ifdef Q_OS_WIN
    BOOL animations = TRUE;
    if (SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION, 0, &animations, 0))
        return animations == FALSE;
#endif
    return false;
}

// F4.7.4: "Dimensioni testo" di Windows (Accessibilita' > Dimensioni testo, 100-225%). Qt non la applica al QML.
double textScale() {
#ifdef Q_OS_WIN
    DWORD value = 0;
    DWORD size = sizeof(value);
    if (RegGetValueW(HKEY_CURRENT_USER, L"Software\\Microsoft\\Accessibility", L"TextScaleFactor",
                     RRF_RT_REG_DWORD, nullptr, &value, &size) == ERROR_SUCCESS && value >= 100 && value <= 225)
        return value / 100.0;
#endif
    return 1.0;
}

// F4.3.5: a batteria (non in carica) orb e vetro scendono in qualita' bassa, salvo scelta esplicita.
bool onBattery() {
#ifdef Q_OS_WIN
    SYSTEM_POWER_STATUS status{};
    if (GetSystemPowerStatus(&status))
        return status.ACLineStatus == 0;
#endif
    return false;
}

// F4.7.3: "Contrasto elevato" di Windows (Accessibilita').
bool highContrast() {
#ifdef Q_OS_WIN
    HIGHCONTRASTW contrast{};
    contrast.cbSize = sizeof(contrast);
    if (SystemParametersInfoW(SPI_GETHIGHCONTRAST, sizeof(contrast), &contrast, 0))
        return (contrast.dwFlags & HCF_HIGHCONTRASTON) != 0;
#endif
    return false;
}

// F4.3.1: "Effetti di trasparenza" di Windows (Personalizzazione > Colori). Spenti -> niente vetro sul desktop.
bool transparencyEffects() {
#ifdef Q_OS_WIN
    DWORD value = 1;
    DWORD size = sizeof(value);
    if (RegGetValueW(HKEY_CURRENT_USER, L"Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize",
                     L"EnableTransparency", RRF_RT_REG_DWORD, nullptr, &value, &size) == ERROR_SUCCESS)
        return value != 0;
    return true;
#else
    return false;
#endif
}

QString quality(const QString &explicitChoice, bool onBattery) {
    if (explicitChoice == QLatin1String("low") || explicitChoice == QLatin1String("high"))
        return explicitChoice;
    return onBattery ? QStringLiteral("low") : QStringLiteral("high");
}

} // namespace SystemSettings

SystemWatch::SystemWatch(QObject *parent) : QObject(parent) {
    poll();
    m_timer.setInterval(PollMs);
    connect(&m_timer, &QTimer::timeout, this, &SystemWatch::poll);
    m_timer.start();
}

void SystemWatch::poll() {
    const bool battery = SystemSettings::onBattery();
    const double scale = SystemSettings::textScale();
    const bool motion = SystemSettings::prefersReducedMotion();
    const bool contrast = SystemSettings::highContrast();
    const bool transparency = SystemSettings::transparencyEffects();
    if (battery == m_onBattery && qFuzzyCompare(scale, m_textScale) && motion == m_reducedMotion
        && contrast == m_highContrast && transparency == m_transparency)
        return;
    m_onBattery = battery;
    m_textScale = scale;
    m_reducedMotion = motion;
    m_highContrast = contrast;
    m_transparency = transparency;
    emit changed();
}
