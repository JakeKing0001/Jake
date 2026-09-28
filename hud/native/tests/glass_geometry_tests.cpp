// F4.3.1: il vetro nativo (DesktopGlass) deve cadere esattamente sotto il pannello che il QML disegna, a qualunque
// scala di Windows, e sempre un pixel dentro l'orlo antialiasato del pannello. Esce con 0 solo se ogni caso torna.
#include <QTextStream>

#include "GlassGeometry.h"

namespace {
int failures = 0;
QTextStream out(stdout);

void check(bool ok, const char *what) {
    if (!ok) {
        out << "FAIL: " << what << "\n";
        ++failures;
    }
}
} // namespace

int main() {
    // 100%: un pannello a (10, 20) di 300x200 in una finestra con l'area client a (500, 100) sullo schermo
    const QRect at100 = GlassGeometry::toPhysical(QPoint(500, 100), QRectF(10, 20, 300, 200), 1.0, 1);
    check(at100 == QRect(511, 121, 298, 198), "100%: posizione assoluta, rientro di un pixel per lato");

    // 150%: tutto in pixel fisici, arrotondato come Qt arrotonda il pannello
    const QRect at150 = GlassGeometry::toPhysical(QPoint(0, 0), QRectF(10, 20, 300, 200), 1.5, 1);
    check(at150 == QRect(16, 31, 448, 298), "150%: coordinate e dimensioni scalate");

    // 125% con coordinate frazionarie (un pannello ancorato al centro): nessun buco di un pixel fra vetro e orlo
    const QRect at125 = GlassGeometry::toPhysical(QPoint(0, 0), QRectF(381.5, 120.25, 318, 410), 1.25, 1);
    check(at125.left() == qRound(381.5 * 1.25) + 1 && at125.right() == qRound((381.5 + 318) * 1.25) - 2,
          "125%: bordi sinistro e destro dagli stessi arrotondamenti del pannello");

    // pannello degenere (in apertura, largo 1-2 px): nessuna finestra
    check(GlassGeometry::toPhysical(QPoint(0, 0), QRectF(0, 0, 1.5, 100), 1.0, 1).isEmpty(),
          "pannello piu' stretto del rientro: nessun vetro");

    // raggio: stesso rientro, mai negativo
    check(GlassGeometry::physicalRadius(22, 1.0, 1) == 21, "raggio a 100%");
    check(GlassGeometry::physicalRadius(22, 1.5, 1) == 32, "raggio a 150%");
    check(GlassGeometry::physicalRadius(0, 2.0, 1) == 0, "raggio nullo resta nullo");

    // dissolvenza: il vetro compare solo da meta' in poi, mai per pannelli nascosti o minuscoli
    check(!GlassGeometry::wantsGlass(true, 0.3, QRectF(0, 0, 300, 200)), "pannello a inizio dissolvenza: niente vetro");
    check(GlassGeometry::wantsGlass(true, 0.5, QRectF(0, 0, 300, 200)), "pannello a meta' dissolvenza: vetro");
    check(!GlassGeometry::wantsGlass(false, 1.0, QRectF(0, 0, 300, 200)), "pannello nascosto: niente vetro");
    check(!GlassGeometry::wantsGlass(true, 1.0, QRectF(0, 0, 4, 200)), "pannello troppo stretto: niente vetro");

    out << (failures == 0 ? "OK" : "FALLITO") << " (" << failures << " errori)\n";
    return failures == 0 ? 0 : 1;
}
