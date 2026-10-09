import QtQuick
import QtQuick.Controls.Basic

Button {
    id: b
    property string kind: "normal"   // normal | primary | danger | ghost
    property string glyph: ""
    property string hint: ""
    property bool iconOnly: false
    hoverEnabled: true
    ToolTip.visible: iconOnly && hovered
    ToolTip.text: text + (hint ? "  " + hint : "")
    ToolTip.delay: 400
    focusPolicy: Qt.StrongFocus
    padding: th.s(8)
    leftPadding: th.s(12)
    rightPadding: th.s(12)
    font.family: th.fontFamily
    font.pixelSize: th.base
    Accessible.name: text + (hint ? " (" + hint + ")" : "")
    Keys.onReturnPressed: clicked()
    Keys.onEnterPressed: clicked()
    contentItem: Row {
        spacing: th.s(6)
        Glyph {
            visible: b.glyph !== ""
            g: b.glyph
            color: lbl.color
            font.pixelSize: th.base
            anchors.verticalCenter: parent.verticalCenter
        }
        KText {
            id: lbl
            visible: !b.iconOnly && b.text !== ""
            text: b.text
            font.pixelSize: th.base
            anchors.verticalCenter: parent.verticalCenter
            color: !b.enabled ? th.muted
                 : b.kind === "primary" ? th.bgSolid
                 : b.kind === "danger" ? th.danger
                 : th.fg
        }
        KText {
            visible: b.hint !== "" && !b.iconOnly
            text: b.hint
            font.pixelSize: th.base - 2
            anchors.verticalCenter: parent.verticalCenter
            color: b.kind === "primary" && b.enabled ? th.alpha(th.bgSolid, 0.7) : th.muted
        }
    }
    background: Rectangle {
        implicitHeight: th.s(30)
        radius: th.radius
        color: b.kind === "primary" && !b.enabled ? th.faint
             : b.kind === "primary" ? (b.down ? Qt.darker(th.accent, 1.2) : b.hovered ? Qt.lighter(th.accent, 1.08) : th.accent)
             : b.down ? th.alpha(th.fg, 0.16) : (b.hovered || b.activeFocus) ? th.hover
             : b.kind === "ghost" ? "transparent" : th.faint
        border.width: b.activeFocus ? 2 : 1
        border.color: b.activeFocus ? (b.kind === "primary" ? th.fg : th.accent)
                    : b.kind === "danger" ? th.alpha(th.danger, 0.5)
                    : b.kind === "ghost" ? "transparent"
                    : b.kind === "primary" ? (b.enabled ? th.accent : th.hairline) : th.hairline
        opacity: b.enabled || b.kind === "primary" ? 1 : 0.5
        Behavior on color { ColorAnimation { duration: 90 } }
    }
}
