import QtQuick
import QtQuick.Controls.Basic

Switch {
    id: s
    font.family: th.fontFamily
    font.pixelSize: th.base + 1
    focusPolicy: Qt.StrongFocus
    Accessible.name: text
    indicator: Rectangle {
        x: s.leftPadding
        y: (s.height - height) / 2
        implicitWidth: th.s(32)
        implicitHeight: th.s(18)
        radius: height / 2
        color: s.checked ? th.accent : th.faint
        border.color: s.activeFocus ? th.accent : th.hairline
        border.width: s.activeFocus ? 2 : 1
        Rectangle {
            width: parent.height - th.s(6)
            height: width
            radius: width / 2
            y: th.s(3)
            x: s.checked ? parent.width - width - th.s(3) : th.s(3)
            color: s.checked ? th.bgSolid : th.muted
            Behavior on x { NumberAnimation { duration: 110; easing.type: Easing.OutCubic } }
        }
    }
    contentItem: KText {
        text: s.text
        leftPadding: s.indicator.width + th.s(10)
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideNone
        wrapMode: Text.Wrap
    }
}
