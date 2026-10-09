import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// The quick-search card: search, unlock, create, setup states.
FocusScope {
    id: card
    property string mode: "search"      // search | unlock | create | setup
    property string pending: "search"   // what to show after unlocking
    property string pendingTitle: ""
    property string flash: ""
    property string flashKind: "error"
    readonly property int rowH: th.s(46)
    readonly property int maxRows: 8
    width: Math.min(th.s(760), (parent ? parent.width : th.s(800)) - th.s(48))
    implicitHeight: body.implicitHeight + th.s(4)
    height: implicitHeight
    Accessible.role: Accessible.Dialog
    Accessible.name: "Keylane quick search"

    function open(m, text) {
        flash = ""
        if (kl.session === "no-vault") { mode = "setup"; Qt.callLater(focusMode); return }
        if (!kl.unlocked) {
            pending = m
            if (m === "new") pendingTitle = text
            else if (text) query.text = text
            mode = "unlock"
            unlockCard.reset()
            Qt.callLater(focusMode)
            return
        }
        if (m === "new") startCreate(text || query.text)
        else {
            if (mode !== "search") mode = "search"
            if (text) query.text = text
            kl.search(query.text)
        }
        Qt.callLater(focusMode)
    }
    function reset() {
        // Called on close: drop query, drafts, secrets.
        query.text = ""
        clearCreate()
        unlockCard.reset()
        flash = ""
        mode = "search"
        list.currentIndex = 0
    }
    function focusMode() {
        if (mode === "search") { query.forceActiveFocus(); query.selectAll() }
        else if (mode === "unlock") unlockCard.focusInput()
        else if (mode === "create") { (cTitle.text ? cUser : cTitle).focusField() }
        else setupBtn.forceActiveFocus()
    }
    function showError(msg) { flashKind = "error"; flash = msg }

    // ---- search actions
    function current() { return list.currentIndex >= 0 ? kl.resultsModel.get(list.currentIndex) : null }
    function copy(field) {
        const r = current()
        if (!r) { showError(query.text ? "No matching entry." : "Nothing selected."); return }
        kl.copyField(r.entryId, field, function (res) {
            if (res.ok) kl.hideLauncher()
            else showError(res.message)
        })
    }
    function edit() {
        const r = current()
        kl.openManager(r ? r.entryId : "")
    }

    // ---- create actions
    property var groupItems: []
    function rebuildGroups() {
        const items = [{ id: "", text: "(top level)" }]
        const gs = kl.groups
        for (let i = 0; i < gs.length; i++) {
            if (gs[i].isTrash || gs[i].inTrash || gs[i].depth === 0) continue
            items.push({ id: gs[i].id, text: gs[i].path })
        }
        groupItems = items
    }
    function startCreate(title) {
        clearCreate()
        rebuildGroups()
        const def = kl.settings.defaultGroup
        for (let i = 0; i < groupItems.length; i++) if (groupItems[i].id === def) cGroup.currentIndex = i
        cTitle.text = title || ""
        regenerate()
        mode = "create"
        Qt.callLater(focusMode)
    }
    function clearCreate() {
        cTitle.text = ""; cUser.text = ""; cPass.text = ""; cUrl.text = ""
        cPass.revealed = false; cBits = 0; showGen = false; saving = false
    }
    property int cBits: 0
    property bool showGen: false
    property bool saving: false
    function regenerate() {
        const r = kl.generate(gen.spec)
        if (r.ok) { cPass.text = r.value; cBits = r.bits } else showError(r.message)
    }
    function createKeys(e) {
        const ctrl = e.modifiers & Qt.ControlModifier
        if (e.key === Qt.Key_Escape) { card.mode = "search"; card.clearCreate(); Qt.callLater(card.focusMode); e.accepted = true }
        else if (ctrl && (e.key === Qt.Key_Return || e.key === Qt.Key_Enter)) { card.save(true); e.accepted = true }
        else if (ctrl && e.key === Qt.Key_S) { card.save(false); e.accepted = true }
        else if (ctrl && e.key === Qt.Key_R) { card.regenerate(); e.accepted = true }
        else if (ctrl && e.key === Qt.Key_G) { card.showGen = !card.showGen; e.accepted = true }
        else if (ctrl && e.key === Qt.Key_H) { cPass.revealed = !cPass.revealed; e.accepted = true }
        else if (ctrl && e.key === Qt.Key_E) { kl.handoffDraft(card.draft()); card.clearCreate(); e.accepted = true }
    }
    function draft() {
        return { title: cTitle.text, username: cUser.text, password: cPass.text, url: cUrl.text,
                 groupId: cGroup.currentIndex >= 0 && groupItems[cGroup.currentIndex] ? groupItems[cGroup.currentIndex].id : "" }
    }
    function save(copyAfter) {
        if (saving) return
        if (!cTitle.text.trim()) { showError("An entry needs a title."); cTitle.focusField(); return }
        saving = true
        flash = ""
        kl.createEntry(draft(), copyAfter, function (r) {
            saving = false
            if (!r.ok) { showError(r.message + " Your draft is kept."); return }
            if (copyAfter && r.copyError) {
                flashKind = "warning"
                flash = "Saved, but the password was not copied: " + r.copyError
                return
            }
            clearCreate()
            if (copyAfter) kl.hideLauncher()
            else {
                mode = "search"
                query.text = ""
                kl.search("")
                flashKind = "success"
                flash = "Saved."
                Qt.callLater(focusMode)
            }
        })
    }

    Connections {
        target: kl
        function onSessionChanged() {
            if (!kl.unlocked) {
                // Lock drops plaintext drafts immediately.
                card.clearCreate()
                if (card.mode === "search" || card.mode === "create") {
                    card.pending = "search"
                    card.mode = kl.session === "no-vault" ? "setup" : "unlock"
                }
            }
        }
        function onIndexChanged() { if (card.mode === "create") card.rebuildGroups() }
    }

    Rectangle {
        id: bg
        anchors.fill: parent
        color: th.bg
        radius: th.radius
        border.color: th.border
        border.width: th.s(2)
    }

    ColumnLayout {
        id: body
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: th.s(2)
        spacing: 0

        // ------------------------------------------------ search
        ColumnLayout {
            visible: card.mode === "search"
            Layout.fillWidth: true
            spacing: 0
            RowLayout {
                Layout.fillWidth: true
                Layout.margins: th.s(14)
                Layout.bottomMargin: th.s(10)
                spacing: th.s(12)
                Glyph { g: "\uf002"; color: th.accent; font.pixelSize: th.base + 6 }
                TextInput {
                    id: query
                    objectName: "query"
                    Layout.fillWidth: true
                    color: th.fg
                    font.family: th.fontFamily
                    font.pixelSize: th.base + 7
                    selectionColor: th.alpha(th.accent, 0.35)
                    selectedTextColor: th.fg
                    clip: true
                    focus: true
                    Accessible.role: Accessible.EditableText
                    Accessible.name: "Search entries"
                    onTextEdited: { card.flash = ""; kl.search(text); list.currentIndex = 0 }
                    KText {
                        anchors.fill: parent
                        verticalAlignment: Text.AlignVCenter
                        visible: !query.text && !query.preeditText
                        text: "Search " + kl.entryCount + " entries"
                        color: th.alpha(th.muted, 0.9)
                        font.pixelSize: th.base + 7
                    }
                    Keys.onPressed: (e) => {
                        const ctrl = e.modifiers & Qt.ControlModifier
                        if (e.key === Qt.Key_Down || (ctrl && e.key === Qt.Key_J) || (e.key === Qt.Key_Tab && !(e.modifiers & Qt.ShiftModifier))) {
                            list.currentIndex = Math.min(list.count - 1, list.currentIndex + 1); e.accepted = true
                        } else if (e.key === Qt.Key_Up || (ctrl && e.key === Qt.Key_K) || e.key === Qt.Key_Backtab) {
                            list.currentIndex = Math.max(0, list.currentIndex - 1); e.accepted = true
                        } else if (e.key === Qt.Key_PageDown) {
                            list.currentIndex = Math.min(list.count - 1, list.currentIndex + card.maxRows); e.accepted = true
                        } else if (e.key === Qt.Key_PageUp) {
                            list.currentIndex = Math.max(0, list.currentIndex - card.maxRows); e.accepted = true
                        } else if (e.key === Qt.Key_Return || e.key === Qt.Key_Enter) {
                            card.copy(ctrl ? "UserName" : "Password"); e.accepted = true
                        } else if (ctrl && e.key === Qt.Key_N) {
                            card.startCreate(query.text); e.accepted = true
                        } else if (ctrl && e.key === Qt.Key_E) {
                            card.edit(); e.accepted = true
                        } else if (ctrl && e.key === Qt.Key_U) {
                            card.copy("URL"); e.accepted = true
                        } else if (ctrl && e.key === Qt.Key_L) {
                            kl.lockNow(); e.accepted = true
                        } else if (e.key === Qt.Key_Escape) {
                            if (query.text) { query.text = ""; kl.search(""); list.currentIndex = 0 }
                            else kl.hideLauncher()
                            e.accepted = true
                        }
                    }
                }
                Rectangle {
                    visible: kl.conflictCount > 0
                    implicitWidth: cf.implicitWidth + th.s(12)
                    implicitHeight: cf.implicitHeight + th.s(6)
                    radius: th.radius
                    color: th.alpha(th.warning, 0.12)
                    border.color: th.alpha(th.warning, 0.5)
                    KText { id: cf; anchors.centerIn: parent; text: kl.conflictCount + " sync conflict" + (kl.conflictCount > 1 ? "s" : ""); color: th.warning; font.pixelSize: th.base - 1 }
                }
                KText { text: kl.vaultName; color: th.muted; font.pixelSize: th.base - 1 }
            }
            Separator { Layout.fillWidth: true; visible: list.count > 0 || query.text }
            ListView {
                id: list
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(count, card.maxRows) * card.rowH + (count ? th.s(12) : 0)
                topMargin: count ? th.s(6) : 0
                bottomMargin: count ? th.s(6) : 0
                clip: true
                model: kl.resultsModel
                currentIndex: 0
                boundsBehavior: Flickable.StopAtBounds
                highlightMoveDuration: 0
                keyNavigationEnabled: false
                Accessible.role: Accessible.List
                Accessible.name: "Results"
                ScrollIndicator.vertical: ScrollIndicator {}
                delegate: Rectangle {
                    id: row
                    required property int index
                    required property string title
                    required property string username
                    required property string groupPath
                    required property string host
                    required property bool conflicted
                    required property bool hasPassword
                    required property bool hasOtp
                    readonly property bool sel: ListView.isCurrentItem
                    width: ListView.view.width - th.s(12)
                    x: th.s(6)
                    height: card.rowH
                    radius: th.radius
                    color: sel ? th.sel : ma.containsMouse ? th.faint : "transparent"
                    border.width: sel ? 1 : 0
                    border.color: th.selBorder
                    Accessible.role: Accessible.ListItem
                    Accessible.name: title + (username ? ", " + username : "") + (groupPath ? ", in " + groupPath : "")
                    Accessible.selected: sel
                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: th.s(10)
                        anchors.rightMargin: th.s(12)
                        spacing: th.s(12)
                        Rectangle {
                            implicitWidth: th.s(28)
                            implicitHeight: th.s(28)
                            radius: th.radius > 0 ? th.s(6) : 0
                            color: row.sel ? th.alpha(th.accent, 0.16) : th.faint
                            border.color: row.sel ? th.alpha(th.accent, 0.5) : th.hairline
                            KText {
                                anchors.centerIn: parent
                                text: (row.title || "?").charAt(0).toUpperCase()
                                color: row.sel ? th.selFg : th.muted
                                font.bold: true
                                font.pixelSize: th.base + 1
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: th.s(1)
                            KText {
                                Layout.fillWidth: true
                                text: row.title
                                color: row.sel ? th.selFg : th.fg
                                font.pixelSize: th.base + 2
                            }
                            KText {
                                Layout.fillWidth: true
                                text: [row.username, row.groupPath].filter(x => x).join("  ·  ")
                                color: th.muted
                                font.pixelSize: th.base - 1
                                visible: text !== ""
                            }
                        }
                        Glyph { visible: row.conflicted; g: "\uf071"; color: th.warning; font.pixelSize: th.base }
                        Glyph { visible: row.hasOtp; g: "\uf017"; font.pixelSize: th.base }
                        KText {
                            visible: row.host !== ""
                            text: row.host
                            color: th.muted
                            font.pixelSize: th.base - 1
                            Layout.maximumWidth: th.s(200)
                        }
                        KText {
                            visible: !row.hasPassword
                            text: "no password"
                            color: th.alpha(th.muted, 0.7)
                            font.pixelSize: th.base - 2
                        }
                    }
                    MouseArea {
                        id: ma
                        anchors.fill: parent
                        hoverEnabled: true
                        onClicked: { list.currentIndex = row.index; query.forceActiveFocus() }
                        onDoubleClicked: { list.currentIndex = row.index; card.copy("Password") }
                    }
                }
            }
            KText {
                visible: list.count === 0
                Layout.fillWidth: true
                Layout.margins: th.s(14)
                Layout.topMargin: th.s(12)
                Layout.bottomMargin: th.s(12)
                text: query.text ? "No matches. Press Ctrl+N to create “" + query.text + "”."
                                 : kl.entryCount ? "Start typing. Recently used entries appear here." : "Your vault is empty. Press Ctrl+N to create the first entry."
                color: th.muted
                wrapMode: Text.Wrap
            }
        }

        // ------------------------------------------------ unlock
        UnlockCard {
            id: unlockCard
            visible: card.mode === "unlock"
            Layout.fillWidth: true
            Layout.margins: th.s(18)
            onCancelled: kl.hideLauncher()
            onUnlocked: {
                if (card.pending === "new") card.startCreate(card.pendingTitle)
                else if (card.pending === "manager") { kl.openManager("") }
                else { card.mode = "search"; kl.search(query.text); list.currentIndex = 0; Qt.callLater(card.focusMode) }
            }
        }

        // ------------------------------------------------ setup
        ColumnLayout {
            visible: card.mode === "setup"
            Layout.fillWidth: true
            Layout.margins: th.s(18)
            spacing: th.s(10)
            RowLayout {
                spacing: th.s(10)
                Glyph { g: "\uf084"; color: th.accent; font.pixelSize: th.base + 6 }
                KText { text: "Welcome to Keylane"; font.pixelSize: th.base + 4; font.bold: true }
            }
            KText {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                color: th.muted
                text: "Open your existing KeePass (.kdbx) vault or create a new one. Keylane works directly on the file; nothing is imported or copied."
            }
            RowLayout {
                Layout.topMargin: th.s(4)
                Item { Layout.fillWidth: true }
                KButton { text: "Close"; kind: "ghost"; hint: "Esc"; onClicked: kl.hideLauncher() }
                KButton { id: setupBtn; text: "Set up vault"; kind: "primary"; hint: "↵"; onClicked: kl.openManagerPage("setup")
                    Keys.onEscapePressed: kl.hideLauncher() }
            }
        }

        // ------------------------------------------------ create
        ColumnLayout {
            visible: card.mode === "create"
            Layout.fillWidth: true
            Layout.margins: th.s(18)
            spacing: th.s(10)
            Keys.onPressed: (e) => card.createKeys(e)
            RowLayout {
                spacing: th.s(10)
                Glyph { g: "\uf067"; color: th.accent; font.pixelSize: th.base + 4 }
                KText { text: "New entry"; font.pixelSize: th.base + 4; font.bold: true }
                Item { Layout.fillWidth: true }
                KText { text: kl.vaultName; color: th.muted; font.pixelSize: th.base - 1 }
            }
            GridLayout {
                Layout.fillWidth: true
                columns: 2
                columnSpacing: th.s(12)
                rowSpacing: th.s(8)
                KField { id: cTitle; keyHandler: card.createKeys; Layout.fillWidth: true; Layout.columnSpan: 2; label: "Title"; placeholder: "e.g. GitHub"; onAccepted: card.save(true) }
                KField { id: cUser; keyHandler: card.createKeys; Layout.fillWidth: true; Layout.preferredWidth: 1; label: "Username"; placeholder: "user@example.com"; onAccepted: card.save(true) }
                KField {
                    id: cPass
                    keyHandler: card.createKeys
                    Layout.fillWidth: true
                    Layout.preferredWidth: 1
                    label: "Password" + (card.cBits ? "  ·  " + cPass.text.length + " chars, ≈" + card.cBits + " bits" : "")
                    secret: true
                    mono: true
                    onAccepted: card.save(true)
                    onEdited: card.cBits = 0
                    IconButton { glyph: cPass.revealed ? "\uf070" : "\uf06e"; tip: (cPass.revealed ? "Hide" : "Show") + " (Ctrl+H)"; onClicked: cPass.revealed = !cPass.revealed }
                    IconButton { glyph: "\uf021"; tip: "Regenerate (Ctrl+R)"; onClicked: card.regenerate() }
                    IconButton { glyph: "\uf013"; tip: "Generator settings (Ctrl+G)"; checked: card.showGen; onClicked: card.showGen = !card.showGen }
                }
                KField { id: cUrl; keyHandler: card.createKeys; Layout.fillWidth: true; Layout.preferredWidth: 1; label: "URL"; placeholder: "https://"; onAccepted: card.save(true) }
                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.preferredWidth: 1
                    spacing: th.s(4)
                    KText { text: "Group"; font.pixelSize: th.base - 1; color: th.muted }
                    KCombo {
                        id: cGroup
                        Layout.fillWidth: true
                        model: card.groupItems
                        textRole: "text"
                        Accessible.name: "Group"
                        onAccepted: card.save(true)
                    }
                }
            }
            GeneratorPanel {
                id: gen
                visible: card.showGen
                Layout.fillWidth: true
                onUse: (v) => { cPass.text = v; card.cBits = gen.bits; card.showGen = false; cPass.focusField() }
            }
            RowLayout {
                Layout.topMargin: th.s(4)
                spacing: th.s(8)
                KButton { text: "Full editor"; kind: "ghost"; hint: "Ctrl+E"; onClicked: { kl.handoffDraft(card.draft()); card.clearCreate() } }
                Item { Layout.fillWidth: true }
                KButton { text: "Cancel"; kind: "ghost"; hint: "Esc"; onClicked: { card.mode = "search"; card.clearCreate(); Qt.callLater(card.focusMode) } }
                KButton { text: "Save"; hint: "Ctrl+S"; enabled: !card.saving; onClicked: card.save(false) }
                KButton { text: card.saving ? "Saving…" : "Save & copy"; kind: "primary"; hint: "↵"; enabled: !card.saving; onClicked: card.save(true) }
            }
        }

        // ------------------------------------------------ status + footer
        Banner {
            Layout.fillWidth: true
            Layout.leftMargin: th.s(12)
            Layout.rightMargin: th.s(12)
            Layout.bottomMargin: th.s(8)
            kind: card.flashKind
            text: card.flash
        }
        Banner {
            Layout.fillWidth: true
            Layout.leftMargin: th.s(12)
            Layout.rightMargin: th.s(12)
            Layout.bottomMargin: th.s(8)
            kind: "warning"
            text: card.mode === "search" && kl.clipboardHistoryWarning ? kl.clipboardHistoryWarning : ""
        }
        Rectangle {
            visible: card.mode === "search"
            Layout.fillWidth: true
            implicitHeight: footer.implicitHeight + th.s(14)
            color: th.faint
            radius: th.radius
            Separator { anchors.top: parent.top; width: parent.width }
            Flow {
                id: footer
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: th.s(14)
                anchors.rightMargin: th.s(14)
                spacing: th.s(16)
                KeyHint { keys: "↵"; label: "Copy password"; active: list.count > 0 }
                KeyHint { keys: "Ctrl+↵"; label: "Copy username"; active: list.count > 0 }
                KeyHint { keys: "Ctrl+N"; label: "New" }
                KeyHint { keys: "Ctrl+E"; label: "Edit"; active: list.count > 0 }
                KeyHint { keys: "Esc"; label: "Close" }
            }
        }
    }
}
