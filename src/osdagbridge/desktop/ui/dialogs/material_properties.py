from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QComboBox, QCheckBox, QStackedWidget
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QDoubleValidator

from osdagbridge.desktop.ui.utils.custom_titlebar import CustomTitleBar
from osdagbridge.desktop.ui.docks.dock_utils import apply_field_style
from osdagbridge.core.bridge_types.plate_girder.ui_fields import VALUES_MATERIAL, VALUES_DECK_CONCRETE_GRADE


DIALOG_TITLE_MATERIAL_PROPERTIES = "Material Properties" #ui need to be generalized

MEMBER_OPTION_GIRDER = "Girder"
MEMBER_OPTION_CROSS_BRACING = "Cross Bracing"
MEMBER_OPTION_END_DIAPHRAGM = "End Diaphragm"
MEMBER_OPTION_DECK = "Deck"

MEMBER_OPTIONS = [
    MEMBER_OPTION_GIRDER,
    MEMBER_OPTION_CROSS_BRACING,
    MEMBER_OPTION_END_DIAPHRAGM,
    MEMBER_OPTION_DECK
]

MATPROP_LABEL_MEMBER = "Member"
MATPROP_LABEL_MATERIAL = "Material"
MATPROP_LABEL_DEFAULT = "Default"

ECM_FACTOR_OPTION_QUARTZITE = "Quartzite/granite aggregates = 1"
ECM_FACTOR_OPTION_LIMESTONE = "Limestone aggregates = 0.9"
ECM_FACTOR_OPTION_SANDSTONE = "Sandstone aggregates = 0.7"
ECM_FACTOR_OPTION_BASALT = "Basalt aggregates = 1.2"
ECM_FACTOR_OPTION_CUSTOM = "Custom"

ECM_FACTOR_OPTIONS = [
    (ECM_FACTOR_OPTION_QUARTZITE, 1.0),
    (ECM_FACTOR_OPTION_LIMESTONE, 0.9),
    (ECM_FACTOR_OPTION_SANDSTONE, 0.7),
    (ECM_FACTOR_OPTION_BASALT, 1.2),
    (ECM_FACTOR_OPTION_CUSTOM, None),
]

ECM_FACTOR_LABELS = [text for text, _ in ECM_FACTOR_OPTIONS]
DEFAULT_ECM_FACTOR_LABEL = ECM_FACTOR_OPTIONS[0][0]
CUSTOM_ECM_FACTOR_LABEL = ECM_FACTOR_OPTION_CUSTOM

PLACEHOLDER_CUSTOM_FACTOR = "Custom factor"
INCLUDE_MEDIAN_OPTION_NO = "No"
INCLUDE_MEDIAN_OPTION_YES = "Yes"

STEEL_MODULUS_E_GPA = 200.0
STEEL_MODULUS_G_GPA = 77.0
STEEL_POISSON_RATIO = 0.30
STEEL_THERMAL_COEFF = 11.7

STEEL_GRADE_BASE_VALUES = {
    250: {"Fy": 250, "Fu": 410},
    275: {"Fy": 275, "Fu": 430},
    300: {"Fy": 300, "Fu": 440},
    350: {"Fy": 350, "Fu": 490},
    410: {"Fy": 410, "Fu": 540},
    450: {"Fy": 450, "Fu": 570},
    550: {"Fy": 550, "Fu": 650},
    600: {"Fy": 600, "Fu": 700},
    650: {"Fy": 650, "Fu": 750},
}

CONCRETE_GRADE_BASE_VALUES = {
    "M20": {"fck": 20.0, "fctm": 2.2, "Ecm": 22.0},
    "M25": {"fck": 25.0, "fctm": 2.6, "Ecm": 25.0},
    "M30": {"fck": 30.0, "fctm": 2.9, "Ecm": 30.0},
    "M35": {"fck": 35.0, "fctm": 3.2, "Ecm": 33.0},
    "M40": {"fck": 40.0, "fctm": 3.5, "Ecm": 34.0},
}

