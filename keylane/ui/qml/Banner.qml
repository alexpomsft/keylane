import QtQuick
import QtQuick.Layouts

// Inline status message. kind: error | warning | info | success
Rectangle {
    id: bn
    property string kind: "error"
    property string text: ""
    readonly property color tone: kind === "error" ? th.danger : kind === "warning" ? th.warning : kind === "success" ? th.success : th.accent
    visible: text !== ""
    implicitHeight: visible ? row.implicitHeight + th.s(12) : 0
    radius: th.radius
    color: th.alpha(tone, 0.10)
    border.color: th.alpha(tone, 0.45)
    border.width: 1
    Accessible.role: Accessible.AlertMessage
    Accessible.name: text
    RowLayout {
        id: row
        anchors.fill: parent
        anchors.margins: th.s(6)
        anchors.leftMargin: th.s(10)
        spacing: th.s(8)
        Glyph {
            g: bn.kind === "error" ? "\uf071" : bn.kind === "warning" ? "\uf071" : bn.kind === "success" ? "\uf00c" : "\uf05a"
            color: bn.tone
            font.pixelSize: th.base
        }
        KText {
            Layout.fillWidth: true
            text: bn.text
            color: th.fg
            wrapMode: Text.Wrap
            elide: Text.ElideNone
            font.pixelSize: th.base
        }
    }
}
