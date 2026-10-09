import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Window

// Full manager window. Closing hides it (the vault stays as it is); lock is explicit.
ApplicationWindow {
    id: win
    title: kl.vaultName ? kl.vaultName + " — Keylane" : "Keylane"
    width: 1180
    height: 760
    minimumWidth: 720
    minimumHeight: 480
    color: th.bgSolid
    font.family: th.fontFamily
    font.pixelSize: th.base + 1

    property string page: "entries"        // entries | conflicts | trash | settings
    // Tiling compositors hand out odd sizes; degrade gracefully.
    readonly property bool compact: width < th.s(980)
    readonly property bool narrow: width < th.s(800)
    property bool sidebarShown: false
    onNarrowChanged: sidebarShown = false
    onSidebarShownChanged: if (sidebarShown) sidebar.focusNav()
    property string groupId: ""
    property string selectedId: ""
    property bool forceClose: false
    readonly property bool unlocked: kl.unlocked

    function present(p, entryId) {
        if (p === "setup" || p === "settings") page = "settings"
        else if (p === "draft") page = "entries"
        else if (p) page = p
        if (entryId && unlocked) { page = "entries"; groupId = ""; search.text = ""; refresh(); selectLater(entryId) }
        if (!visible) { visible = true }
        raise(); requestActivate()
        Qt.callLater(focusDefault)
    }
    function focusDefault() {
        if (!kl.unlocked) { if (kl.session === "locked") unlock.focusInput(); return }
        if (page === "entries") { if (editor.active && selectedId) list.forceActiveFocus(); else search.focusField() }
        else focusPage()
    }
    function refresh() { kl.listEntries(groupId, search.text) }
    function selectLater(id) { pendingSelect = id; Qt.callLater(applyPending) }
    property string pendingSelect: ""
    function applyPending() {
        if (!pendingSelect) return
        const m = kl.entriesModel
        for (let i = 0; i < m.count; i++) if (m.get(i).entryId === pendingSelect) { list.currentIndex = i; list.positionViewAtIndex(i, ListView.Contain); break }
        guardedSelect(pendingSelect)
        pendingSelect = ""
    }
    // Ask before abandoning unsaved edits. then(proceed: bool)
    function confirmLeave(then) {
        if (!editor.dirty) { then(true); return }
        confirm.ask("Unsaved changes", "Save the changes to “" + (editor.titleText() || "this entry") + "” first?",
            [{ id: "cancel", text: "Cancel", kind: "ghost" }, { id: "discard", text: "Discard", kind: "danger" }, { id: "save", text: "Save", kind: "primary" }],
            function (c) {
                if (c === "discard") { editor.clear(); then(true) }
                else if (c === "save") editor.save(function (ok) { then(ok) })
                else then(false)
            })
    }
    function guardedSelect(id) {
        if (id === editor.entryId && !editor.isNew) return
        confirmLeave(function (ok) {
            if (!ok) { syncListToEditor(); return }
            selectedId = id
            editor.load(id)
        })
    }
    function syncListToEditor() {
        const m = kl.entriesModel
        for (let i = 0; i < m.count; i++) if (m.get(i).entryId === editor.entryId) { list.currentIndex = i; return }
        list.currentIndex = -1
    }
    // Row indices shift whenever the list is re-filtered or the vault changes;
    // keep the highlight on the entry the editor shows, not on a stale index.
    Connections {
        target: kl.entriesModel
        function onModelReset() { Qt.callLater(win.syncListToEditor) }
        function onRowsInserted() { Qt.callLater(win.syncListToEditor) }
        function onRowsRemoved() { Qt.callLater(win.syncListToEditor) }
        function onDataChanged() { Qt.callLater(win.syncListToEditor) }
    }
    function newEntry(prefill) {
        confirmLeave(function (ok) {
            if (!ok) return
            page = "entries"
            selectedId = ""
            if (search.text) { search.text = ""; refresh() }
            list.currentIndex = -1
            const p = prefill || {}
            if (!p.groupId && groupId) p.groupId = groupId
            editor.newEntry(p)
        })
    }
    function focusPage() {
        if (page === "entries") { if (list.count) { list.currentIndex = Math.max(0, list.currentIndex); list.forceActiveFocus() } else search.focusField() }
        else if (page === "trash") trashPage.focusList()
        else if (page === "conflicts") conflictsPage.focusList()
        else if (page === "settings") settingsPage.focusPage()
    }
    function go(p, gid) {
        confirmLeave(function (ok) {
            if (!ok) return
            page = p
            groupId = gid || ""
            if (narrow) sidebarShown = false
            if (p === "entries") { refresh(); list.currentIndex = -1; if (!editor.dirty) editor.clear() }
            Qt.callLater(focusPage)
        })
    }

    onClosing: (close) => {
        if (forceClose || !editor.dirty) return
        close.accepted = false
        confirmLeave(function (ok) { if (ok) { win.visible = false } })
    }
    onUnlockedChanged: {
        if (unlocked) { refresh(); Qt.callLater(focusDefault) }
        else { editor.clear(); selectedId = ""; search.text = ""; confirm.close(); unlock.reset(); Qt.callLater(focusDefault) }
    }
    Component.onCompleted: if (unlocked) refresh()

    Connections {
        target: kl
        function onDraftHandoff(d) { win.newEntry(d) }
        function onIndexChanged() { if (win.page === "entries") win.syncListToEditor() }
    }

    Shortcut { sequences: ["Ctrl+\\"]; enabled: win.unlocked && win.narrow; onActivated: win.sidebarShown = !win.sidebarShown }
    Shortcut { sequences: ["Ctrl+Shift+H"]; enabled: win.unlocked && win.page === "entries" && !editor.activeFocus; onActivated: editor.showHistory() }
    Shortcut { sequences: ["Ctrl+G"]; enabled: win.unlocked; onActivated: { if (win.narrow) win.sidebarShown = true; sidebar.focusNav() } }
    Shortcut { sequences: ["Ctrl+N"]; enabled: win.unlocked; onActivated: win.newEntry() }
    Shortcut { sequences: ["Ctrl+F", "/"]; enabled: win.unlocked && win.page === "entries" && !editorHasFocus(); onActivated: { search.focusField() } }
    Shortcut { sequences: ["Ctrl+L"]; enabled: win.unlocked; onActivated: win.confirmLeave(function (ok) { if (ok) kl.lockNow() }) }
    Shortcut { sequences: ["Ctrl+W"]; onActivated: win.close() }
    Shortcut { sequences: ["Ctrl+Q"]; onActivated: win.confirmLeave(function (ok) { if (ok) kl.quit() }) }
    Shortcut { sequences: ["Ctrl+,"]; enabled: win.unlocked; onActivated: win.go("settings") }
    Shortcut { sequences: ["Ctrl+1"]; enabled: win.unlocked; onActivated: win.go("entries", "") }
    Shortcut { sequences: ["Ctrl+B"]; enabled: win.unlocked && editor.entryId !== ""; onActivated: kl.copyField(editor.entryId, "UserName", win.copyCb) }
    Shortcut { sequences: ["Ctrl+C"]; enabled: win.unlocked && list.activeFocus && editor.entryId !== ""; onActivated: kl.copyField(editor.entryId, "Password", win.copyCb) }
    Shortcut { sequences: ["Ctrl+U"]; enabled: win.unlocked && list.activeFocus && editor.entryId !== ""; onActivated: kl.copyField(editor.entryId, "URL", win.copyCb) }
    Shortcut { sequences: ["Delete"]; enabled: win.unlocked && list.activeFocus && editor.entryId !== "" && win.page === "entries"; onActivated: win.trashEntry(editor.entryId) }
    function editorHasFocus() { const f = win.activeFocusItem; return f && (f instanceof TextInput || f instanceof TextEdit) }
    property var copyCb: function (r) { if (!r.ok) status.show("error", r.message) }

    function trashEntry(id) {
        const t = editor.titleText()
        kl.recycle(id, function (r) {
            if (!r.ok) { status.show("error", r.message); return }
            editor.clear(); selectedId = ""
            status.show("info", "Moved “" + t + "” to the trash.")
        })
    }

    ConfirmDialog { id: confirm }
    TextPrompt { id: prompt }

    // ------------------------------------------------------------ locked / setup
    Item {
        anchors.fill: parent
        visible: !win.unlocked
        Rectangle {
            anchors.centerIn: parent
            width: Math.min(th.s(480), parent.width - th.s(40))
            height: lockedCol.implicitHeight + th.s(48)
            color: th.surface
            radius: th.radius
            border.color: th.border
            border.width: th.s(2)
            visible: kl.session !== "no-vault"
            ColumnLayout {
                id: lockedCol
                anchors.fill: parent
                anchors.margins: th.s(24)
                UnlockCard {
                    id: unlock
                    Layout.fillWidth: true
                    onCancelled: win.close()
                }
                RowLayout {
                    Layout.topMargin: th.s(8)
                    KText { text: kl.vaultPath; color: th.muted; font.pixelSize: th.base - 1; Layout.fillWidth: true; elide: Text.ElideMiddle }
                    KButton { text: "Change vault…"; kind: "ghost"; onClicked: kl.openManagerPage("setup"); visible: false }
                }
            }
        }
        SetupPage {
            anchors.centerIn: parent
            width: Math.min(th.s(600), parent.width - th.s(40))
            visible: kl.session === "no-vault"
        }
    }

    // ------------------------------------------------------------ unlocked
    RowLayout {
        anchors.fill: parent
        visible: win.unlocked
        spacing: 0

        Sidebar {
            id: sidebar
            visible: !win.narrow || win.sidebarShown
            Layout.preferredWidth: win.compact ? th.s(190) : th.s(230)
            Layout.fillHeight: true
            page: win.page
            groupId: win.groupId
            onNavigate: (p, gid) => win.go(p, gid)
            onNewGroup: (parentId) => prompt.ask("New group", "Name", "", function (name) {
                kl.createGroup(name, parentId, function (r) { if (!r.ok) status.show("error", r.message); else win.go("entries", r.id) })
            })
            onRenameGroup: (gid, name) => prompt.ask("Rename group", "Name", name, function (n) {
                kl.renameGroup(gid, n, function (r) { if (!r.ok) status.show("error", r.message) })
            })
            onTrashGroup: (gid, name) => confirm.ask("Move “" + name + "” to the trash?", "The group and everything in it can be restored from the trash.",
                [{ id: "cancel", text: "Cancel", kind: "ghost" }, { id: "ok", text: "Move to trash", kind: "danger" }],
                function (c) { if (c === "ok") kl.recycle(gid, function (r) { if (!r.ok) status.show("error", r.message); else win.go("entries", "") }) })
            onMoveGroup: (gid) => groupMover.openFor(gid)
            onDismissed: { if (win.narrow) win.sidebarShown = false; if (win.page === "entries") list.forceActiveFocus() }
            onLock: win.confirmLeave(function (ok) { if (ok) kl.lockNow() })
        }
        Separator { Layout.fillHeight: true; implicitWidth: 1; visible: sidebar.visible }

        // entry list column
        ColumnLayout {
            visible: win.page === "entries"
            Layout.preferredWidth: win.compact ? th.s(260) : th.s(340)
            Layout.maximumWidth: Layout.preferredWidth
            Layout.fillHeight: true
            spacing: 0
            ColumnLayout {
                Layout.fillWidth: true
                Layout.margins: th.s(12)
                spacing: th.s(8)
                RowLayout {
                    IconButton { visible: win.narrow; glyph: "\uf0c9"; tip: "Groups (Ctrl+\\)"; onClicked: win.sidebarShown = !win.sidebarShown }
                    KText {
                        Layout.fillWidth: true
                        elide: Text.ElideRight
                        text: sidebar.groupName(win.groupId)
                        font.pixelSize: th.base + 4
                        font.bold: true
                    }
                    KText { text: kl.entriesModel.count; color: th.muted }
                    IconButton { glyph: "\uf067"; tip: "New entry (Ctrl+N)"; onClicked: win.newEntry() }
                }
                KField {
                    id: search
                    Layout.fillWidth: true
                    placeholder: "Filter (Ctrl+F)"
                    onEdited: win.refresh()
                    onAccepted: { if (list.count) { list.currentIndex = 0; list.forceActiveFocus(); selectTimer.restart() } }
                    input.Keys.onDownPressed: { if (list.count) { list.currentIndex = Math.max(0, list.currentIndex); list.forceActiveFocus(); selectTimer.restart() } }
                    input.Keys.onEscapePressed: { if (search.text) { search.text = ""; win.refresh() } else list.forceActiveFocus() }
                }
            }
            ListView {
                id: list
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                model: kl.entriesModel
                currentIndex: -1
                keyNavigationEnabled: true
                highlightMoveDuration: 0
                activeFocusOnTab: true
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar { }
                Keys.onReturnPressed: if (currentIndex >= 0) { win.guardedSelect(model.get(currentIndex).entryId); }
                Keys.onUpPressed: (e) => { if (currentIndex <= 0) search.focusField(); else { decrementCurrentIndex(); e.accepted = true } }
                Keys.onPressed: (e) => {
                    if (e.key === Qt.Key_Home && count) { currentIndex = 0; selectTimer.restart(); e.accepted = true }
                    else if (e.key === Qt.Key_End && count) { currentIndex = count - 1; selectTimer.restart(); e.accepted = true }
                    else if (e.key === Qt.Key_J && e.modifiers & Qt.ControlModifier) { incrementCurrentIndex(); e.accepted = true }
                    else if (e.key === Qt.Key_K && e.modifiers & Qt.ControlModifier) { decrementCurrentIndex(); e.accepted = true }
                }
                onCurrentIndexChanged: if (currentIndex >= 0 && activeFocus) selectTimer.restart()
                Timer { id: selectTimer; interval: 60; onTriggered: if (list.currentIndex >= 0) win.guardedSelect(list.model.get(list.currentIndex).entryId) }
                delegate: Rectangle {
                    id: row
                    required property int index
                    required property string entryId
                    required property string title
                    required property string username
                    required property string groupPath
                    required property string host
                    required property bool conflicted
                    required property bool hasOtp
                    required property int attachments
                    readonly property bool cur: ListView.isCurrentItem
                    width: ListView.view.width
                    height: th.s(50)
                    color: cur ? th.sel : ma.containsMouse ? th.hover : "transparent"
                    Rectangle { visible: row.cur; width: th.s(3); height: parent.height; color: th.accent }
                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: th.s(14)
                        anchors.rightMargin: th.s(12)
                        spacing: th.s(10)
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 1
                            KText { Layout.fillWidth: true; text: row.title; color: row.cur ? th.selFg : th.fg; font.bold: row.cur }
                            KText {
                                Layout.fillWidth: true
                                text: [row.username, row.host].filter(x => x).join("  ·  ") || row.groupPath
                                color: th.muted
                                font.pixelSize: th.base - 1
                            }
                        }
                        Glyph { visible: row.conflicted; g: "\uf071"; color: th.warning }
                        Glyph { visible: row.attachments > 0; g: "\uf0c6"; color: th.muted }
                    }
                    MouseArea {
                        id: ma
                        anchors.fill: parent
                        hoverEnabled: true
                        onClicked: { list.currentIndex = row.index; list.forceActiveFocus(); win.guardedSelect(row.entryId) }
                    }
                }
                KText {
                    anchors.centerIn: parent
                    visible: list.count === 0
                    width: parent.width - th.s(40)
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.Wrap
                    color: th.muted
                    text: search.text ? "Nothing matches “" + search.text + "”." : "No entries here yet.\nPress Ctrl+N to create one."
                }
            }
        }
        Separator { Layout.fillHeight: true; implicitWidth: 1; visible: win.page === "entries" }

        EntryEditor {
            id: editor
            visible: win.page === "entries"
            Layout.fillWidth: true
            Layout.fillHeight: true
            onSaved: (id) => { win.selectedId = id; win.refresh(); win.selectLater(id) }
            onTrashRequested: (id) => win.trashEntry(id)
            onRequestResolve: (id) => { win.go("conflicts", ""); conflictsPage.selectEntry(id) }
        }
        ConflictsPage {
            id: conflictsPage
            visible: win.page === "conflicts"
            Layout.fillWidth: true
            Layout.fillHeight: true
            confirmDialog: confirm
            onMessage: (k, t) => status.show(k, t)
            onOpenEntry: (id) => { win.go("entries", ""); win.selectLater(id) }
        }
        TrashPage {
            id: trashPage
            visible: win.page === "trash"
            Layout.fillWidth: true
            Layout.fillHeight: true
            onMessage: (k, t) => status.show(k, t)
            confirmDialog: confirm
        }
        SettingsPage {
            id: settingsPage
            visible: win.page === "settings"
            Layout.fillWidth: true
            Layout.fillHeight: true
            confirmDialog: confirm
            onMessage: (k, t) => status.show(k, t)
        }
    }

    GroupMover { id: groupMover; onMessage: (k, t) => status.show(k, t) }

    // ephemeral status line
    Rectangle {
        id: status
        property string kind: "info"
        property string text: ""
        function show(k, t) { kind = k; text = t; opacity = 1; hideT.restart() }
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: th.s(64)
        width: Math.min(stxt.implicitWidth + th.s(32), parent.width - th.s(40))
        height: stxt.implicitHeight + th.s(16)
        radius: th.radius
        color: th.surface
        border.width: th.s(2)
        border.color: kind === "error" ? th.danger : kind === "warning" ? th.warning : th.accent
        opacity: 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: 160 } }
        KText { id: stxt; anchors.centerIn: parent; text: status.text; width: Math.min(implicitWidth, win.width - th.s(80)); wrapMode: Text.Wrap }
        Timer { id: hideT; interval: status.kind === "error" ? 6000 : 2600; onTriggered: status.opacity = 0 }
    }
    Connections {
        target: kl
        function onNotice(k, t) { if (win.visible) status.show(k, t) }
        function onCopied(label, secs) { if (win.visible && win.active) status.show("success", label + " copied · clears in " + secs + "s") }
    }
}
