"""
3D CAD Viewer Window for OsdagBridge.

- Embeds CustomViewer3d
- Calls CAD generator
- Multi-select component visibility
"""

import sys
import math

from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QCheckBox,
    QPushButton,
)
from PySide6.QtCore import QTimer, Qt

from OCC.Core.Quantity import Quantity_Color, Quantity_TOC_RGB
from OCC.Core.AIS import AIS_Shape
from OCC.Core.gp import gp_Pnt
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeSphere
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
from OCC.Display.backend import load_backend

try:
    from OCC.Core.AIS import AIS_TextLabel
    _HAS_AIS_TEXT = True
except ImportError:
    _HAS_AIS_TEXT = False

# CAD generator
from osdagbridge.core.bridge_types.plate_girder.cad_generator import PlateGirderCADGenerator
from osdagbridge.core.bridge_types.plate_girder import results_data

# Custom 3D Viewer
from osdagbridge.desktop.ui.utils.custom_3dviewer import CustomViewer3d

from osdagbridge.core.bridge_types.plate_girder.dto import (
    BridgeParametersDTO,
    SectionDimsDTO,
    ISectionDimsDTO,
    ShearStudParamsDTO,
    GirderSegmentDTO,
)


# ── Z-layer constant (resolved once at import time) ───────────────────────────
try:
    from OCC.Core.Graphic3d import Graphic3d_ZLayerId_Topmost as _Z_TOP
except ImportError:
    try:
        from OCC.Core.Graphic3d import Graphic3d_ZLayerId_Top as _Z_TOP
    except ImportError:
        _Z_TOP = None

try:
    from OCC.Core.Aspect import Aspect_TODT_NORMAL as _TODT_NORMAL
except ImportError:
    try:
        from OCC.Core.Aspect import Aspect_TODT_SUBTITLE as _TODT_NORMAL
    except ImportError:
        _TODT_NORMAL = 0  # numeric fallback


