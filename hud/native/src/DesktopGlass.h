#pragma once

#include <QObject>
#include <QQmlEngine>
#include <QQuickWindow>
#include <QVariantList>
#include <memory>
#include <vector>

class NativeGlassPane;

// F4.3.1 ("blur/composition nativi con effetto sobrio"): vetro vero sul desktop dietro ai pannelli.
//
// Perche' finestre separate. L'overlay dell'HUD e' una finestra Qt trasparente, quindi WS_EX_LAYERED (exstyle letto il
// 27/09/2026: 0x080800a8): DWM non compone nessun backdrop dietro una finestra a livelli - l'esperimento con
// SetWindowRgn + accent sull'overlay dava una finestra tutta nera. Qui ogni pannello visibile ha dietro di se' una
// lastra nativa (NativeGlass.h: HWND senza bitmap di redirezione, backdrop sfocato della composizione, angoli
// arrotondati antialiasati al raggio del pannello). L'overlay resta com'e' - orb, contenuto, input, click-through - e
// disegna sopra la lastra la tinta, l'orlo e il testo. Le lastre:
// - stanno subito sotto l'overlay nello z-order, senza attivarsi, fuori da Alt-Tab e dalla barra delle applicazioni;
// - coincidono col pannello (che riceve i click) e non intercettano mai l'input (HTTRANSPARENT);
// - si nascondono con l'HUD e con il pannello a meta' dissolvenza; senza Windows 11, con gli effetti di trasparenza
//   spenti o col contrasto elevato non esistono proprio: resta il vetro disegnato dall'HUD (GlassPanel).
// Costo: il blur lo compone DWM sulla GPU, fuori dal render loop di Qt; nessuna cattura dello schermo.
class DesktopGlass : public QObject {
    Q_OBJECT
    QML_ELEMENT
    Q_PROPERTY(bool supported READ supported CONSTANT)
    Q_PROPERTY(QString mode READ mode CONSTANT)

public:
    explicit DesktopGlass(QObject *parent = nullptr);
    ~DesktopGlass() override;

    bool supported() const;
    // "backdrop" (lastre native) o "none"
    QString mode() const;
    // "Effetti di trasparenza" di Windows (Personalizzazione > Colori). Spenti -> niente vetro sul desktop.
    Q_INVOKABLE bool transparencyEffectsEnabled() const;

    // regions: una lista di {x, y, width, height, radius, visible, opacity} in coordinate logiche della finestra, un
    // elemento per pannello (opacity = quella effettiva, antenati compresi). Il vetro va solo ai pannelli visibili da
    // meta' dissolvenza in poi (GlassGeometry::wantsGlass); le lastre in piu' si nascondono.
    Q_INVOKABLE void sync(QQuickWindow *window, const QVariantList &regions);
    Q_INVOKABLE void hideAll();
    // quante lastre sono visibili adesso (diagnostica e verifica a schermo)
    Q_INVOKABLE int visibleCount() const;

private:
    struct Pane {
        std::unique_ptr<NativeGlassPane> glass;
        bool shown = false;
    };
    NativeGlassPane *paneAt(size_t index);
    void hidePane(Pane &pane);
    std::vector<Pane> m_panes;
    unsigned m_syncs = 0;
};
