import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Item {
    id: tp
    property var confirmDialog: null
    signal message(string kind, string text)
    function focusList() { if (tl.count && tl.currentIndex < 0) tl.currentIndex = 0; tl.forceActiveFocus() }
    readonly property var trashedGroups: kl.groups.filter(g => g.inTrash && !g.isTrash)
    function restore(id, title) { kl.restore(id, function (r) { tp.message(r.ok ? "success" : "error", r.ok ? "Restored “" + title + "”." : r.message) }) }
    function destroy_(id, title) {
        confirmDialog.ask("Delete “" + title + "” permanently?", "This removes it from the vault. Backups made before now still contain it.",
            [{ id: "cancel", text: "Cancel", kind: "ghost" }, { id: "ok", text: "Delete permanently", kind: "danger" }],
            function (c) { if (c === "ok") kl.deletePermanently(id, function (r) { if (!r.ok) tp.message("error", r.message) }) })
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: th.s(18)
        spacing: th.s(12)
        RowLayout {
            KText { text: "Trash"; font.pixelSize: th.base + 6; font.bold: true }
            Item { Layout.fillWidth: true }
            KButton {
                text: "Empty trash"; kind: "danger"; glyph: "\uf1f8"
                enabled: tl.count > 0 || tp.trashedGroups.length > 0
                onClicked: tp.confirmDialog.ask("Empty the trash?", "Everything in the trash is deleted permanently.",
                    [{ id: "cancel", text: "Cancel", kind: "ghost" }, { id: "ok", text: "Empty trash", kind: "danger" }],
                    function (c) { if (c === "ok") kl.emptyTrash(function (r) { tp.message(r.ok ? "success" : "error", r.ok ? "Trash emptied." : r.message) }) })
            }
        }
        KText { text: "Trashed items stay in the vault (and sync) until deleted. Restore puts them back where they were."; color: th.muted; Layout.fillWidth: true; wrapMode: Text.Wrap }
        Repeater {
            model: tp.trashedGroups
            RowLayout {
                required property var modelData
                Layout.fillWidth: true
                Glyph { g: "\uf07b"; color: th.muted }
                KText { text: modelData.name; Layout.fillWidth: true }
                KButton { text: "Restore"; kind: "ghost"; onClicked: tp.restore(modelData.id, modelData.name) }
                KButton { text: "Delete"; kind: "ghost"; onClicked: tp.destroy_(modelData.id, modelData.name) }
            }
        }
        ListView {
            id: tl
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            model: kl.trashModel
            activeFocusOnTab: true
            keyNavigationEnabled: true
            highlightMoveDuration: 0
            Keys.onPressed: (e) => {
                if (currentIndex < 0) return
                const it = model.get(currentIndex)
                if (e.key === Qt.Key_Delete) { tp.destroy_(it.entryId, it.title); e.accepted = true }
                else if (e.key === Qt.Key_R || e.key === Qt.Key_Return) { tp.restore(it.entryId, it.title); e.accepted = true }
            }
            delegate: Rectangle {
                id: r
                required property int index
                required property string entryId
                required property string title
                required property string username
                required property string groupPath
                width: ListView.view.width
                height: th.s(48)
                color: ListView.isCurrentItem && tl.activeFocus ? th.sel : tma.containsMouse ? th.hover : "transparent"
                RowLayout {
                    anchors.fill: parent; anchors.leftMargin: th.s(10); anchors.rightMargin: th.s(6)
                    ColumnLayout {
                        Layout.fillWidth: true; spacing: 1
                        KText { text: r.title; Layout.fillWidth: true }
                        KText { text: [r.username, r.groupPath ? "from " + r.groupPath : ""].filter(x => x).join("  ·  "); color: th.muted; font.pixelSize: th.base - 1; Layout.fillWidth: true }
                    }
                    KButton { text: "Restore"; kind: "ghost"; hint: "R"; onClicked: tp.restore(r.entryId, r.title) }
                    KButton { text: "Delete"; kind: "ghost"; hint: "Del"; onClicked: tp.destroy_(r.entryId, r.title) }
                }
                MouseArea { id: tma; anchors.fill: parent; hoverEnabled: true; z: -1; onClicked: { tl.currentIndex = r.index; tl.forceActiveFocus() } }
            }
            KText { anchors.centerIn: parent; visible: tl.count === 0 && tp.trashedGroups.length === 0; text: "The trash is empty."; color: th.muted }
        }
    }
}
