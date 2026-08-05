from datetime import datetime
import pandas as pd
import tkinter as tk
from tkinter import messagebox

from engine.accounting_book import AccountingBook
from enums.engine_enums import RegisterHeaders, DateElements


class UserInterface:

    def __init__(self):
        self._categories = None
        #self._user = self._user_login()

        # Crear la ventana de inicio de sesión
        #login_window = LoginWindow()
        #login_window.start()
        #self._user = login_window.username
        self._user = self._user_login()
        self._active_book = AccountingBook(user=self._user, load_data=False)
        if AccountingBook.check_existing_registers(self._user):
            self._active_book.load_book()
        else:
            self._new_user_menu()
        self._menu()

    def _new_user_menu(self):
        print(f"\nNo existen registros para el usuario {self._user}. ¿Qué deseas hacer?\n")
        print("1. Crear un nuevo libro de cuentas")
        print("2. Intentar recuperar datos borrados")
        print("3. Salir")
        answer = input("\nIntroduce tu respuesta: ")
        match answer:
            case "1":
                self._active_book.new_book()
                print("\nSe ha creado un nuevo libro de cuentas")
            case "2":
                print(f"\nComprobando la existencia de copias de seguridad del usuario '{self._user}'")
                AccountingBook._restore_user_data(self._user)
            case "3":
                print("\nHas elegido cerrar sesión. ¡Hasta pronto!")
                raise SystemExit

    @staticmethod
    def _user_login() -> str:
        return input("Introduce tu nombre de usuario: ").strip()

    @staticmethod
    def _ask_date() -> datetime:
        date = input("Fecha (DD-MM-YYYY, presiona Enter para hoy): ").strip()
        if not date:  # Si el usuario deja vacío, toma la fecha actual
            date = datetime.now().strftime(DateElements.FORMAT_DDMMYYYY.value)
        try:
            date = datetime.strptime(date, DateElements.FORMAT_DDMMYYYY.value).strftime(
                DateElements.FORMAT_DDMMYYYY.value)
        except ValueError:
            print("Formato de fecha inválido. Intenta nuevamente.")
            return UserInterface._ask_date()
        return pd.to_datetime(date, format=DateElements.FORMAT_DDMMYYYY.value)

    @staticmethod
    def _ask_comments() -> str:
        comments = input("Escribe un comentario acerca de este gasto. Para dejarlo vacío, pulsa la tecla Enter: ")
        if not comments:
            return "Sin comentarios"
        else:
            return comments

    @staticmethod
    def _ask_amount() -> float:
        amount = input("Introduce el importe (utiliza el punto como separador decimal): ").strip()
        try:
            amount = float(amount)
        except ValueError:
            print("Formato de importe inválido. Intenta nuevamente.")
            UserInterface._ask_amount()
        return amount

    def _ask_category(self) -> str:
        if len(self._active_book.categories) == 0:
            print("No hay categorías preexistentes. Se creará una nueva.")
            self._create_expense_category()
        print("Estas son las categorías existentes:")
        i = 1
        for category in self._active_book.categories:
            print(f"{i}. {category}")
            i += 1
        answer = input("Indica la categoría de gasto o 0 para añadir una nueva categoría: ").strip()

        if not answer:
            print("Debes seleccionar una de las categorías del listado. Prueba otra vez.")
            self._ask_category()
        elif answer == "0":
            self._create_expense_category()
            return self._ask_category()
        else:
            try:
                return self._active_book.categories[int(answer) - 1]
            except IndexError:
                print("La respuesta indicada no corresponde a ninguna categoría existente.")
                print("Por favor, indica el número de una de las categorías de la lista.")
                self._ask_category()

    def _new_register(self) -> None:
        print("Introduce los datos de la transacción:")
        date = UserInterface._ask_date()
        category = self._ask_category()
        amount = UserInterface._ask_amount()
        comments = UserInterface._ask_comments()

        self._active_book.new_register(date, category, amount, comments)

    def _create_expense_category(self) -> None:
        category_label = input("Introduce el nombre de la nueva categoría de gasto: ")
        self._active_book.create_expense_category(category_label)

    def _display_data(self) -> None:
        category = self._ask_category()  # TODO: si no hay categorías preexistentes pregunta si quieres crear una nueva, lo cual no tiene sentido en esta función
        filters = {RegisterHeaders.CATEGORY: category}

        year = input("Indica el año correspondiente en el que se produjo la transacción que deseas consultar. "
                     "Si quieres ver todo el histórico, pulsa la tecla Enter: ")
        if not year:
            self._active_book.display_data(filters=filters)
        else:
            filters.update({DateElements.YEAR: int(year)})
            month = input("Indica el número del mes correspondiente en el que se produjo la transacción que deseas "
                          "consultar. Si quieres consultar el año completo, pulsa la tecla Enter: ")
            if not month:
                self._active_book.display_data(filters=filters)
            else:
                day = input("Indica el día correspondiente en el que se produjo la transacción que deseas "
                            "consultar. Si quieres consultar el mes completo, pulsa la tecla Enter: ")
                if not day:
                    filters.update({DateElements.MONTH: int(month)})
                else:
                    filters.update({DateElements.MONTH: int(month), DateElements.DAY: int(day)})
                self._active_book.display_data(filters=filters)

    def _delete_user_data(self) -> None:

        print(f"Se procederá a eliminar los datos del usuario {self._user} y se cerrará la sesión. "
              f"¿Estás seguro/a de querer continuar?\n")
        print("1. No, he cambiado de idea.")
        print(f"2. Sí, quiero eliminar los datos del usuario {self._user}")

        user_confirmation = input("\nIntroduce tu respuesta: ")

        match user_confirmation:
            case "1":
                print(f"\nOperación cancelada. No se eliminaron los datos correspondientes al usuario {self._user}\n")
            case "2":
                self._active_book.delete_user_data(self._user)
                return
            case _:
                print("Respuesta no válida. Por favor, inténtalo de nuevo.\n")
                self._delete_user_data()

    def _edit_register(self) -> None:
        self._display_data()
        register_id = int(input("Pega aquí el ID del registro que deseas modificar: ").strip())
        print("Introduce los nuevos datos del registro.")
        date = UserInterface._ask_date()
        category = self._ask_category()
        amount = UserInterface._ask_amount()
        comments = UserInterface._ask_comments()
        self._active_book.edit_register(register_id=register_id, new_date=date, new_category=category,
                                        new_amount=amount,
                                        new_comments=comments)
        print("¡Registro modificado con éxito!")

    def _menu(self) -> None:
        while True:
            print("\n--- Gestor de Gastos ---")
            print("1. Añadir nuevo registro")
            print("2. Ver registros")
            print("3. Editar registro")  # TODO
            print("4. Añadir categoría de gasto")
            print("5. Añadir categoría de ingreso")  # TODO
            print("6. Editar categoría de gasto")  # TODO
            print("7. Editar categoría de ingreso")  # TODO
            print("8. Eliminar datos de usuario")
            print("9. Restaurar datos de usuario")  # TODO
            print("10. Salir")

            opcion = input("\nSelecciona una opción: ").strip()

            match opcion:
                case "1":
                    self._new_register()
                    AccountingBook._save_to_file(self._active_book._data, self._active_book._data_file)
                case "2":
                    self._display_data()
                case "3":
                    self._edit_register()
                    AccountingBook._save_to_file(self._active_book._data, self._active_book._data_file)
                case "4":
                    self._create_expense_category()
                case "5":
                    raise NotImplementedError("Lamentablemente, esta opción todavía no está implementada. Elige otra.")
                case "6":
                    raise NotImplementedError("Lamentablemente, esta opción todavía no está implementada. Elige otra.")
                case "7":
                    raise NotImplementedError("Lamentablemente, esta opción todavía no está implementada. Elige otra.")
                case "8":
                    self._delete_user_data()
                    return
                case "9":
                    raise NotImplementedError("Lamentablemente, esta opción todavía no está implementada. Elige otra.")
                case "10":
                    print("¡Hasta luego!")
                    raise SystemExit
                case _:
                    print("Opción no válida. Intenta nuevamente.")
                    self._menu()