class CAD3DWindow(QWidget):
    """Main 3D CAD window for OsdagBridge."""

    def __init__(self, parent=None):
        super().__init__(parent)

        self.generator = PlateGirderCADGenerator()

        # Viewer / display handles
        self.viewer = None
        self.display = None
        self._cad_init_pending = True

        # Node state — single source of truth
        # _node_data: nid -> {x, y, z (mm), label}
        self._node_data: dict = {}

        self.setup_ui()
        self.init_display()

    # ── UI SETUP ──────────────────────────────────────────────────────────────

    def setup_ui(self):
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)

        self.component_selector = BridgeComponentCheckbox(self)
        self.component_selector.hide()
        self.layout.addWidget(self.component_selector)

    # ── CAD INITIALIZATION ────────────────────────────────────────────────────

    def init_display(self):
        """Initialise the 3D viewer widget (no geometry rendered yet)."""
        load_backend("pyside6")
        self.viewer = CustomViewer3d(self)
        self.viewer.setMouseTracking(True)
        self.layout.addWidget(self.viewer)
        QTimer.singleShot(0, self._deferred_init_driver)

    def _deferred_init_driver(self):
        if not self._cad_init_pending:
            return
        self.viewer.InitDriver()
        self._cad_init_pending = False

        self.display = self.viewer._display
        self.viewer.context = self.display.Context
        self.viewer.view = self.display.View

        self.display.set_bg_gradient_color([255, 255, 255], [126, 126, 126])
        self.viewer.context.SetAutomaticHilight(False)

        if hasattr(self.viewer, "display_view_cube"):
            self.viewer.display_view_cube()

        self._create_zoom_controls()

    def _is_display_ready(self):
        return self.display is not None and not self._cad_init_pending

    # ── RENDER / CLEAR ────────────────────────────────────────────────────────

    def render_3d_cad(self, design_params: BridgeParametersDTO):
        """Generate and render the 3D bridge model. Safe to call multiple times."""
        if not self._is_display_ready():
            return
        self.generator.model_data = self.generator.generate(design_params)
        self._load_bridge()
        self.component_selector.show()

    def clear_3d_cad(self):
        """Clear the 3D model and hide the component selector."""
        if not self._is_display_ready():
            return
        if hasattr(self.viewer, "cleanup_for_new_model"):
            self.viewer.cleanup_for_new_model()
        self.display.EraseAll()
        self.display.Repaint()

        self._node_data = {}
        self.viewer.model_ais_objects = {}
        self.viewer.model_hover_labels = {}
        if hasattr(self.viewer, "deck_texture_ais"):
            self.viewer.deck_texture_ais = []
        if hasattr(self.viewer, "set_node_hover_data"):
            self.viewer.set_node_hover_data([])

        self.component_selector.hide()
        self.component_selector.reset()

    # ── BRIDGE LOADING ────────────────────────────────────────────────────────

    def _load_bridge(self):
        if not self._is_display_ready():
            return

        cad_data = self.generator.model_data
        display = self.display
        context = self.viewer.context

        if hasattr(self.viewer, "cleanup_for_new_model"):
            self.viewer.cleanup_for_new_model()
        display.EraseAll()

        # ── Colors ────────────────────────────────────────────────────────────
        C = Quantity_Color
        T = Quantity_TOC_RGB
        WEB_COLOR          = C(47/255,  47/255,  35/255,  T)
        FLANGE_COLOR       = C(134/255, 134/255, 100/255, T)
        STIFFENER_COLOR    = C(72/255,  72/255,  54/255,  T)
        DECK_COLOR         = C(100/255, 100/255, 100/255, T)
        BARRIER_COLOR      = C(40/255,  40/255,  40/255,  T)
        BRACING_COLOR      = C(60/255,  60/255,  60/255,  T)
        WBEAM_COLOR        = C(128/255, 128/255, 128/255, T)
        BARRIER_POST_COLOR = C(20/255,  20/255,  20/255,  T)
        SUPPORT_COLOR      = C(20/255,  20/255,  20/255,  T)

        # ── Helper ────────────────────────────────────────────────────────────
        def reg(shapes, key, label, color, transparency=None):
            if not shapes:
                return
            if not isinstance(shapes, list):
                shapes = [shapes]
            ais_list = []
            for shp in shapes:
                ais = display.DisplayShape(shp, color=color,
                                           transparency=transparency, update=False)
                ais = ais[0] if isinstance(ais, list) else ais
                context.Activate(ais, 0)
                ais_list.append(ais)
            self.viewer.model_ais_objects[key] = ais_list
            self.viewer.model_hover_labels[key] = label

        self.viewer.model_ais_objects = {}

        reg(cad_data.get("girder_web", []),       "Girder Web",          "Girder Web",     WEB_COLOR)
        reg(cad_data.get("girder_flanges", []),    "Girder Flange",       "Girder Flange",  FLANGE_COLOR)
        reg(cad_data.get("stiffeners", []),        "Stiffener",           "Stiffener",      STIFFENER_COLOR)
        reg(cad_data.get("shear_studs", []),       "Shear Stud",          "Shear Stud",     STIFFENER_COLOR)
        reg(cad_data.get("supports", []),          "Support",             "Support",        SUPPORT_COLOR, transparency=0.6)
        reg(cad_data.get("cross_bracings", []),    "Cross Bracing",       "Cross Bracing",  BRACING_COLOR)
        reg(cad_data.get("deck_slab"),             "Deck",                "Deck Slab",      DECK_COLOR)
        reg(cad_data.get("crash_barrier_w_beams", []), "Crash Barrier W-Beam", "W-Beam",   WBEAM_COLOR)
        reg(cad_data.get("median_w_beams", []),    "Median W-Beam",       "Median W-Beam",  WBEAM_COLOR)
        reg(cad_data.get("crash_barriers", []),    "Crash Barrier",       "Crash Barrier",  BARRIER_POST_COLOR)
        reg(cad_data.get("median_barriers", []),   "Median",              "Median Barrier", BARRIER_COLOR)
        reg(cad_data.get("railings", []),          "Railing",             "Railing",        BARRIER_COLOR)

        # Deck textures (display-only, no hover)
        self.viewer.deck_texture_ais = []
        for tex in cad_data.get("deck_textures", []):
            ais = display.DisplayShape(
                tex, color=C(0.2, 0.2, 0.2, T), update=False
            )
            ais = ais[0] if isinstance(ais, list) else ais
            self.viewer.deck_texture_ais.append(ais)

        # Nodes and grillage
        node_positions = self._render_nodes()
        self._render_grillage(node_positions)

        display.View_Iso()
        display.FitAll()
        if hasattr(self.viewer, "display_view_cube"):
            self.viewer.display_view_cube()

        self.component_selector.show()
        self.component_selector.apply_selection()

    # ── RENDER NODES ──────────────────────────────────────────────────────────

    def _render_nodes(self) -> dict:
        """
        Build node sphere markers from the live OpenSees model.
        All nodes are initially displayed but hidden immediately by
        apply_selection() unless the Node checkbox is checked.

        Returns a dict: nid -> (x_mm, y_mm, z_mm) for grillage construction.
        """
        if not self._is_display_ready():
            return {}

        try:
            nodes, members = results_data._build_nodes_members()
        except Exception:
            return {}

        if not nodes:
            return {}

        deck_top_z = self.generator.model_data.get("deck_top_z")
        if deck_top_z is None:
            return {}

        # Unit heuristic: OpenSees uses metres; CAD uses mm.
        coords = [(float(c[0]), float(c[2])) for c in nodes.values() if c]
        max_abs = max((max(abs(x), abs(z)) for x, z in coords), default=0.0)
        scale = 1000.0 if max_abs <= 500.0 else 1.0

        skew_rad = math.radians(float(getattr(self.generator, "skew_angle", 0.0) or 0.0))
        skew_tan = math.tan(skew_rad) if abs(skew_rad) > 1e-9 else 0.0

        z_vals = [z for _, z in coords]
        z_center = 0.5 * (min(z_vals) + max(z_vals)) if z_vals and min(z_vals) >= -1e-6 else 0.0

        NODE_COLOR  = Quantity_Color(0.0, 0.0, 0.0, Quantity_TOC_RGB)
        NODE_R      = 65.0   # visible sphere radius
        PICK_R      = 120.0  # larger transparent pick sphere for hover hit-testing
        z_base      = float(deck_top_z) + 2.0

        self._node_data = {}
        node_ais_list = []
        hover_nodes = []

        self.viewer.model_ais_objects.pop("Node", None)
        if hasattr(self.viewer, "set_node_hover_data"):
            self.viewer.set_node_hover_data([])
        if hasattr(self.viewer, "model_hover_labels_by_ais"):
            self.viewer.model_hover_labels_by_ais.clear()

        for nid, coord in nodes.items():
            if not coord:
                continue

            x_m, z_m = float(coord[0]), float(coord[2])
            x_mm = x_m * scale
            y_mm = (z_m - z_center) * scale
            if skew_tan:
                x_mm += y_mm * skew_tan

            label = f"Node {nid}\nX: {x_m:.2f} m\nZ: {z_m:.2f} m"

            # Visible sphere
            vis = AIS_Shape(BRepPrimAPI_MakeSphere(gp_Pnt(x_mm, y_mm, z_base), NODE_R).Shape())
            vis.SetColor(NODE_COLOR)
            if _Z_TOP is not None:
                try:
                    vis.SetZLayer(_Z_TOP)
                except Exception:
                    pass
            self.viewer.context.Display(vis, False)
            node_ais_list.append(vis)

            # Transparent pick sphere (larger hit area for hover)
            pick = AIS_Shape(BRepPrimAPI_MakeSphere(gp_Pnt(x_mm, y_mm, z_base), PICK_R).Shape())
            pick.SetColor(NODE_COLOR)
            if _Z_TOP is not None:
                try:
                    pick.SetZLayer(_Z_TOP)
                except Exception:
                    pass
            try:
                pick.SetTransparency(0.97)
            except Exception:
                pass
            self.viewer.context.Display(pick, False)
            self.viewer.context.Activate(pick, 0)
            try:
                self.viewer.context.SetSelectionSensitivity(pick, 0, 30)
            except Exception:
                pass
            if hasattr(self.viewer, "model_hover_labels_by_ais"):
                self.viewer.model_hover_labels_by_ais[pick] = label
            node_ais_list.append(pick)

            hover_nodes.append({"x": x_mm, "y": y_mm, "z": z_base, "label": label})
            self._node_data[nid] = {"x": x_mm, "y": y_mm, "z": z_base, "label": label}

        self.viewer.model_ais_objects["Node"] = node_ais_list
        if hasattr(self.viewer, "set_node_hover_data"):
            self.viewer.set_node_hover_data(hover_nodes)

        # Store members for grillage
        self._members = members
        return {nid: (d["x"], d["y"], d["z"]) for nid, d in self._node_data.items()}

    # ── RENDER GRILLAGE ───────────────────────────────────────────────────────

    def _render_grillage(self, node_positions: dict) -> None:
        """Draw member edges between nodes to form the grillage overlay."""
        if not self._is_display_ready() or not node_positions:
            return

        members = getattr(self, "_members", None)
        if not members:
            return

        grillage_color = Quantity_Color(0.15, 0.15, 0.15, Quantity_TOC_RGB)
        grillage_ais = []

        deck_thickness = float(getattr(self.generator, "deck_thickness", 0.0) or 0.0)
        # Single layer at deck top; optionally a second layer below the deck slab.
        z_offsets = [0.0] + ([-deck_thickness - 4.0] if deck_thickness > 0 else [])

        for node_pair in members.values():
            if not node_pair or len(node_pair) < 2:
                continue
            p1 = node_positions.get(node_pair[0])
            p2 = node_positions.get(node_pair[1])
            if not p1 or not p2:
                continue
            for dz in z_offsets:
                edge = BRepBuilderAPI_MakeEdge(
                    gp_Pnt(p1[0], p1[1], p1[2] + dz),
                    gp_Pnt(p2[0], p2[1], p2[2] + dz),
                ).Shape()
                ais = self.display.DisplayShape(edge, color=grillage_color, update=False)
                ais = ais[0] if isinstance(ais, list) else ais
                try:
                    ais.SetWidth(2.0)
                except Exception:
                    pass
                grillage_ais.append(ais)

        if grillage_ais:
            self.viewer.model_ais_objects["Grillage"] = grillage_ais

    # ── RENDER NODE NUMBERS ───────────────────────────────────────────────────

    def _render_node_numbers(self) -> None:
        """
        Display node-id labels without any background box.
        Text is rendered in 3D world space (height in mm) so it scales
        proportionally with the model as the user zooms in/out.
        Text is dark (near-black) for legibility on the light deck.
        Labels face the camera and are drawn on the topmost Z-layer.
        """
        if not self._is_display_ready() or not self._node_data:
            return

        # Clear any previous labels
        for ais in self.viewer.model_ais_objects.pop("NodeNumbers", []):
            try:
                self.viewer.context.Erase(ais, False)
            except Exception:
                pass

        # Very dark charcoal — visible against the light deck without a box
        text_color = Quantity_Color(0.05, 0.05, 0.05, Quantity_TOC_RGB)

        label_ais_list = []

        if _HAS_AIS_TEXT:
            for nid, d in self._node_data.items():
                try:
                    txt = AIS_TextLabel()
                    txt.SetText(str(nid))
                    txt.SetPosition(gp_Pnt(d["x"], d["y"], d["z"] + 80.0))
                    txt.SetColor(text_color)
                    # NORMAL display type — no subtitle/background box
                    try:
                        txt.SetDisplayType(_TODT_NORMAL)
                    except Exception:
                        pass
                    # 3D world-space height (mm) — scales with the model on zoom.
                    # 350 mm ≈ a comfortable fraction of typical node spacing.
                    try:
                        txt.SetHeight(30.0)
                    except Exception:
                        pass
                    try:
                        txt.SetFlipping(True)
                    except Exception:
                        pass
                    if _Z_TOP is not None:
                        try:
                            txt.SetZLayer(_Z_TOP)
                        except Exception:
                            pass
                    self.viewer.context.Display(txt, False)
                    label_ais_list.append(txt)
                except Exception:
                    pass
        else:
            # Fallback: small coloured sphere when AIS_TextLabel unavailable
            for nid, d in self._node_data.items():
                try:
                    r = (int(nid) * 37 % 200 + 55) / 255.0
                    g = (int(nid) * 71 % 200 + 55) / 255.0
                    col = Quantity_Color(r, g, 0.9, Quantity_TOC_RGB)
                    ais = AIS_Shape(
                        BRepPrimAPI_MakeSphere(gp_Pnt(d["x"], d["y"], d["z"] + 80.0), 45.0).Shape()
                    )
                    ais.SetColor(col)
                    if _Z_TOP is not None:
                        try:
                            ais.SetZLayer(_Z_TOP)
                        except Exception:
                            pass
                    self.viewer.context.Display(ais, False)
                    label_ais_list.append(ais)
                except Exception:
                    pass

        if label_ais_list:
            self.viewer.model_ais_objects["NodeNumbers"] = label_ais_list

    # ── VISIBILITY ────────────────────────────────────────────────────────────

    def update_component_visibility(self, selected_components: list) -> None:
        """Show/hide model objects based on the current checkbox selection."""
        if not self._is_display_ready():
            return

        context = self.viewer.context
        show_node_numbers = "NodeNumbers" in selected_components
        show_nodes        = "Node" in selected_components

        # Map checkbox keys → internal model_ais_objects keys
        component_map = {
            "Girder":        ["Girder Web", "Girder Flange", "Support", "Stiffener", "Shear Stud"],
            "Deck":          ["Deck"],
            "Cross Bracing": ["Cross Bracing"],
            "Crash Barrier": ["Crash Barrier", "Crash Barrier W-Beam"],
            "Median":        ["Median", "Median W-Beam"],
            "Railing":       ["Railing"],
            "Grillage":      ["Grillage"],
            "Node":          ["Node"],
        }

        # Build the set of AIS keys that should be visible (base + overlays except NodeNumbers)
        visible_keys: set = set()
        for comp in selected_components:
            if comp in component_map:
                visible_keys.update(component_map[comp])

        # ── Node Numbers: mutually exclusive with sphere markers ──────────────
        if show_node_numbers:
            self._render_node_numbers()
            # Hide sphere markers while numbers are shown
            for ais in self.viewer.model_ais_objects.get("Node", []):
                try:
                    context.Erase(ais, False)
                except Exception:
                    pass
            # Disable hover tooltips (text labels already give the info)
            if hasattr(self.viewer, "set_node_hover_data"):
                self.viewer.set_node_hover_data([])
        else:
            # Clear any existing number labels
            for ais in self.viewer.model_ais_objects.pop("NodeNumbers", []):
                try:
                    context.Erase(ais, False)
                except Exception:
                    pass
            # Re-enable hover when Node spheres are visible
            if show_nodes and hasattr(self.viewer, "set_node_hover_data"):
                self.viewer.set_node_hover_data(
                    [{"x": d["x"], "y": d["y"], "z": d["z"], "label": d["label"]}
                     for d in self._node_data.values()]
                )
            elif hasattr(self.viewer, "set_node_hover_data"):
                self.viewer.set_node_hover_data([])

        # ── Show / hide all registered AIS objects ────────────────────────────
        for key, ais_list in self.viewer.model_ais_objects.items():
            if key == "NodeNumbers":
                continue  # already handled above
            if key == "Node" and show_node_numbers:
                continue  # already erased above; don't re-show
            should_show = key in visible_keys
            for ais in ais_list:
                try:
                    if should_show:
                        context.Display(ais, False)
                    else:
                        context.Erase(ais, False)
                except Exception:
                    pass

        # Deck textures follow the Deck checkbox
        show_deck = "Deck" in selected_components
        for ais in getattr(self.viewer, "deck_texture_ais", []):
            try:
                if show_deck:
                    context.Display(ais, False)
                else:
                    context.Erase(ais, False)
            except Exception:
                pass

        self.display.FitAll()
        self.display.Repaint()

    def show_full_model(self) -> None:
        """Display all registered bridge components (fallback)."""
        if not self._is_display_ready():
            return
        context = self.viewer.context
        for ais_list in self.viewer.model_ais_objects.values():
            for ais in ais_list:
                try:
                    context.Display(ais, False)
                except Exception:
                    pass
        for ais in getattr(self.viewer, "deck_texture_ais", []):
            try:
                context.Display(ais, False)
            except Exception:
                pass
        self.display.FitAll()
        self.display.Repaint()

    def regenerate_bridge(self):
        self._load_bridge()

    # ── ZOOM CONTROLS ─────────────────────────────────────────────────────────

    def _create_zoom_controls(self):
        self._view_cube_size   = 75
        self._view_cube_margin = 10
        self._zoom_btn_size    = 40
        self._zoom_spacing     = 6

        _style = """
            QPushButton { font-size: 20px; font-weight: bold;
                          background-color: white; border: 1px solid #bdbdbd; }
            QPushButton:hover   { background-color: #e6e6e6; }
            QPushButton:pressed { background-color: #d6d6d6; }
        """

        self.zoom_in_btn = QPushButton("+", self.viewer)
        self.zoom_in_btn.setFixedSize(self._zoom_btn_size, self._zoom_btn_size)
        self.zoom_in_btn.setCursor(Qt.PointingHandCursor)
        self.zoom_in_btn.clicked.connect(lambda: self.display.ZoomFactor(1.1))
        self.zoom_in_btn.setStyleSheet(_style)

        self.zoom_out_btn = QPushButton("-", self.viewer)
        self.zoom_out_btn.setFixedSize(self._zoom_btn_size, self._zoom_btn_size)
        self.zoom_out_btn.setCursor(Qt.PointingHandCursor)
        self.zoom_out_btn.clicked.connect(lambda: self.display.ZoomFactor(1 / 1.1))
        self.zoom_out_btn.setStyleSheet(_style)

        self.zoom_in_btn.show()
        self.zoom_out_btn.show()
        self._position_zoom_buttons()

        self._orig_resize_event = self.viewer.resizeEvent
        self.viewer.resizeEvent = self._cad_resize_proxy

    def _position_zoom_buttons(self):
        if not hasattr(self, "zoom_in_btn"):
            return
        w = self.viewer.width()
        cube_right  = w - self._view_cube_margin
        cube_left   = cube_right - self._view_cube_size
        cube_bottom = self._view_cube_margin + self._view_cube_size + 30
        btn_x = cube_left + self._view_cube_size // 2 - self._zoom_btn_size // 2
        self.zoom_in_btn.move(btn_x, cube_bottom + self._zoom_spacing)
        self.zoom_out_btn.move(btn_x, cube_bottom + self._zoom_spacing * 2 + self._zoom_btn_size)

    def _cad_resize_proxy(self, event):
        if self._orig_resize_event:
            self._orig_resize_event(event)
        self._position_zoom_buttons()


