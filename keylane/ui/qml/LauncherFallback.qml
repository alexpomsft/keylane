import QtQuick
import QtQuick.Window

// Used only when layer-shell-qt is unavailable: a frameless dialog window.
Window {
    id: w
    visible: false
    color: "transparent"
    title: "Keylane"
    flags: Qt.Dialog | Qt.FramelessWindowHint
    width: card.width
    height: card.height
    function open(mode, text) { visible = true; requestActivate(); card.open(mode, text) }
    function close_() { card.reset(); visible = false }
    LauncherCard { id: card; focus: true }
}
