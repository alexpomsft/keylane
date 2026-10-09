import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Modal choice dialog. ask(title, text, [{id, text, kind}], callback(id))
Popup {
    id: d
    property string heading: ""
    property string message: ""
    property var choices: []
    property var callback: null
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape
    anchors.centerIn: Overlay.overlay
    width: Math.min(th.s(460), Overlay.overlay ? Overlay.overlay.width - th.s(40) : th.s(460))
    padding: th.s(20)
    function ask(h, m, cs, cb) {
        heading = h; message = m; choices = cs; callback = cb
        open()
        Qt.callLater(() => { if (btns.children.length) btns.children[btns.children.length - 2].forceActiveFocus() })
    }
    function finish(id) { const cb = callback; callback = null; close(); if (cb) cb(id) }
    onClosed: if (callback) { const cb = callback; callback = null; cb("cancel") }
    Overlay.modal: Rectangle { color: th.alpha(th.bgSolid, 0.6) }
    background: Rectangle { color: th.bgSolid; border.color: th.border; border.width: th.s(2); radius: th.radius }
    contentItem: ColumnLayout {
        spacing: th.s(12)
        KText { text: d.heading; font.pixelSize: th.base + 4; font.bold: true; Layout.fillWidth: true; wrapMode: Text.Wrap }
        KText { text: d.message; color: th.muted; Layout.fillWidth: true; wrapMode: Text.Wrap; elide: Text.ElideNone; visible: text !== "" }
        RowLayout {
            id: btns
            Layout.topMargin: th.s(6)
            spacing: th.s(8)
            Item { Layout.fillWidth: true }
            Repeater {
                model: d.choices
                KButton {
                    required property var modelData
                    text: modelData.text
                    kind: modelData.kind || "normal"
                    onClicked: d.finish(modelData.id)
                }
            }
        }
    }
}
