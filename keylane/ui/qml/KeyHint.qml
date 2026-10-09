import QtQuick
import QtQuick.Layouts

// Footer key hint: [keycap] label
RowLayout {
    property string keys: ""
    property string label: ""
    property bool active: true
    spacing: th.s(5)
    opacity: active ? 1 : 0.4
    Accessible.role: Accessible.StaticText
    Accessible.name: keys + " " + label
    Rectangle {
        implicitWidth: kc.implicitWidth + th.s(10)
        implicitHeight: kc.implicitHeight + th.s(4)
        radius: Math.min(th.radius, 4)
        color: th.faint
        border.color: th.hairline
        border.width: 1
        KText {
            id: kc
            anchors.centerIn: parent
            text: keys
            font.pixelSize: th.base - 1
            color: th.fg
        }
    }
    KText {
        text: label
        font.pixelSize: th.base - 1
        color: th.muted
    }
}