KEY_CONCRETE_FCK = "Characteristic Compressive (Cube) Strength of Concrete, (fck)cu (MPa)"
KEY_CONCRETE_FCTM = "Mean Tensile Strength of Concrete, fctm (MPa)"
KEY_CONCRETE_ECM = "Secant Modulus of Elasticity of Concrete, Ecm (GPa)"
KEY_ECM_FACTOR = "Ecm Multiplication Factor"
KEY_THERMAL_EXPANSION = "Thermal Expansion Coefficient, (×10⁻⁶/°C)"
KEY_STEEL_FU = "Ultimate Tensile Strength, Fu (MPa)"
KEY_STEEL_FY = "Yield Strength, Fy (MPa)"
KEY_STEEL_E = "Modulus of Elasticity, E (GPa)"
KEY_STEEL_G = "Modulus of Rigidity, G (GPa)"
KEY_STEEL_POISSON = "Poisson's Ratio, ν"

DISP_CONCRETE_FCK = "Characteristic Compressive (Cube) Strength of Concrete, f<sub>ck</sub> (MPa)"
DISP_CONCRETE_FCTM = "Mean Tensile Strength of Concrete, f<sub>ctm</sub> (MPa)"
DISP_CONCRETE_ECM = "Secant Modulus of Elasticity of Concrete, E<sub>cm</sub> (GPa)"
DISP_ECM_FACTOR = "E<sub>cm</sub> Multiplication Factor"
DISP_THERMAL_EXPANSION = "Thermal Expansion Coefficient, (&times;10<sup>&minus;6</sup>/°C)"
DISP_STEEL_FU = "Ultimate Tensile Strength, F<sub>u</sub> (MPa)"
DISP_STEEL_FY = "Yield Strength, F<sub>y</sub> (MPa)"
DISP_STEEL_E = "Modulus of Elasticity, E (GPa)"
DISP_STEEL_G = "Modulus of Rigidity, G (GPa)"
DISP_STEEL_POISSON = "Poisson&apos;s Ratio, &nu;"


TYPE_TEXTBOX = 'textbox'
TYPE_COMBOBOX = 'combobox'

def material_properties_values():
    """Return list of material property fields"""
    steel_props = []
    t1 = (KEY_STEEL_FU, DISP_STEEL_FU, TYPE_TEXTBOX, None, True, 'Double Validator')
    steel_props.append(t1)

    t2 = (KEY_STEEL_FY, DISP_STEEL_FY, TYPE_TEXTBOX, None, True, 'Double Validator')
    steel_props.append(t2)

    t3 = (KEY_STEEL_E, DISP_STEEL_E, TYPE_TEXTBOX, None, True, 'Double Validator')
    steel_props.append(t3)

    t4 = (KEY_STEEL_G, DISP_STEEL_G, TYPE_TEXTBOX, None, True, 'Double Validator')
    steel_props.append(t4)

    t5 = (KEY_STEEL_POISSON, DISP_STEEL_POISSON, TYPE_TEXTBOX, None, True, 'Double Validator')
    steel_props.append(t5)

    t6 = (KEY_THERMAL_EXPANSION, DISP_THERMAL_EXPANSION, TYPE_TEXTBOX, None, True, 'Double Validator')
    steel_props.append(t6)

    deck_props = []
    t7 = (KEY_CONCRETE_FCK, DISP_CONCRETE_FCK, TYPE_TEXTBOX, None, True, 'Double Validator')
    deck_props.append(t7)

    t8 = (KEY_CONCRETE_FCTM, DISP_CONCRETE_FCTM, TYPE_TEXTBOX, None, True, 'Double Validator')
    deck_props.append(t8)

    t9 = (KEY_CONCRETE_ECM, DISP_CONCRETE_ECM, TYPE_TEXTBOX, None, True, 'Double Validator')
    deck_props.append(t9)

    t10 = (KEY_ECM_FACTOR, DISP_ECM_FACTOR, TYPE_COMBOBOX, ECM_FACTOR_LABELS, True, 'No Validator')
    deck_props.append(t10)

    t11 = (KEY_THERMAL_EXPANSION, DISP_THERMAL_EXPANSION, TYPE_TEXTBOX, None, True, 'Double Validator')
    deck_props.append(t11)

    return {
        'steel': steel_props,
        'deck': deck_props
    }

