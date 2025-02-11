import json
import pandas as pd
import os
import shutil
from datetime import datetime
from pathlib import Path
import pickle
from typing import Callable

from enums.engine_enums import RegisterHeaders, FileIds, DateElements, ReadMode, WriteMode, FileExtensions, Directories

# Archivo donde se almacenarán los datos
_DATA_PATH = Path(__file__).resolve().parent.parent / Directories.DATA.value
_DATA_PATH.mkdir(exist_ok=True)
_BACKUP_PATH = _DATA_PATH / Directories.BACKUP.value
_BACKUP_PATH.mkdir(exist_ok=True)
registers_file_extension = FileExtensions.PICKLE
categories_file_extension = FileExtensions.JSON


class AccountingBook():

    def __init__(self, user, load_data: bool = False):
        self._user = user
        self._data_file = f'registers_{self._user}.{registers_file_extension.value}'
        self._categories_file = f'categories_{self._user}.{categories_file_extension.value}'
        if load_data:
            self._data = AccountingBook._load_book(self._user)
            self._categories = AccountingBook._load_categories(self._categories_file, self._user)

    @property
    def user(self):
        return self._user

    @property
    def categories(self):
        return self._categories

    @staticmethod
    def _check_existing_registers(user: str):
        return os.path.exists(f"{_DATA_PATH}/{FileIds.BOOK_FILE_PREFIX}{user}.{registers_file_extension.value}")

    @staticmethod
    def _create_book(user: str) -> list:
        return []

    @staticmethod
    def _load_book(user: str) -> list:
        file_name = f"{FileIds.BOOK_FILE_PREFIX.value}{user}.{registers_file_extension.value}"
        file_extension = FileExtensions.get_file_extension(file_name)
        load_method = get_load_method(file_extension)
        read_mode = get_file_read_mode(file_extension)
        try:
            with open(f'{_DATA_PATH}/{file_name}', read_mode.value) as input_file:
                return load_method(input_file)
        except FileNotFoundError:
            print(f"\nNo existen registros para el usuario {user}. ¿Qué deseas hacer?\n")
            print("1. Crear un nuevo libro de cuentas")
            print("2. Intentar recuperar datos borrados")
            print("3. Salir")
            answer = input("\nIntroduce tu respuesta: ")
            match answer:
                case "1":
                    print("\nSe ha creado un nuevo libro de cuentas")
                    return []
                case "2":
                    print(f"\nComprobando la existencia de copias de seguridad del usuario '{user}'")
                    AccountingBook._restore_user_data()
                case "3":
                    print("\nHas elegido cerrar sesión. ¡Hasta pronto!")

    @staticmethod
    def _load_categories(categories_file, user) -> list:
        try:
            with open(f'{_DATA_PATH}/{categories_file}', ReadMode.TEXT.value) as file:
                return json.load(file)
        except FileNotFoundError:
            print(f"No existen categorías de gasto para el usuario {user}.")
            print("Puedes añadir una nueva categoría desde el menú principal.")
            return []

    @staticmethod
    def _save_to_file(data: list, file_name: str):
        file_extension = FileExtensions.get_file_extension(file_name)
        match file_extension:
            case FileExtensions.PICKLE:
                try:
                    with open(f'{_DATA_PATH}/{file_name}', WriteMode.BINARY.value) as output_file:
                        pickle.dump(data, output_file)
                except (OSError, IOError) as e:
                    print(f"Error al guardar el archivo {file_name}: {e}")
            case FileExtensions.JSON:
                try:
                    with open(f'{_DATA_PATH}/{file_name}', WriteMode.TEXT.value) as output_file:
                        json.dump(data, output_file, indent=4)
                except (OSError, IOError) as e:
                    print(f"Error al guardar el archivo {file_name}: {e}")
            case _:
                raise NotImplementedError(f"Dump method for file extension {file_extension} not implemented")

    def new_register(self, date, category, amount, comments):
        new_registration = BookCell(date, category, amount, comments)

        self._data.append(new_registration)
        print("¡Transacción añadida con éxito!")

    def _display_data(self, filters: dict):
        if not self._data:
            print("No hay transacciones registradas.")
        else:
            # Filter the category
            filtered_cases = [entry for entry in self._data if entry.category == filters[RegisterHeaders.CATEGORY]]

            # Filter the year
            if DateElements.YEAR in filters:
                filtered_cases = [entry for entry in filtered_cases if entry.date.year == filters[DateElements.YEAR]]
            if DateElements.MONTH in filters:
                filtered_cases = [entry for entry in filtered_cases if entry.date.month == filters[DateElements.MONTH]]
            if DateElements.DAY in filters:
                filtered_cases = [entry for entry in filtered_cases if entry.date.day == filters[DateElements.DAY]]

            if len(filtered_cases) == 0:
                print(f"No hay registros para los filtros establecidos:")
                print(f"{RegisterHeaders.CATEGORY.value}: {filters[RegisterHeaders.CATEGORY]}")
                if DateElements.YEAR in filters:
                    print(f"{DateElements.YEAR.value}: {filters[DateElements.YEAR]}")
                if DateElements.MONTH in filters:
                    print(f"{DateElements.MONTH.value}: {filters[DateElements.MONTH]}")
                if DateElements.DAY in filters:
                    print(f"{DateElements.DAY.value}: {filters[DateElements.DAY]}")
            else:
                for entry in filtered_cases:
                    entry._display_data()

    def create_expense_category(self, new_category: str):
        self._categories.append(new_category)
        self._save_to_file(self._categories, self._categories_file)
        print(f"La nueva categoría de gasto {new_category} ha sido añadida correctamente.")

    # def _edit_register(self):# TODO

    # date = self._ask_date()
    # category = self._ask_category()
    # print("El gasto que va a ser editado es el siguiente:")
    # self._display_data(filters)
    # new_amount = self._ask_amount()

    @staticmethod
    def _delete_user_data(user):
        data_file = f"{FileIds.BOOK_FILE_PREFIX.value}{user}.{registers_file_extension.value}"
        categories_file = f"{FileIds.CATEGORIES_FILE_PREFIX.value}{user}.{categories_file_extension.value}"
        if not os.path.exists(f"{_DATA_PATH}/{data_file}"):
            print(f"Los datos del usuario '{user}' no existen.")
            return
        shutil.move(f"{_DATA_PATH}/{data_file}", f"{_BACKUP_PATH}/{data_file}")
        if not os.path.exists(f"{_DATA_PATH}/{categories_file}"):
            print(f"No se encontraron categorías preexistentes para el usuario {user}")
        else:
            shutil.move(f"{_DATA_PATH}/{categories_file}", f"{_BACKUP_PATH}/{categories_file}")

        print(f"Se han eliminado todos los datos del usuario '{user}'. Todavía puedes restaurarlos accediendo al "
              f"directorio de respaldo: {_BACKUP_PATH}")

    @staticmethod
    def _restore_user_data(user):
        data_file = f"{FileIds.BOOK_FILE_PREFIX.value}{user}.{registers_file_extension.value}"
        categories_file = f"{FileIds.CATEGORIES_FILE_PREFIX.value}{user}.{categories_file_extension.value}"
        if not os.path.exists(f"{_BACKUP_PATH}/{data_file}"):
            print(
                f"No se pudo recuperar información sobre registros del usuario '{user}'. Comprueba el directorio "
                f"de respaldo: {_BACKUP_PATH}.")
            return
        shutil.move(f"{_BACKUP_PATH}/{data_file}", f"{_DATA_PATH}/{data_file}")
        if not os.path.exists(f"{_BACKUP_PATH}/{categories_file}"):
            print(
                f"No se pudo recuperar información sobre categorías del usuario '{user}'. Comprueba el directorio"
                f" de respaldo: {_BACKUP_PATH}.")
        else:
            shutil.move(f"{_BACKUP_PATH}/{categories_file}", f"{_DATA_PATH}/{categories_file}")

        print(f"Los datos de '{user}' han sido restaurados. Puedes encontrarlos en: {_DATA_PATH}")


