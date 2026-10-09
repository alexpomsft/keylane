import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs

Item {
    id: st
    property var confirmDialog: null
    signal message(string kind, string text)
    property var backupList: []
    function focusPage() { sv.forceActiveFocus() }
    function apply(m) { const r = kl.saveSettings(m); if (!r.ok) message("error", r.message); else message("success", "Settings saved.") }
    onVisibleChanged: if (visible) backupList = kl.backups()

    FileDialog { id: vaultDlg; title: "Choose a KDBX vault"; nameFilters: ["KeePass databases (*.kdbx)", "All files (*)"]; onAccepted: vPath.text = kl.localPath(selectedFile) }
    FileDialog { id: keyDlg; property var target: null; title: "Choose a key file"; onAccepted: target.text = kl.localPath(selectedFile) }
    FileDialog { id: mergeDlg; title: "Merge from KDBX file"; nameFilters: ["KeePass databases (*.kdbx)", "All files (*)"]; onAccepted: mPath.text = kl.localPath(selectedFile) }

    component Section: ColumnLayout {
        default property alias body: inner.data
        property string title: ""
        property string subtitle: ""
        Layout.fillWidth: true
        Layout.maximumWidth: th.s(760)
        spacing: th.s(10)
        SectionLabel { text: parent.title }
        KText { visible: parent.subtitle !== ""; text: parent.subtitle; color: th.muted; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: th.base }
        ColumnLayout { id: inner; Layout.fillWidth: true; spacing: th.s(10) }
        Separator { Layout.fillWidth: true; Layout.topMargin: th.s(10) }
    }
    component Row2: RowLayout {
        property string label: ""
        default property alias body: content.data
        Layout.fillWidth: true
        spacing: th.s(12)
        KText { text: parent.label; Layout.preferredWidth: th.s(220); wrapMode: Text.Wrap }
        RowLayout { id: content; Layout.fillWidth: true; spacing: th.s(8) }
    }

    AutoScrollView {
        id: sv
        anchors.fill: parent
        ColumnLayout {
            width: sv.availableWidth - th.s(40)
            x: th.s(24)
            spacing: th.s(18)
            Item { implicitHeight: th.s(6) }
            KText { text: "Settings"; font.pixelSize: th.base + 6; font.bold: true }
            Repeater {
                model: kl.settings.warnings
                Banner { required property var modelData; Layout.fillWidth: true; Layout.maximumWidth: th.s(760); kind: "warning"; text: modelData }
            }

            Section {
                title: "Vault"
                subtitle: "Keylane edits this KDBX file in place, keeping it compatible with KeePassXC and other KeePass apps."
                KField {
                    id: vPath; Layout.fillWidth: true; label: "Vault file"; text: kl.settings.vaultPath
                    IconButton { glyph: "\uf07c"; tip: "Browse…"; onClicked: vaultDlg.open() }
                }
                KField {
                    id: vKey; Layout.fillWidth: true; label: "Key file (optional)"; text: kl.settings.keyfilePath; placeholder: "none"
                    IconButton { glyph: "\uf07c"; tip: "Browse…"; onClicked: { keyDlg.target = vKey; keyDlg.open() } }
                }
                RowLayout {
                    Item { Layout.fillWidth: true }
                    KButton {
                        text: "Use this vault"
                        enabled: vPath.text !== kl.settings.vaultPath || vKey.text !== kl.settings.keyfilePath
                        onClicked: st.confirmDialog.ask("Switch vault?", "The current vault is locked first.",
                            [{ id: "cancel", text: "Cancel", kind: "ghost" }, { id: "ok", text: "Switch", kind: "primary" }],
                            function (c) { if (c === "ok") { kl.lockNow(); const r = kl.useVault(vPath.text, vKey.text); st.message(r.ok ? "success" : "error", r.ok ? "Vault changed." : r.message) } })
                    }
                }
                GridLayout {
                    visible: kl.unlocked
                    columns: 2
                    columnSpacing: th.s(18)
                    rowSpacing: th.s(2)
                    KText { text: "Format"; color: th.muted }
                    KText { text: (kl.formatInfo.version || "?") + " · " + (kl.formatInfo.cipher || "") }
                    KText { text: "Key derivation"; color: th.muted }
                    KText { text: (kl.formatInfo.kdf || "") + (kl.formatInfo.kdfDetail ? " · " + kl.formatInfo.kdfDetail : "") }
                    KText { text: "Contents"; color: th.muted }
                    KText { text: (kl.formatInfo.entries || 0) + " entries in " + (kl.formatInfo.groups || 0) + " groups" }
                }
            }

            Section {
                title: "Locking"
                Row2 { label: "Lock when the desktop locks"; KSwitch { checked: kl.settings.lockOnDesktopLock; onToggled: st.apply({ lockOnDesktopLock: checked }) } }
                Row2 { label: "Lock before sleep or hibernate"; KSwitch { checked: kl.settings.lockOnSleep; onToggled: st.apply({ lockOnSleep: checked }) } }
                Row2 {
                    label: "Lock after inactivity"
                    KField { id: inact; Layout.fillWidth: false; Layout.preferredWidth: th.s(80); text: String(kl.settings.inactivityMinutes); input.validator: IntValidator { bottom: 0; top: 1440 }
                         onAccepted: st.apply({ inactivityMinutes: parseInt(text || "0") }) }
                    KText { text: "minutes (0 = never)"; color: th.muted }
                    KButton { text: "Apply"; kind: "ghost"; visible: inact.text !== String(kl.settings.inactivityMinutes); onClicked: st.apply({ inactivityMinutes: parseInt(inact.text || "0") }) }
                }
                KText { text: "Unsaved drafts are discarded when the vault locks; they are never written to disk."; color: th.muted; font.pixelSize: th.base - 1; Layout.fillWidth: true; wrapMode: Text.Wrap }
            }

            Section {
                title: "Clipboard"
                Row2 {
                    label: "Clear copied secrets after"
                    KField { id: clipS; Layout.fillWidth: false; Layout.preferredWidth: th.s(80); text: String(kl.settings.clipboardSeconds); input.validator: IntValidator { bottom: 5; top: 600 }
                         onAccepted: st.apply({ clipboardSeconds: parseInt(text) }) }
                    KText { text: "seconds"; color: th.muted }
                    KButton { text: "Apply"; kind: "ghost"; visible: clipS.text !== String(kl.settings.clipboardSeconds); onClicked: st.apply({ clipboardSeconds: parseInt(clipS.text) }) }
                }
                Banner {
                    Layout.fillWidth: true
                    kind: kl.clipboardHistoryWarning ? "warning" : "success"
                    text: kl.clipboardHistoryWarning ? kl.clipboardHistoryWarning : "Clipboard history: copies are marked sensitive and your history tool skips them."
                }
            }

            Section {
                title: "Password generator"
                subtitle: "Defaults for new entries and quick create."
                GeneratorPanel { Layout.fillWidth: true }
            }

            Section {
                title: "Appearance"
                Row2 {
                    label: "Theme"
                    KCombo {
                        Layout.preferredWidth: th.s(220)
                        model: [{ v: "auto", t: "Auto (follow Omarchy)" }, { v: "omarchy", t: "Omarchy" }, { v: "dark", t: "Dark" }, { v: "light", t: "Light" }]
                        textRole: "t"
                        currentIndex: Math.max(0, ["auto", "omarchy", "dark", "light"].indexOf(kl.settings.theme))
                        onActivated: (i) => st.apply({ theme: model[i].v })
                    }
                    KText { text: th.source === "omarchy" ? "Using the current Omarchy theme" : (kl.settings.theme === "auto" || kl.settings.theme === "omarchy") ? "No Omarchy theme found; using built-in colors" : "Built-in colors"; color: th.muted; font.pixelSize: th.base - 1; Layout.fillWidth: true }
                }
            }

            Section {
                title: "Backups"
                subtitle: "A copy of the vault is kept before saves (at most one every " + kl.settings.backupIntervalMinutes + " minutes, and always before applying changes from another device)."
                Row2 {
                    label: "Backups to keep"
                    KField { id: bk; Layout.fillWidth: false; Layout.preferredWidth: th.s(80); text: String(kl.settings.backupKeep); input.validator: IntValidator { bottom: 1; top: 500 }
                         onAccepted: st.apply({ backupKeep: parseInt(text) }) }
                    KButton { text: "Apply"; kind: "ghost"; visible: bk.text !== String(kl.settings.backupKeep); onClicked: st.apply({ backupKeep: parseInt(bk.text) }) }
                }
                KText { text: st.backupList.length ? st.backupList.length + " backup(s) in " + kl.settings.statePath : "No backups yet."; color: th.muted; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: th.base - 1 }
                Repeater {
                    model: st.backupList.slice(0, 5)
                    RowLayout {
                        required property var modelData
                        Glyph { g: "\uf1c0"; color: th.muted }
                        KText { text: Qt.formatDateTime(new Date(modelData.time), "yyyy-MM-dd HH:mm:ss") + "  ·  " + Math.round(modelData.size / 1024) + " KiB"; Layout.fillWidth: true }
                        IconButton { glyph: "\uf0c5"; tip: "Copy path"; onClicked: kl.copyValue(modelData.path, "Backup path", function (r) {}) }
                    }
                }
            }

            Section {
                visible: kl.unlocked
                title: "Merge from another file"
                subtitle: "Bring in changes from a KDBX copy (for example a conflicted sync copy). Conflicts are shown before anything is saved."
                KField {
                    id: mPath; Layout.fillWidth: true; label: "File"
                    IconButton { glyph: "\uf07c"; tip: "Browse…"; onClicked: mergeDlg.open() }
                }
                RowLayout {
                    Layout.fillWidth: true
                    KField { id: mPw; Layout.fillWidth: true; Layout.preferredWidth: 1; label: "Its password (if different)"; secret: true }
                    KField {
                        id: mKey; Layout.fillWidth: true; Layout.preferredWidth: 1; label: "Its key file (optional)"
                        IconButton { glyph: "\uf07c"; tip: "Browse…"; onClicked: { keyDlg.target = mKey; keyDlg.open() } }
                    }
                }
                RowLayout {
                    Item { Layout.fillWidth: true }
                    KButton {
                        text: "Merge"; kind: "primary"; enabled: mPath.text !== "" && !kl.busy
                        onClicked: {
                            const pw = mPw.text; mPw.text = ""
                            kl.mergeFile(mPath.text, pw, mKey.text, function (r) {
                                if (!r.ok) { st.message("error", r.message); return }
                                st.message(kl.conflictCount ? "warning" : "success", kl.conflictCount ? "Merged with " + kl.conflictCount + " conflict(s) to resolve." : "Merged and saved.")
                            })
                        }
                    }
                }
            }

            Section {
                visible: kl.unlocked
                title: "Change master credentials"
                KField { id: cCur; Layout.fillWidth: true; label: "Current password"; secret: true }
                KField {
                    id: cCurKey; Layout.fillWidth: true; label: "Current key file"; text: kl.settings.keyfilePath; placeholder: "none"
                    IconButton { glyph: "\uf07c"; tip: "Browse…"; onClicked: { keyDlg.target = cCurKey; keyDlg.open() } }
                }
                RowLayout {
                    Layout.fillWidth: true
                    KField { id: cNew; Layout.fillWidth: true; Layout.preferredWidth: 1; label: "New password"; secret: true }
                    KField { id: cNew2; Layout.fillWidth: true; Layout.preferredWidth: 1; label: "Repeat new password"; secret: true; error: cNew2.text && cNew.text !== cNew2.text ? "Does not match" : "" }
                }
                KField {
                    id: cNewKey; Layout.fillWidth: true; label: "New key file (optional)"; placeholder: "none"
                    IconButton { glyph: "\uf07c"; tip: "Browse…"; onClicked: { keyDlg.target = cNewKey; keyDlg.open() } }
                }
                KText { text: "Other devices will need the new credentials to open the synced vault."; color: th.muted; font.pixelSize: th.base - 1 }
                RowLayout {
                    Item { Layout.fillWidth: true }
                    KButton {
                        text: "Change credentials"; kind: "danger"
                        enabled: (cNew.text !== "" || cNewKey.text !== "") && cNew.text === cNew2.text
                        onClicked: {
                            const a = cCur.text, b = cNew.text
                            cCur.text = ""; cNew.text = ""; cNew2.text = ""
                            kl.changeCredentials(a, cCurKey.text, b, cNewKey.text, function (r) {
                                st.message(r.ok ? "success" : "error", r.ok ? "Master credentials changed." : r.message)
                            })
                        }
                    }
                }
            }

            Section {
                title: "About"
                KText { text: "Config: " + kl.settings.configPath; color: th.muted; font.pixelSize: th.base - 1; Layout.fillWidth: true; wrapMode: Text.WrapAnywhere }
                KText { text: "State & backups: " + kl.settings.statePath; color: th.muted; font.pixelSize: th.base - 1; Layout.fillWidth: true; wrapMode: Text.WrapAnywhere }
                KText { text: "Passphrase word list: EFF Large Wordlist (CC BY 3.0)."; color: th.muted; font.pixelSize: th.base - 1 }
            }
            Item { implicitHeight: th.s(30) }
        }
    }
}