class LoginWindow:
    def __init__(self):
        self._root = tk.Tk()
        self._root.title("Inicio de Sesión")  # TODO: encode enum
        self._username_entry = None
        self._username = ""
        self._create_widgets()
    @property
    def username(self):
        return self._username

    def start(self):
        self._root.mainloop()

    def _create_widgets(self):
        # Etiqueta y campo para el nombre de usuario
        tk.Label(self._root, text="Nombre de usuario:").grid(row=0, column=0, padx=10, pady=10)
        self._username_entry = tk.Entry(self._root)
        self._username_entry.grid(row=0, column=1, padx=10, pady=10)

        # Botón para iniciar sesión
        tk.Button(self._root, text="Iniciar Sesión", command=self._on_login_click).grid(row=1, column=0, columnspan=2, pady=10)

    def _on_login_click(self):
        """Manejar el evento de clic en el botón 'Iniciar Sesión'."""
        self._username = self._username_entry.get().strip()  # Obtener el nombre de usuario

        # Verificar si el campo está vacío
        if not self._username:
            messagebox.showwarning("Error", "El nombre de usuario no puede estar vacío.")
        else:
            print(f"Usuario '{self._username}' ha iniciado sesión.")  # Aquí puedes agregar la lógica de inicio de sesión
            self._root.destroy()  # Cerrar la ventana de login

