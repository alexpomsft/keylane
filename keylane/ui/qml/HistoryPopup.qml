import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Popup {
    id: hp
    property string entryId: ""
    property var items: []
    property var viewing: null
    property int viewingIndex: -1
    property bool reveal: false
    property string error: ""
    signal restored()
    modal: true
    focus: true
    anchors.centerIn: Overlay.overlay
    width: Math.min(th.s(720), Overlay.overlay ? Overlay.overlay.width - th.s(40) : th.s(720))
    height: Math.min(th.s(520), Overlay.overlay ? Overlay.overlay.height - th.s(40) : th.s(520))
    padding: th.s(18)
    Overlay.modal: Rectangle { color: th.alpha(th.bgSolid, 0.6) }
    background: Rectangle { color: th.bgSolid; border.color: th.border; border.width: th.s(2); radius: th.radius }
    function openFor(id) {
        entryId = id; viewing = null; viewingIndex = -1; reveal = false; error = ""; hl.currentIndex = -1
        kl.history(id, function (r) {
            if (!r.ok) { hp.error = r.message; return }
            hp.items = r.items.slice().reverse()
            if (hp.items.length) hl.currentIndex = 0
        })
        open()
        hl.forceActiveFocus()
    }
    onClosed: { viewing = null; reveal = false }
    contentItem: ColumnLayout {
        spacing: th.s(10)
        RowLayout {
            KText { text: "History"; font.pixelSize: th.base + 4; font.bold: true }
            Item { Layout.fillWidth: true }
            IconButton { glyph: "\uf00d"; tip: "Close"; onClicked: hp.close() }
        }
        Banner { Layout.fillWidth: true; text: hp.error }
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: th.s(12)
            ListView {
                id: hl
                Layout.preferredWidth: Math.min(th.s(280), hp.availableWidth * 0.38)
                Layout.fillHeight: true
                clip: true
                model: hp.items
                focus: true
                keyNavigationEnabled: true
                Component.onCompleted: currentIndex = -1
                Keys.onPressed: (e) => {
                    const ctrl = e.modifiers & Qt.ControlModifier
                    if (ctrl && e.key === Qt.Key_H) { hp.reveal = !hp.reveal; e.accepted = true }
                    else if (ctrl && (e.key === Qt.Key_Return || e.key === Qt.Key_Enter)) { hp.restore(); e.accepted = true }
                    else if (ctrl && e.key === Qt.Key_C) { hp.copyPassword(); e.accepted = true }
                }
                onCurrentIndexChanged: if (currentIndex >= 0) hp.view(hp.items[currentIndex].index)
                delegate: Rectangle {
                    required property var modelData
                    required property int index
                    width: ListView.view.width
                    height: th.s(48)
                    radius: th.radius
                    color: ListView.isCurrentItem ? th.sel : "transparent"
                    border.width: ListView.isCurrentItem ? 1 : 0
                    border.color: th.selBorder
                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: th.s(6); spacing: 0
                        KText { Layout.fillWidth: true; elide: Text.ElideRight; text: Qt.formatDateTime(new Date(modelData.modified), "yyyy-MM-dd  HH:mm"); color: ListView.isCurrentItem ? th.selFg : th.fg }
                        KText { Layout.fillWidth: true; elide: Text.ElideRight; text: modelData.changed ? "changed: " + modelData.changed : modelData.title; color: th.muted; font.pixelSize: th.base - 1 }
                    }
                    MouseArea { anchors.fill: parent; onClicked: hl.currentIndex = index }
                }
            }
            Separator { Layout.fillHeight: true; implicitWidth: 1 }
            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: th.s(8)
                KText { visible: !hp.viewing; text: "Select a version."; color: th.muted }
                GridLayout {
                    visible: !!hp.viewing
                    columns: 2
                    columnSpacing: th.s(12)
                    rowSpacing: th.s(6)
                    Layout.fillWidth: true
                    KText { text: "Title"; color: th.muted }
                    KText { Layout.fillWidth: true; Layout.preferredWidth: 1; elide: Text.ElideRight; text: hp.viewing ? hp.viewing.title : "" }
                    KText { text: "Username"; color: th.muted }
                    KText { Layout.fillWidth: true; Layout.preferredWidth: 1; elide: Text.ElideRight; text: hp.viewing ? hp.viewing.username : "" }
                    KText { text: "Password"; color: th.muted }
                    KText { Layout.fillWidth: true; Layout.preferredWidth: 1; elide: Text.ElideRight; text: hp.viewing ? (hp.reveal ? hp.viewing.password : (hp.viewing.password ? "••••••••••••" : "")) : "" }
                    KText { text: "URL"; color: th.muted }
                    KText { Layout.fillWidth: true; Layout.preferredWidth: 1; elide: Text.ElideRight; text: hp.viewing ? hp.viewing.url : "" }
                    KText { text: "Notes"; color: th.muted }
                    KText { Layout.fillWidth: true; Layout.preferredWidth: 1; text: hp.viewing ? hp.viewing.notes : ""; maximumLineCount: 4; wrapMode: Text.Wrap }
                }
                Item { Layout.fillHeight: true }
                Flow {
                    visible: !!hp.viewing
                    Layout.fillWidth: true
                    spacing: th.s(8)
                    layoutDirection: Qt.RightToLeft
                    KButton {
                        text: "Restore"
                        hint: "Ctrl+Enter"
                        kind: "primary"
                        onClicked: hp.restore()
                    }
                    KButton { text: "Copy password"; kind: "ghost"; onClicked: hp.copyPassword() }
                    KButton { text: hp.reveal ? "Hide" : "Reveal"; hint: "Ctrl+H"; kind: "ghost"; onClicked: hp.reveal = !hp.reveal }
                }
            }
        }
    }
    function copyPassword() { kl.copyHistoryField(entryId, viewingIndex, "Password", function (r) { hp.error = r.ok ? "" : r.message }) }
    function restore() {
        if (viewingIndex < 0) return
        kl.restoreHistory(entryId, viewingIndex, function (r) { if (r.ok) { hp.close(); hp.restored() } else hp.error = r.message })
    }
    function view(idx) {
        viewingIndex = idx; reveal = false
        kl.readHistory(entryId, idx, function (r) { if (r.ok && hp.viewingIndex === idx) hp.viewing = r.entry; else if (!r.ok) hp.error = r.message })
    }
}
