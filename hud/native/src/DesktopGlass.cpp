#include "DesktopGlass.h"

#include "GlassGeometry.h"
#include "NativeGlass.h"

#include <QVariantMap>

#ifdef Q_OS_WIN
#include <windows.h>
#endif

namespace {
// un pixel dentro l'orlo antialiasato del pannello, che cosi' copre sempre il bordo della lastra
constexpr int GlassInset = 1;
constexpr unsigned RestackEverySyncs = 16;  // con il sync a 60 ms del QML
} // namespace

DesktopGlass::DesktopGlass(QObject *parent) : QObject(parent) {}

DesktopGlass::~DesktopGlass() = default;

bool DesktopGlass::supported() const {
    return NativeGlassPane::available();
}

QString DesktopGlass::mode() const {
    return supported() ? QStringLiteral("backdrop") : QStringLiteral("none");
}

bool DesktopGlass::transparencyEffectsEnabled() const {
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

NativeGlassPane *DesktopGlass::paneAt(size_t index) {
    if (index >= m_panes.size())
        m_panes.resize(index + 1);
    Pane &pane = m_panes[index];
    if (!pane.glass)
        pane.glass = std::make_unique<NativeGlassPane>();
    return pane.glass->valid() ? pane.glass.get() : nullptr;
}

void DesktopGlass::hidePane(Pane &pane) {
    if (pane.glass && pane.shown)
        pane.glass->hide();
    pane.shown = false;
}

void DesktopGlass::sync(QQuickWindow *window, const QVariantList &regions) {
#ifdef Q_OS_WIN
    if (!window || !window->isVisible() || !supported()) {
        hideAll();
        return;
    }
    HWND overlay = reinterpret_cast<HWND>(window->winId());
    POINT origin{0, 0};
    ClientToScreen(overlay, &origin);
    const qreal dpr = window->devicePixelRatio();
    // un'altra finestra "sempre sopra" potrebbe infilarsi fra overlay e lastre: lo z-order si riafferma ogni ~1 s
    const bool restack = (m_syncs++ % RestackEverySyncs) == 0;
    size_t used = 0;
    for (const QVariant &value : regions) {
        const QVariantMap region = value.toMap();
        const QRectF logical(region.value(QStringLiteral("x")).toReal(), region.value(QStringLiteral("y")).toReal(),
                             region.value(QStringLiteral("width")).toReal(),
                             region.value(QStringLiteral("height")).toReal());
        if (!GlassGeometry::wantsGlass(region.value(QStringLiteral("visible"), true).toBool(),
                                       region.value(QStringLiteral("opacity"), 1.0).toReal(), logical))
            continue;
        const QRect rect = GlassGeometry::toPhysical(QPoint(origin.x, origin.y), logical, dpr, GlassInset);
        if (rect.isEmpty())
            continue;
        NativeGlassPane *glass = paneAt(used);
        if (!glass)
            break;
        const int radius = GlassGeometry::physicalRadius(region.value(QStringLiteral("radius")).toReal(), dpr,
                                                         GlassInset);
        glass->place(overlay, rect.x(), rect.y(), rect.width(), rect.height(), float(radius), restack);
        m_panes[used].shown = true;
        ++used;
    }
    for (size_t i = used; i < m_panes.size(); ++i)
        hidePane(m_panes[i]);
#else
    Q_UNUSED(window);
    Q_UNUSED(regions);
#endif
}

void DesktopGlass::hideAll() {
    for (Pane &pane : m_panes)
        hidePane(pane);
}

int DesktopGlass::visibleCount() const {
    int count = 0;
    for (const Pane &pane : m_panes)
        count += pane.shown ? 1 : 0;
    return count;
}
