import QtQuick

// Nerd Font icon glyph.
Text {
    property string g: ""
    text: g
    color: th.muted
    font.family: th.fontFamily
    font.pixelSize: th.base + 2
    horizontalAlignment: Text.AlignHCenter
    verticalAlignment: Text.AlignVCenter
    Accessible.ignored: true
}
