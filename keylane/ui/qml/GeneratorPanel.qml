import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Password/passphrase generator settings with a live preview.
ColumnLayout {
    id: gp
    property var spec: Object.assign({}, kl.generatorSpec)
    property string value: ""
    property int bits: 0
    property string error: ""
    signal use(string value)
    spacing: th.s(10)

    function regenerate() {
        const r = kl.generate(spec)
        if (r.ok) { value = r.value; bits = r.bits; error = "" }
        else { value = ""; bits = 0; error = r.message }
    }
    function set(k, v) { const s = Object.assign({}, spec); s[k] = v; spec = s; regenerate() }
    Component.onCompleted: regenerate()

    RowLayout {
        spacing: th.s(6)
        KButton { text: "Password"; kind: spec.mode === "password" ? "primary" : "normal"; onClicked: gp.set("mode", "password") }
        KButton { text: "Passphrase"; kind: spec.mode === "passphrase" ? "primary" : "normal"; onClicked: gp.set("mode", "passphrase") }
        Item { Layout.fillWidth: true }
        KText { text: gp.bits ? "≈ " + gp.bits + " bits" : ""; color: gp.bits >= 80 ? th.success : gp.bits >= 60 ? th.warning : th.danger }
    }
    Rectangle {
        Layout.fillWidth: true
        implicitHeight: prev.implicitHeight + th.s(16)
        radius: th.radius
        color: th.faint
        border.color: th.hairline
        RowLayout {
            anchors.fill: parent
            anchors.margins: th.s(8)
            TextEdit {
                id: prev
                Layout.fillWidth: true
                readOnly: true
                selectByMouse: true
                wrapMode: TextEdit.WrapAnywhere
                text: gp.value
                color: th.fg
                font.family: th.fontFamily
                font.pixelSize: th.base + 2
                Accessible.name: "Generated value"
            }
            IconButton { glyph: "\uf021"; tip: "Regenerate"; onClicked: gp.regenerate() }
        }
    }
    Banner { Layout.fillWidth: true; kind: "error"; text: gp.error }

    GridLayout {
        Layout.fillWidth: true
        columns: 2
        columnSpacing: th.s(16)
        rowSpacing: th.s(6)
        visible: spec.mode === "password"
        KText { text: "Length " + spec.length; color: th.muted }
        KSlider {
            id: len
            Layout.fillWidth: true
            from: 8; to: 128; stepSize: 1
            value: spec.length
            onMoved: gp.set("length", Math.round(value))
            Accessible.name: "Length"
        }
        Flow {
            Layout.columnSpan: 2
            Layout.fillWidth: true
            spacing: th.s(6)
            KSwitch { width: th.s(110); text: "a–z"; checked: spec.lower; onToggled: gp.set("lower", checked) }
            KSwitch { width: th.s(110); text: "A–Z"; checked: spec.upper; onToggled: gp.set("upper", checked) }
            KSwitch { width: th.s(110); text: "0–9"; checked: spec.digits; onToggled: gp.set("digits", checked) }
            KSwitch { width: th.s(110); text: "Symbols"; checked: spec.symbols; onToggled: gp.set("symbols", checked) }
            KSwitch { text: "Avoid look-alikes (Il1O0)"; checked: spec.exclude_ambiguous; onToggled: gp.set("exclude_ambiguous", checked) }
            KSwitch { text: "At least one of each"; checked: spec.require_each; onToggled: gp.set("require_each", checked) }
        }
        KText { text: "Symbol set"; color: th.muted }
        KField { Layout.fillWidth: true; text: spec.symbol_set; onEdited: gp.set("symbol_set", text) }
        KText { text: "Exclude"; color: th.muted }
        KField { Layout.fillWidth: true; text: spec.exclude_chars; placeholder: "characters to never use"; onEdited: gp.set("exclude_chars", text) }
    }
    GridLayout {
        Layout.fillWidth: true
        columns: 2
        columnSpacing: th.s(16)
        rowSpacing: th.s(6)
        visible: spec.mode === "passphrase"
        KText { text: "Words " + spec.words; color: th.muted }
        KSlider {
            Layout.fillWidth: true
            from: 3; to: 20; stepSize: 1
            value: spec.words
            onMoved: gp.set("words", Math.round(value))
            Accessible.name: "Words"
        }
        KText { text: "Separator"; color: th.muted }
        KField { Layout.fillWidth: true; text: spec.separator; onEdited: gp.set("separator", text) }
        Flow {
            Layout.columnSpan: 2
            Layout.fillWidth: true
            spacing: th.s(16)
            KSwitch { text: "Capitalize"; checked: spec.capitalize; onToggled: gp.set("capitalize", checked) }
            KSwitch { text: "Add a digit"; checked: spec.add_number; onToggled: gp.set("add_number", checked) }
        }
    }
    RowLayout {
        spacing: th.s(8)
        KButton {
            text: "Save as default"
            kind: "ghost"
            onClicked: {
                const r = kl.saveGeneratorSpec(gp.spec)
                gp.error = r.ok ? "" : r.message
            }
        }
        Item { Layout.fillWidth: true }
        KButton { text: "Use"; kind: "primary"; enabled: gp.value !== ""; onClicked: gp.use(gp.value) }
    }
}