# ── COMPONENT CHECKBOX BAR ────────────────────────────────────────────────────

class BridgeComponentCheckbox(QWidget):
    """Horizontal component selector with multi-select capability."""

    # (label, internal key)
    # key=None  → "Model" pseudo-checkbox (selects all base components)
    # key in overlay_keys → independent toggle, not affected by Model
    COMPONENTS = [
        ("Model",         None),
        ("Girder",        "Girder"),
        ("Deck",          "Deck"),
        ("Cross Bracing", "Cross Bracing"),
        ("Crash Barrier", "Crash Barrier"),
        ("Median",        "Median"),
        ("Railing",       "Railing"),
        ("Grillage view", "Grillage"),
        ("Node",          "Node"),
        ("Node Numbers",  "NodeNumbers"),
    ]
    OVERLAY_KEYS = {"Grillage", "Node", "NodeNumbers"}

    def __init__(self, parent: CAD3DWindow):
        super().__init__(parent)
        self._cad = parent

        self.setObjectName("cad_component_selector")
        self.setFixedHeight(30)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 4, 12, 4)
        layout.setSpacing(16)
        layout.addStretch()

        self._checkboxes: list[QCheckBox] = []
        for label, key in self.COMPONENTS:
            cb = QCheckBox(label, self)
            cb.setObjectName(label)
            cb.setCursor(Qt.PointingHandCursor)
            cb.clicked.connect(lambda checked, k=key, c=cb: self._on_click(k, c, checked))
            layout.addWidget(cb)
            self._checkboxes.append(cb)

        layout.addStretch()

        # Default: Model checked; Node + Node Numbers unchecked
        self._checkboxes[0].setChecked(True)

    # ── click logic ───────────────────────────────────────────────────────────

    def _on_click(self, key, cb, checked):
        model_cb = self._checkboxes[0]

        if key is None:                        # "Model" clicked
            if checked:
                # Uncheck individual base components, keep overlays as-is
                for c, (_, k) in zip(self._checkboxes[1:], self.COMPONENTS[1:]):
                    if k and k not in self.OVERLAY_KEYS:
                        c.blockSignals(True)
                        c.setChecked(False)
                        c.blockSignals(False)
            else:
                if not self._any_base_checked():
                    cb.blockSignals(True)
                    cb.setChecked(True)
                    cb.blockSignals(False)
            self._apply()
            return

        if key in self.OVERLAY_KEYS:
            self._apply()
            return

        # Base component clicked
        if checked:
            model_cb.blockSignals(True)
            model_cb.setChecked(False)
            model_cb.blockSignals(False)
        else:
            if not self._any_base_checked():
                model_cb.blockSignals(True)
                model_cb.setChecked(True)
                model_cb.blockSignals(False)
        self._apply()

    def _any_base_checked(self) -> bool:
        return any(
            cb.isChecked()
            for cb, (_, k) in zip(self._checkboxes[1:], self.COMPONENTS[1:])
            if k and k not in self.OVERLAY_KEYS
        )

    def _collect(self) -> list:
        model_checked = self._checkboxes[0].isChecked()
        result = []
        for cb, (_, key) in zip(self._checkboxes[1:], self.COMPONENTS[1:]):
            if not key:
                continue
            if key in self.OVERLAY_KEYS:
                if cb.isChecked():
                    result.append(key)
            elif model_checked or cb.isChecked():
                result.append(key)
        return result

    def _apply(self):
        selected = self._collect()
        if selected:
            self._cad.update_component_visibility(selected)
            return
        # Nothing selected → fall back to Model
        self._checkboxes[0].blockSignals(True)
        self._checkboxes[0].setChecked(True)
        self._checkboxes[0].blockSignals(False)
        selected = self._collect()
        if selected:
            self._cad.update_component_visibility(selected)
        else:
            self._cad.show_full_model()

    def apply_selection(self):
        self._apply()

    def reset(self):
        """Reset to default: Model checked, all others unchecked."""
        for cb in self._checkboxes:
            cb.blockSignals(True)
            cb.setChecked(False)
            cb.blockSignals(False)
        self._checkboxes[0].blockSignals(True)
        self._checkboxes[0].setChecked(True)
        self._checkboxes[0].blockSignals(False)


