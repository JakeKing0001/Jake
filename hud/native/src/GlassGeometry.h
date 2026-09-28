#pragma once

#include <QPoint>
#include <QRect>
#include <QRectF>
#include <QtMath>

// F4.3.1: dove va, in pixel fisici dello schermo, il vetro nativo dietro un pannello. Il pannello e' descritto dal QML
// in coordinate logiche della finestra dell'HUD; la finestra di vetro e' una HWND separata in coordinate fisiche.
// Il vetro e' rientrato di `inset` pixel su ogni lato (e il raggio di conseguenza): la sua regione arrotondata non ha
// antialiasing, il bordo del pannello si' - rientrando, l'orlo disegnato dall'HUD copre sempre il bordo del vetro.
namespace GlassGeometry {

inline QRect toPhysical(QPoint clientOrigin, const QRectF &logical, qreal dpr, int inset) {
    const int left = qRound(logical.x() * dpr) + inset;
    const int top = qRound(logical.y() * dpr) + inset;
    const int right = qRound((logical.x() + logical.width()) * dpr) - inset;
    const int bottom = qRound((logical.y() + logical.height()) * dpr) - inset;
    if (right <= left || bottom <= top)
        return QRect();
    return QRect(QPoint(clientOrigin.x() + left, clientOrigin.y() + top),
                 QPoint(clientOrigin.x() + right - 1, clientOrigin.y() + bottom - 1));
}

inline int physicalRadius(qreal logicalRadius, qreal dpr, int inset) {
    return qMax(0, qRound(logicalRadius * dpr) - inset);
}

// Un pannello che sta comparendo o sparendo ha il vetro solo da meta' dissolvenza in poi: il blur di sistema non si
// puo' sfumare (una finestra a livelli non lo compone), quindi appare dove il pannello e' gia' ben visibile.
inline bool wantsGlass(bool visible, qreal effectiveOpacity, const QRectF &logical) {
    return visible && effectiveOpacity >= 0.5 && logical.width() >= 8 && logical.height() >= 8;
}

} // namespace GlassGeometry
