import QtQuick
import QtQuick.Window
import QtQuick.Layouts
import org.kde.layershell 1.0 as LayerShell

// Non-focusable confirmation after a copy.
Window {
    id: w
    visible: false
    color: "transparent"
    width: box.width
    height: box.height
    LayerShell.Window.scope: "keylane-toast"
    LayerShell.Window.layer: LayerShell.Window.LayerOverlay
    LayerShell.Window.anchors: LayerShell.Window.AnchorBottom
    LayerShell.Window.margins.bottom: th.s(48)
    LayerShell.Window.keyboardInteractivity: LayerShell.Window.KeyboardInteractivityNone
    LayerShell.Window.exclusionZone: -1
    LayerShell.Window.screenConfiguration: LayerShell.Window.ScreenFromCompositor
    property string headline: ""
    property string detail: ""
    function flash(h, d) {
        headline = h; detail = d
        visible = true
        box.opacity = 1
        hide.restart()
    }
    Timer { id: hide; interval: 1600; onTriggered: fade.start() }
    NumberAnimation { id: fade; target: box; property: "opacity"; to: 0; duration: 180; onFinished: w.visible = false }
    Rectangle {
        id: box
        width: row.implicitWidth + th.s(28)
        height: row.implicitHeight + th.s(18)
        radius: th.radius
        color: th.bgSolid
        border.color: th.border
        border.width: th.s(2)
        RowLayout {
            id: row
            anchors.centerIn: parent
            spacing: th.s(10)
            Glyph { g: "\uf00c"; color: th.success; font.pixelSize: th.base + 2 }
            KText { text: w.headline; font.pixelSize: th.base + 1 }
            KText { text: w.detail; color: th.muted; font.pixelSize: th.base - 1 }
        }
    }
}
