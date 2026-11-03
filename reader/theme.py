"""Shared Qt style sheet for every TreeSea Reader window.

Kept in one place so the main workbench, the effects library and the calculator
stop drifting apart. Import `STYLE` and apply it once on the QApplication.
"""

STYLE = """
QMainWindow,QDialog { background:#0e171f; color:#e4edf3; }
QWidget { color:#dae5ec; font-family:'Microsoft YaHei UI'; font-size:12px; }
QFrame#panel { background:#14222c; border:1px solid #263b47; border-radius:10px; }
QLabel#heading { font-size:24px; font-weight:700; color:#f2f7fa; }
QLabel#section { font-size:14px; font-weight:600; color:#86dcd0; }
QLabel#muted { color:#92a6b3; }
QLabel#status { padding:10px; background:#17332f; border:1px solid #2e6257; border-radius:6px; color:#9de0cd; }
QLabel#review { padding:8px; background:#242013; border:1px solid #4a3f24; border-radius:6px; color:#f0d79a; }
QPushButton { background:#233b49; border:1px solid #35515f; padding:8px 10px; border-radius:6px; }
QPushButton:hover { background:#2e4b5b; }
QPushButton:disabled { color:#617681; background:#172831; }
QPushButton#primary { background:#258d7c; color:white; border:1px solid #43baa0; font-weight:600; }
QPushButton#primary:disabled { background:#1a312e; color:#5d7f78; border-color:#26453f; }
QComboBox,QKeySequenceEdit,QSpinBox,QLineEdit { background:#0e1b24; padding:6px; border:1px solid #35505d; border-radius:4px; }
QComboBox QAbstractItemView { background:#14222c; color:white; selection-background-color:#276c63; }
QCheckBox { color:#cfe0e8; spacing:6px; }
QTableWidget,QListWidget,QTextBrowser,QPlainTextEdit,QTextEdit,QTreeWidget {
    background:#101d27; border:1px solid #29414e; border-radius:5px; selection-background-color:#28544f; }
QTableWidget::item { padding:5px; }
QHeaderView { background:#1a2d39; border:0; }
QHeaderView::section { background:#1a2d39; padding:6px; border:0; color:#a6bcc9; }
QTableCornerButton::section { background:#1a2d39; border:0; }
QTabWidget::pane { border:1px solid #2a414d; border-radius:5px; }
QTabBar::tab { background:#172934; padding:10px 20px; color:#9eb2c0; }
QTabBar::tab:selected { color:#80e0cd; background:#203d47; }
QGraphicsView { background:#0b141c; border:0; }
QSplitter::handle { background:#243945; }
QScrollArea,QScrollArea>QWidget>QWidget { background:#0e171f; }
QTableCornerButton::section { background:#1a2d39; border:0; }
QToolTip { background:#1b303b; color:white; border:1px solid #577a85; }
QFrame#topbar { background:#0d1a23; border-bottom:1px solid #223641; }
QFrame#hud { background:#0b1620; border-bottom:1px solid #1b2c37; }
QFrame#statusline { background:#0b1620; border-top:1px solid #1b2c37; }
QLabel#statuslineText { color:#a9bdc9; }
QFrame#nav { background:#0b1620; border-right:1px solid #1b2c37; }
QPushButton#navItem { text-align:left; padding:8px 12px; border:0; border-radius:6px; background:transparent; color:#c3d4dd; }
QPushButton#navItem:hover { background:#15232d; }
QPushButton#navItem:checked { background:#173c38; color:#7fe3cd; font-weight:600; }
QLabel#navSection { color:#6d8592; padding:10px 12px 4px 12px; }
QPushButton#hudChip { background:#12212b; border:1px solid #223541; border-radius:11px; padding:2px 10px; color:#cfe0e8; }
QPushButton#hudChip:hover { border-color:#2f7f73; color:white; }
QPushButton#ghost { background:#16242e; border:1px solid #2a414d; padding:4px 10px; }
QLabel#stateDot { color:#39d98a; font-size:14px; }
QFrame#seg { background:#0f1d26; border:1px solid #223541; border-radius:6px; }
QPushButton#segItem { background:transparent; border:0; padding:4px 10px; border-radius:4px; color:#a9bdc9; }
QPushButton#segItem:checked { background:#1d3c45; color:#8ce7d3; }
QLabel#prediction { color:#f6e6a8; background:#1d3129; border:1px solid #35513f; padding:3px 10px; border-radius:4px; }
QTabWidget#pages::pane { border:0; }
QToolButton { background:#1d3440; border:1px solid #2c4552; border-radius:6px; padding:5px 10px; }
QToolButton:hover { background:#24414f; }
QToolButton::menu-indicator { width:0px; }
QMenu { background:#14222c; border:1px solid #29414e; padding:4px; }
QMenu::item { padding:6px 18px; border-radius:4px; }
QMenu::item:selected { background:#26544f; }
QProgressBar { background:#101d27; border:1px solid #29414e; border-radius:6px; text-align:center; color:#cfe0e8; }
QProgressBar::chunk { background:#258d7c; border-radius:5px; }
QGroupBox { border:1px solid #29414e; border-radius:8px; margin-top:10px; padding-top:8px; }
QGroupBox::title { subcontrol-origin:margin; left:10px; padding:0 4px; color:#a9bdc9; }
"""
