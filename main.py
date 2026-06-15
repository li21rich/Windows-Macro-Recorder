import tkinter as tk
from macro import MacroEngine
from gui import MacroGUI

if __name__ == "__main__":
    engine = MacroEngine()
    root   = tk.Tk()
    root.title("Macro Recorder")
    root.iconbitmap("pen.ico")
    app    = MacroGUI(root, engine)
    root.mainloop()