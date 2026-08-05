import tkinter as tk
from tkinter import ttk
from datetime import datetime, timedelta

from enums.tkinter_enums import UserInterfaceLabels
from enums.engine_enums import DateElements


class TkinterUserInterface:
    def __init__(self, root):
        self._root = root
        self._root.title("Registro de Gastos e Ingresos")

        # Variables para almacenar los datos
        self._type = tk.StringVar()
        self._amount = tk.DoubleVar()
        self._category = tk.StringVar()
        self._date = tk.StringVar()

        # Crear los widgets
        self.create_widgets()

    def create_widgets(self):
        # Etiqueta y campo para el importe
        tk.Label(self._root, text=UserInterfaceLabels.AMOUNT.value).grid(row=0, column=0, padx=10, pady=10)
        tk.Entry(self._root, textvariable=self._amount).grid(row=0, column=1, padx=10, pady=10)

        # Etiqueta y desplegable para la categoría
        tk.Label(self._root, text=UserInterfaceLabels.CATEGORY.value).grid(row=1, column=0, padx=10, pady=10)
        categories = ["Comida", "Transporte", "Ocio", "Salario", "Otros"]
        ttk.Combobox(self._root, textvariable=self._category, values=categories).grid(row=1, column=1, padx=10, pady=10)

        # Etiqueta y desplegable para la fecha
        tk.Label(self._root, text=UserInterfaceLabels.DATE.value).grid(row=2, column=0, padx=10, pady=10)
        self.date_combobox = ttk.Combobox(self._root, textvariable=self._date)
        self.date_combobox.grid(row=2, column=1, padx=10, pady=10)
        self.populate_dates()

        # Botón para guardar el registro
        tk.Button(self._root, text=UserInterfaceLabels.SAVE.value, command=self.save_record).grid(row=3, column=0,
                                                                                                  columnspan=2, pady=10)

    def populate_dates(self):
        # Generar fechas para los últimos 30 días
        dates = [(datetime.now() - timedelta(days=i)).strftime(DateElements.FORMAT_YYYYMMDD.value) for i in range(30)]
        self.date_combobox['values'] = dates
        self.date_combobox.current(0)  # Establecer la fecha actual como predeterminada

    def save_record(self):
        # Obtener los valores de los campos
        amount = self._amount.get()
        category = self._category.get()
        date = self._date.get()

        # Aquí puedes guardar los datos en tu sistema de almacenamiento (archivo, base de datos, etc.)
        print(f"Guardando registro: Importe={amount}, Categoría={category}, Fecha={date}")

        # Limpiar los campos después de guardar
        self._amount.set(0.0)
        self._category.set("")
        self.date_combobox.current(0)

if __name__ == "__main__":
    root = tk.Tk()
    app = TkinterUserInterface(root)
    root.mainloop()