class NoScrollComboBox(QComboBox):
    def wheelEvent(self, event):
        event.ignore()

class MaterialPropertiesDialog(QDialog):
    MEMBER_OPTIONS = MEMBER_OPTIONS
    STEEL_MEMBERS = {MEMBER_OPTION_GIRDER, MEMBER_OPTION_CROSS_BRACING, MEMBER_OPTION_END_DIAPHRAGM}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(DIALOG_TITLE_MATERIAL_PROPERTIES)
        self.setMinimumWidth(580)
        self.setStyleSheet("""
        QDialog {
            background-color: white;
            border: 1px solid #90AF13;
        }
    """)

        self.parent_dock = parent
        self._loading = False
        self.current_member = None
        self.member_data = {}

        self.material_props = material_properties_values()
        self.steel_fields = self.material_props['steel']
        self.deck_fields = self.material_props['deck']

        self.member_combo = NoScrollComboBox()
        self.member_combo.addItems(self.MEMBER_OPTIONS)
        apply_field_style(self.member_combo)

        self.material_combo = NoScrollComboBox()
        apply_field_style(self.material_combo)
        self.setupWrapper()
        main_layout = QVBoxLayout(self.content_widget)
        main_layout.setContentsMargins(20, 16, 20, 16)

        form_container = QWidget()
        form_layout = QVBoxLayout(form_container)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(12)
        
        member_row = QHBoxLayout()
        member_row.setContentsMargins(0, 0, 0, 0)
        member_row.setSpacing(18)
        member_label = QLabel(MATPROP_LABEL_MEMBER)
        member_label.setStyleSheet("font-size: 12px; color: #2d2d2d;")
        member_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        member_label.setFixedWidth(280)
        self.member_combo.setFixedWidth(242)
        member_row.addWidget(member_label)
        member_row.addWidget(self.member_combo)
        member_row.addStretch()
        form_layout.addLayout(member_row)
        
        material_row = QHBoxLayout()
        material_row.setContentsMargins(0, 0, 0, 0)
        material_row.setSpacing(18)
        material_label = QLabel(MATPROP_LABEL_MATERIAL)
        material_label.setStyleSheet("font-size: 12px; color: #2d2d2d;")
        material_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        material_label.setFixedWidth(280)
        self.material_combo.setFixedWidth(242)
        material_row.addWidget(material_label)
        material_row.addWidget(self.material_combo)
        material_row.addStretch()
        form_layout.addLayout(material_row)
        
        main_layout.addWidget(form_container)

        self.stack = QStackedWidget()
        self.stack.setContentsMargins(0, 0, 0, 0)
        self.steel_page = self._build_steel_form()
        self.deck_page = self._build_deck_form()
        self.stack.addWidget(self.steel_page)
        self.stack.addWidget(self.deck_page)
        main_layout.addWidget(self.stack)

        default_row = QHBoxLayout()
        default_row.setContentsMargins(0, 0, 0, 0)
        default_row.setSpacing(18)
        default_label = QLabel(MATPROP_LABEL_DEFAULT)
        default_label.setStyleSheet("font-size: 12px; color: #2d2d2d;")
        default_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        default_label.setFixedWidth(280)
        self.default_checkbox = QCheckBox()
     
        checkbox_container = QWidget()
        checkbox_layout = QHBoxLayout(checkbox_container)
        checkbox_layout.setContentsMargins(0, 0, 0, 0)
        checkbox_layout.setSpacing(0)
        checkbox_layout.addWidget(self.default_checkbox)
        checkbox_layout.addStretch()
        
        default_row.addWidget(default_label)
        default_row.addWidget(checkbox_container)
        main_layout.addLayout(default_row)

        self.member_combo.currentTextChanged.connect(self._on_member_changed)
        self.material_combo.currentTextChanged.connect(self._on_material_changed)
        self.default_checkbox.stateChanged.connect(self._on_default_toggled)

        self._initialize_member_data()
        self._on_member_changed(self.member_combo.currentText())
        self.setFixedSize(self.sizeHint())

    def setupWrapper(self):
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowSystemMenuHint)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(1, 1, 1, 1)
        main_layout.setSpacing(0)
        
        self.title_bar = CustomTitleBar()
        self.title_bar.setTitle(DIALOG_TITLE_MATERIAL_PROPERTIES)
        main_layout.addWidget(self.title_bar)
        
        self.content_widget = QWidget(self)
        main_layout.addWidget(self.content_widget, 1)

    def closeEvent(self, event):
        self._save_current_member_form()
        super().closeEvent(event)

    def _build_steel_form(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.steel_field_inputs = {}
        
        for field_tuple in self.steel_fields:
            key, display_label, widget_type, values, required, validator = field_tuple
            
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(18)
            
            label = QLabel(display_label)
            label.setTextFormat(Qt.RichText)
            label.setStyleSheet("font-size: 12px; color: #2d2d2d;")
            label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            label.setFixedWidth(280)
            label.setWordWrap(True)
            
            line_edit = QLineEdit()
            line_edit.setFixedWidth(242)
            apply_field_style(line_edit)
            line_edit.setValidator(QDoubleValidator(0.0, 99999.0, 1))
            line_edit.textEdited.connect(self._handle_user_override)
            self.steel_field_inputs[key] = line_edit
            
            row.addWidget(label)
            row.addWidget(line_edit)
            row.addStretch()
            layout.addLayout(row)
            
        layout.addStretch()
        return widget

    def _build_deck_form(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.deck_field_inputs = {}
        
        for field_tuple in self.deck_fields:
            key, display_label, widget_type, values, required, validator = field_tuple
            
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(18)
            
            label = QLabel(display_label)
            label.setTextFormat(Qt.RichText)
            label.setStyleSheet("font-size: 12px; color: #2d2d2d;")
            label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            label.setFixedWidth(280)
            label.setWordWrap(True)
            
            if key == KEY_ECM_FACTOR:
                self.deck_factor_combo = NoScrollComboBox()
                self.deck_factor_combo.addItems(values)
                self.deck_factor_combo.setFixedWidth(242)
                apply_field_style(self.deck_factor_combo)
                self.deck_factor_combo.currentTextChanged.connect(self._on_factor_changed)

                self.deck_factor_custom_input = QLineEdit()
                apply_field_style(self.deck_factor_custom_input)
                self.deck_factor_custom_input.setPlaceholderText(PLACEHOLDER_CUSTOM_FACTOR)
                self.deck_factor_custom_input.setFixedWidth(242)
                self.deck_factor_custom_input.setVisible(False)
                self.deck_factor_custom_input.setEnabled(False)
                self.deck_factor_custom_input.setValidator(QDoubleValidator(0.1, 5.0, 1))
                self.deck_factor_custom_input.textEdited.connect(self._handle_user_override)

                row.addWidget(label)
                row.addWidget(self.deck_factor_combo)
                row.addStretch()
                
                self.deck_factor_custom_container = QWidget()
                custom_layout = QHBoxLayout(self.deck_factor_custom_container)
                custom_layout.setContentsMargins(0, 0, 0, 0)
                custom_layout.setSpacing(18)

                custom_label = QLabel("")
                custom_label.setFixedWidth(280) 
                custom_layout.addWidget(custom_label)
                custom_layout.addWidget(self.deck_factor_custom_input)
                custom_layout.addStretch()

                self.deck_factor_custom_container.setVisible(False)
                layout.addWidget(self.deck_factor_custom_container)
                
                self.deck_field_inputs[key] = self.deck_factor_combo
            else:
                line_edit = QLineEdit()
                line_edit.setFixedWidth(242)
                apply_field_style(line_edit)
                line_edit.setValidator(QDoubleValidator(0.0, 99999.0, 1))
                line_edit.textEdited.connect(self._handle_user_override)
                row.addWidget(label)
                row.addWidget(line_edit)
                row.addStretch()
                self.deck_field_inputs[key] = line_edit
                
            layout.addLayout(row)
            
        layout.addStretch()
        return widget

    def _initialize_member_data(self):
        for member in self.MEMBER_OPTIONS:
            material = self._get_parent_grade(member)
            fields = self._default_fields_for_member(member, material)
            self.member_data[member] = {
                "material": material,
                "fields": fields,
                "is_default": False if member == MEMBER_OPTION_DECK else True,
                "factor_label": DEFAULT_ECM_FACTOR_LABEL if member == MEMBER_OPTION_DECK else None,
                "custom_factor": "1.0" if member == MEMBER_OPTION_DECK else None,
            }

    def _default_fields_for_member(self, member, material=None, factor_label=None, custom_factor=None):
        if member == MEMBER_OPTION_DECK:
            grade = material or self._get_parent_grade(member) or (VALUES_DECK_CONCRETE_GRADE[0] if VALUES_DECK_CONCRETE_GRADE else "")
            factor_label = factor_label or DEFAULT_ECM_FACTOR_LABEL
            factor_value = self._factor_value_from_label(factor_label, custom_factor)
            return self._deck_defaults(grade, factor_value)
        grade = material or self._get_parent_grade(member) or (VALUES_MATERIAL[0] if VALUES_MATERIAL else "")
        return self._steel_defaults(grade)

    def _steel_defaults(self, grade):
        grade_value = self._extract_numeric_grade(grade)
        defaults = STEEL_GRADE_BASE_VALUES.get(grade_value, STEEL_GRADE_BASE_VALUES[250])
        
        result = {}
        for field_tuple in self.steel_fields:
            key = field_tuple[0]
            if "Fu" in key:
                result[key] = "{:.1f}".format(defaults["Fu"])
            elif "Fy" in key:
                result[key] = "{:.1f}".format(defaults["Fy"])
            elif "E (GPa)" in key and "Rigidity" not in key:
                result[key] = "{:.1f}".format(STEEL_MODULUS_E_GPA)
            elif "G (GPa)" in key:
                result[key] = "{:.1f}".format(STEEL_MODULUS_G_GPA)
            elif "Poisson" in key:
                result[key] = "{:.1f}".format(STEEL_POISSON_RATIO)
            elif "Thermal" in key:
                result[key] = "{:.1f}".format(STEEL_THERMAL_COEFF)
        return result
    
    def _get_concrete_from_code(self, grade):
        grade = grade.replace(" ", "").upper()
        return CONCRETE_GRADE_BASE_VALUES.get(grade)

    def _deck_defaults(self, grade, factor_value):
        data = self._get_concrete_from_code(grade)

        if not data:
            return {}

        fck = data["fck"]
        fctm = data["fctm"]
        ecm = round(data["Ecm"] * factor_value, 1)

        result = {}
        for field_tuple in self.deck_fields:
            key = field_tuple[0]
            if "fck" in key:
                result[key] = "{:.1f}".format(fck)
            elif "fctm" in key:
                result[key] = "{:.1f}".format(fctm)
            elif "Ecm (GPa)" in key:
                result[key] = "{:.1f}".format(ecm)
            elif "Thermal" in key:
                result[key] = "11.7"
        return result
    
    def _extract_numeric_grade(self, grade, default=250):
        digits = ''.join(ch for ch in grade if ch.isdigit())
        try:
            return int(digits) if digits else default
        except ValueError:
            return default

    def _materials_for_member(self, member):
        return VALUES_DECK_CONCRETE_GRADE if member == MEMBER_OPTION_DECK else VALUES_MATERIAL

    def _on_member_changed(self, member):
        if self.current_member:
            self._save_current_member_form()

        self.current_member = member
        is_deck = member == MEMBER_OPTION_DECK
        self.stack.setCurrentWidget(self.deck_page if is_deck else self.steel_page)

        data = self.member_data.get(member)
        if not data:
            self.member_data[member] = self._create_default_entry(member)
            data = self.member_data[member]

        materials = self._materials_for_member(member)
        self._loading = True

        self.material_combo.clear()
        self.material_combo.addItems(materials)
        if data["material"] in materials:
            self.material_combo.setCurrentText(data["material"])
        elif materials:
            self.material_combo.setCurrentIndex(0)
            data["material"] = self.material_combo.currentText()

        if data.get("is_default") and not self._loading:
            self._apply_defaults_for_member(member, update_ui=True)
        else:
            if is_deck:
                self._populate_deck_fields(data)
            else:
                self._populate_steel_fields(data)
            
        self._loading = False

    def _populate_steel_fields(self, data):
        for label, widget in self.steel_field_inputs.items():
            value = data["fields"].get(label, "")
            try:
                formatted_value = "{:.1f}".format(float(value))
                widget.setText(formatted_value)
            except (ValueError, TypeError):
                widget.setText(value)

    def _populate_deck_fields(self, data):
        for label, widget in self.deck_field_inputs.items():
            if label == KEY_ECM_FACTOR:
                factor_label = data.get("factor_label", DEFAULT_ECM_FACTOR_LABEL)
                if factor_label not in ECM_FACTOR_LABELS:
                    factor_label = DEFAULT_ECM_FACTOR_LABEL
                self.deck_factor_combo.blockSignals(True)
                self.deck_factor_combo.setCurrentText(factor_label)
                self.deck_factor_combo.blockSignals(False)
                self._update_custom_factor_visibility(factor_label)
                self.deck_factor_custom_input.blockSignals(True)
                custom_val = data.get("custom_factor", "1.0")
                try:
                    formatted_custom = "{:.1f}".format(float(custom_val))
                    self.deck_factor_custom_input.setText(formatted_custom)
                except (ValueError, TypeError):
                    self.deck_factor_custom_input.setText(custom_val)
                self.deck_factor_custom_input.blockSignals(False)
            else:
                value = data["fields"].get(label, "")
                try:
                    formatted_value = "{:.1f}".format(float(value))
                    widget.setText(formatted_value)
                except (ValueError, TypeError):
                    widget.setText(value)

        for label, widget in self.deck_field_inputs.items():
            if isinstance(widget, QLineEdit):
                widget.setReadOnly(False)
                widget.setEnabled(True)

    def _save_current_member_form(self):
        if not self.current_member:
            return
        data = self.member_data.setdefault(self.current_member, self._create_default_entry(self.current_member))
        data["material"] = self.material_combo.currentText()
        if self.current_member == MEMBER_OPTION_DECK:
            for label, widget in self.deck_field_inputs.items():
                if label == KEY_ECM_FACTOR:
                    data["factor_label"] = self.deck_factor_combo.currentText()
                    data["custom_factor"] = self.deck_factor_custom_input.text() or "1.0"
                else:
                    data["fields"][label] = widget.text()
            factor_value = self._factor_value_from_label(data["factor_label"], data.get("custom_factor"))
            data["fields"][KEY_ECM_FACTOR] = "{:.1f}".format(factor_value)
        else:
            for label, widget in self.steel_field_inputs.items():
                data["fields"][label] = widget.text()
        data["is_default"] = self.default_checkbox.isChecked()

    def _create_default_entry(self, member):
        material = self._get_parent_grade(member)
        return {
            "material": material,
            "fields": self._default_fields_for_member(member, material),
            "is_default": True,
            "factor_label": DEFAULT_ECM_FACTOR_LABEL if member == MEMBER_OPTION_DECK else None,
            "custom_factor": "1.0" if member == MEMBER_OPTION_DECK else None,
        }

    def _apply_defaults_for_member(self, member, update_ui=True):
        data = self.member_data.setdefault(member, self._create_default_entry(member))

        grade = data.get("material") or self._get_parent_grade(member)
        data["material"] = grade

        if member == MEMBER_OPTION_DECK:
            data["factor_label"] = DEFAULT_ECM_FACTOR_LABEL
            data["custom_factor"] = "1.0"
            factor_value = self._factor_value_from_label(DEFAULT_ECM_FACTOR_LABEL)
            data["fields"] = self._deck_defaults(grade, factor_value)
        else:
            data["fields"] = self._steel_defaults(grade)
        data["is_default"] = True

        if update_ui and member == self.current_member:
            self._loading = True
            self.material_combo.setCurrentText(grade)
            if member == MEMBER_OPTION_DECK:
                self._populate_deck_fields(data)
            else:
                self._populate_steel_fields(data)
            self.default_checkbox.setChecked(True)
            self._loading = False

    def _factor_value_from_label(self, label, custom_factor=None):
        for text, value in ECM_FACTOR_OPTIONS:
            if text == label:
                if value is None:
                    try:
                        return float(custom_factor) if custom_factor else 1.0
                    except ValueError:
                        return 1.0
                return value
        return 1.0

    def _reset_current_member_to_defaults(self):
        if not self.current_member:
            return

        self._apply_defaults_for_member(self.current_member, update_ui=False)
        data = self.member_data.get(self.current_member)
        if not data:
            return

        target_material = data.get("material", "")
        self._loading = True
        if target_material:
            index = self.material_combo.findText(target_material)
            if index >= 0:
                self.material_combo.setCurrentIndex(index)
            elif self.material_combo.count() > 0:
                self.material_combo.setCurrentIndex(0)
                data["material"] = self.material_combo.currentText()
        if self.current_member == MEMBER_OPTION_DECK:
            self._populate_deck_fields(data)
        else:
            self._populate_steel_fields(data)
        self._loading = False

        self.default_checkbox.blockSignals(True)
        self.default_checkbox.setChecked(True)
        self.default_checkbox.blockSignals(False)
        self._save_current_member_form()

    def _update_custom_factor_visibility(self, label):
        is_custom = label == CUSTOM_ECM_FACTOR_LABEL
        self.deck_factor_custom_container.setVisible(is_custom)
        self.deck_factor_custom_input.setEnabled(is_custom)

    def _on_material_changed(self, material):
        if self._loading:
            return

        data = self.member_data.get(self.current_member)
        if not data:
            return
        
        data["material"] = material

        if self.current_member == MEMBER_OPTION_DECK:
            factor_value = self._factor_value_from_label(
                data.get("factor_label", DEFAULT_ECM_FACTOR_LABEL),
                data.get("custom_factor")
            )
            data["fields"] = self._deck_defaults(material, factor_value)
            self._populate_deck_fields(data)
        else:
            data["fields"] = self._steel_defaults(material)
            self._populate_steel_fields(data)

        data["is_default"] = True
        self.default_checkbox.blockSignals(True)
        self.default_checkbox.setChecked(True)
        self.default_checkbox.blockSignals(False)

    def _on_default_toggled(self, state):
        if self._loading:
            return
        try:
            check_state = Qt.CheckState(state)
        except ValueError:
            check_state = Qt.CheckState.Checked if bool(state) else Qt.CheckState.Unchecked
        if check_state == Qt.CheckState.Checked:
            self._reset_current_member_to_defaults()
        else:
            data = self.member_data.get(self.current_member)
            if data:
                data["is_default"] = False

    def _on_factor_changed(self, label):
        self._update_custom_factor_visibility(label)
        self._handle_user_override()

    def _handle_user_override(self):
        if self._loading:
            return
        if self.default_checkbox.isChecked():
            self._loading = True
            self.default_checkbox.setChecked(False)
            self._loading = False
        data = self.member_data.get(self.current_member)
        if data:
            data["is_default"] = False
        self._save_current_member_form()

    def _get_parent_grade(self, member):
        parent = self.parent_dock
        if not parent:
            return ""
        mapping = {
            MEMBER_OPTION_GIRDER: getattr(parent, "girder_combo", None),
            MEMBER_OPTION_CROSS_BRACING: getattr(parent, "cross_bracing_combo", None),
            MEMBER_OPTION_END_DIAPHRAGM: getattr(parent, "end_diaphragm_combo", None),
            MEMBER_OPTION_DECK: getattr(parent, "deck_combo", None),
        }
        combo = mapping.get(member)
        return combo.currentText() if combo else ""

    def set_member(self, member):
        index = self.member_combo.findText(member)
        if index >= 0:
            self.member_combo.setCurrentIndex(index)

    def sync_with_parent_defaults(self):
        for member, data in self.member_data.items():
            if data.get("is_default"):
                self._apply_defaults_for_member(member, update_ui=(member == self.current_member))