# ── STANDALONE TESTING ────────────────────────────────────────────────────────

def main():
    app = QApplication(sys.argv)

    bridge_parameters = BridgeParametersDTO(
        span_length_L=25_000,
        girder_section_d=900,
        girder_section_bf=500,
        girder_section_bf_b=500,
        girder_section_tf=260,
        girder_section_tf_b=260,
        girder_section_tw=100,
        num_girders=5,
        girder_spacing=2_750,
        skew_angle=0,
        carriageway_width=12_000,
        deck_thickness=400,
        footpath_config="BOTH",
        footpath_width=1_500,
        railing_width=300,
        barrier_type="Semi-Rigid",
        crash_barrier_subtype="Double W-beam",
        enable_median=True,
        median_type="Metallic Crash Barrier",
        rail_count=3,
        railing_type="rcc",
        include_intermediate_stiffeners=True,
        intermediate_stiffener_spacing=2_000,
        intermediate_stiffener_thickness=20,
        intermediate_stiffener_outstand=None,
        num_end_stiffener_pairs=4,
        end_stiffener_thickness=30,
        end_stiffener_outstand=None,
        include_longitudinal_stiffeners=True,
        num_longitudinal_stiffeners=2,
        longitudinal_stiffener_thickness=20,
        longitudinal_stiffener_outstand=None,
        cross_bracing_spacing=4_000,
        bracing_type="X",
        x_bracket_option="BOTH",
        k_top_bracket=True,
        diagonal_section_type="ANGLE",
        diagonal_section_dims=SectionDimsDTO(leg_h=100, leg_w=50, connection_type="LONGER_LEG"),
        diagonal_thickness=5,
        top_chord_section_type="DOUBLE_CHANNEL",
        top_chord_section_dims=SectionDimsDTO(leg_h=80, leg_w=40, connection_type="LONGER_LEG"),
        top_chord_thickness=5,
        bottom_chord_section_type="ANGLE",
        bottom_chord_section_dims=SectionDimsDTO(leg_h=80, leg_w=40, connection_type="LONGER_LEG"),
        bottom_chord_thickness=5,
        end_diaphragm_type="Cross Bracing",
        end_diaphragm_spacing=100,
        end_diaphragm_bracing_type="K",
        end_diaphragm_diagonal_section_type="ANGLE",
        end_diaphragm_diagonal_section_dims=SectionDimsDTO(leg_h=100, leg_w=50, connection_type="LONGER_LEG"),
        end_diaphragm_diagonal_thickness=5,
        end_diaphragm_top_chord_section_type="CHANNEL",
        end_diaphragm_top_chord_section_dims=SectionDimsDTO(leg_h=80, leg_w=40, connection_type="LONGER_LEG"),
        end_diaphragm_top_chord_thickness=5,
        end_diaphragm_bottom_chord_section_type="ANGLE",
        end_diaphragm_bottom_chord_section_dims=SectionDimsDTO(leg_h=80, leg_w=40, connection_type="LONGER_LEG"),
        end_diaphragm_bottom_chord_thickness=5,
        end_diaphragm_section="I_SECTION",
        end_diaphragm_dims=ISectionDimsDTO(depth=800, flange_width=250, web_thickness=12, flange_thickness=100),
        shear_stud_params=ShearStudParamsDTO(
            base_diameter=50, top_diameter=70, base_height=150,
            top_height=50, num_per_section=4, transverse_spacing=305, pitch=500,
        ),
        girder_segments=[
            GirderSegmentDTO(length=25_000, D=900, tw=100, T_ft=260, T_fb=260, B_ft=500, B_fb=500)
        ],
        girder_segments_dict=None,
    )

    win = CAD3DWindow()
    win.show()
    QTimer.singleShot(200, lambda: win.render_3d_cad(bridge_parameters))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()