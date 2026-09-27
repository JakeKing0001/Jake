pragma Singleton
import QtQuick

// Un solo linguaggio visivo per tutto l'HUD: stesso vetro, stesso gradiente, stessi raggi e colori in
// ogni pannello (mai un aspetto diverso per elemento). Il vetro vero con blur di sistema e' F4.3: qui il
// "vetro" e' un riempimento scuro semitrasparente con gradiente e bordo luminoso, leggibile anche sopra
// un desktop chiaro o pieno di testo.
QtObject {
    // Riempimento pieno finche' non c'e' il blur di sistema (F4.3): con un vetro semitrasparente il testo delle
    // finestre sotto trapelava e rendeva i pannelli illeggibili (screenshot reale del 26/09/2026).
    readonly property color glassTop: "#1b1e27"
    readonly property color glassBottom: "#111319"
    readonly property color glassBorder: Qt.rgba(1, 1, 1, 0.10)
    readonly property color glassHighlight: Qt.rgba(1, 1, 1, 0.06)
    readonly property real radius: 16
    readonly property real smallRadius: 10

    readonly property color text: "#f3f4f6"
    readonly property color textMuted: "#a1a7b3"
    readonly property color textFaint: "#6b7280"
    readonly property color accent: "#4fd1ff"
    readonly property color ok: "#3ddc84"
    readonly property color warn: "#facc15"
    readonly property color attention: "#fb923c"
    readonly property color danger: "#f87171"
    readonly property color focusRing: "#93c5fd"

    readonly property color control: "#262b36"
    readonly property color controlHover: "#323846"
    readonly property int fontSmall: 12
    readonly property int fontBody: 14
}
