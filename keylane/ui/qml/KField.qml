import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Labelled single-line input with optional trailing actions.
ColumnLayout {
    id: f
    property alias text: input.text
    property alias input: input
    property string label: ""
    property string placeholder: ""
    property bool secret: false
    property bool revealed: false
    property bool readOnly: false
    property bool mono: false
    property string error: ""
    // Optional function(event) run before the TextInput's own key handling, so
    // app shortcuts win over line-editing bindings such as Ctrl+E / Ctrl+K.
    property var keyHandler: null
    default property alias actions: trailing.data
    signal accepted()
    signal edited()
    spacing: th.s(4)
    function focusField() { input.forceActiveFocus(); input.selectAll() }

    KText {
        visible: f.label !== ""
        text: f.label
        font.pixelSize: th.base - 1
        color: input.activeFocus ? th.fg : th.muted
    }
    Rectangle {
        Layout.fillWidth: true
        implicitHeight: th.s(32)
        radius: th.radius
        color: f.readOnly ? "transparent" : th.faint
        border.width: input.activeFocus ? 2 : 1
        border.color: f.error ? th.danger : input.activeFocus ? th.accent : th.hairline
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: th.s(10)
            anchors.rightMargin: th.s(4)
            spacing: th.s(2)
            TextInput {
                id: input
                Layout.fillWidth: true
                Layout.preferredWidth: th.s(60)
                Layout.minimumWidth: th.s(40)
                clip: true
                activeFocusOnTab: true
                color: th.fg
                selectionColor: th.alpha(th.accent, 0.35)
                selectedTextColor: th.fg
                font.family: th.fontFamily
                font.pixelSize: th.base + 1
                readOnly: f.readOnly
                selectByMouse: true
                echoMode: f.secret && !f.revealed ? TextInput.Password : TextInput.Normal
                passwordCharacter: "•"
                inputMethodHints: f.secret ? (Qt.ImhSensitiveData | Qt.ImhNoPredictiveText | Qt.ImhHiddenText) : Qt.ImhNone
                Accessible.name: f.label || f.placeholder
                Accessible.passwordEdit: f.secret
                onAccepted: f.accepted()
                onTextEdited: f.edited()
                Keys.onPressed: (e) => { if (f.keyHandler && (e.modifiers & Qt.ControlModifier)) f.keyHandler(e) }
                onTextChanged: if (!activeFocus) cursorPosition = 0
                KText {
                    anchors.fill: parent
                    verticalAlignment: Text.AlignVCenter
                    visible: input.text === "" && !input.preeditText
                    text: f.placeholder
                    color: th.alpha(th.muted, 0.8)
                    font.pixelSize: th.base + 1
                }
            }
            Row { id: trailing; spacing: 0 }
        }
    }
    KText {
        visible: f.error !== ""
        text: f.error
        color: th.danger
        font.pixelSize: th.base - 1
        wrapMode: Text.Wrap
        Layout.fillWidth: true
    }
}