class BookCell():
    def __init__(self, date: datetime, category: str, amount: float, comments: str):
        self.date = date
        self.category = category
        self.amount = amount
        self.comments = comments
        self._registration_time = datetime.now().strftime(DateElements.FORMAT_DDMMYYYYHHMMSS.value)
        self.id = self._registration_time.replace('-', '').replace(' ', '').replace(':', '')

    def _display_data(self):
        print(f"\n---- ID del gasto: {self.id} ----\n")
        print(f"{RegisterHeaders.DATE.value}: {self.date.strftime(DateElements.FORMAT_DDMMYYYY.value)}")
        print(f"{RegisterHeaders.CATEGORY.value}: {self.category}")
        print(f"{RegisterHeaders.AMOUNT.value}: {self.amount}")
        print(f"{RegisterHeaders.COMMENT.value}: {self.comments}")
        print("\n")


def get_load_method(file_extension: FileExtensions) -> Callable:
    match file_extension:
        case FileExtensions.PICKLE:
            return pickle.load
        case FileExtensions.JSON:
            return json.load
        case _:
            raise NotImplementedError(f"Load method for file extension {file_extension} not implemented")


def get_file_read_mode(file_extension: FileExtensions) -> ReadMode:
    match file_extension:
        case FileExtensions.PICKLE:
            return ReadMode.BINARY
        case FileExtensions.JSON:
            return ReadMode.TEXT
        case _:
            raise NotImplementedError(f"Read mode for file extension {file_extension} not implemented")
