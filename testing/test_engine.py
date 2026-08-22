from unittest import TestCase
from unittest.mock import patch, call
import json
import pickle
from pathlib import Path
import pandas as pd

from lxcell.engine.accounting_book import AccountingBook
from lxcell.engine.user_interface import UserInterface
from lxcell.enums.engine_enums import Directories, FileExtensions, DateElements

_PATH_DATA = Path(__file__).resolve().parent.parent / Directories.DATA.value
DUMMY_USER = "TestUser"
DUMMY_DATE = "1-1-2025"
DUMMY_CATEGORY = "TestCategory"
DUMMY_AMOUNT = "10"
DUMMY_COMMENT = "dummy comment"
DUMMY_ID = "101101"
MODIFIED_DATE = "2-2-2025"
MODIFIED_AMOUNT = "200"
MODIFIED_COMMENT = "modified comment"
registers_file_extension = FileExtensions.PICKLE
categories_file_extension = FileExtensions.JSON


class TestBookInitializer(TestCase):

    @patch('builtins.input', side_effect=[
        DUMMY_USER,  # Nombre de usuario
        "1",  # Crear nuevo libro
        "1",  # Registrar nuevo gasto
        DUMMY_DATE,  # Fecha del gasto
        DUMMY_CATEGORY,  # Categoría del gasto
        "1",  # Seleccionar categoría
        DUMMY_AMOUNT,  # Monto del gasto
        DUMMY_COMMENT,  # Comentario del gasto
        "10",  # Salir
    ])
    @patch("lxcell.engine.accounting_book.datetime")
    def setUp(self, mock_datetime, mock_input) -> None:
        # Mock the new register's ID
        mock_datetime.now.return_value.strftime.return_value = DUMMY_ID

        # Create new register
        UserInterface()

    @patch('builtins.input', side_effect=[
        DUMMY_USER,  # Iniciamos sesión de nuevo
        "8",  # Eliminar datos de usuario
        "2"  # Confirmar eliminación
    ])
    def test_new_register(self, mock_input) -> None:
        with open(f'{_PATH_DATA}/categories_{DUMMY_USER}.{categories_file_extension.value}') as f:
            categories = json.load(f)

        with open(f'{_PATH_DATA}/registers_{DUMMY_USER}.{registers_file_extension.value}', 'rb') as f:
            register = pickle.load(f)[int(DUMMY_ID)]

        with self.subTest("Check categories file"):
            self.assertEqual(categories[0], DUMMY_CATEGORY)

        with self.subTest("Check date"):
            dummy_date_timestamp = pd.to_datetime(DUMMY_DATE, format=DateElements.FORMAT_DDMMYYYY.value)
            self.assertEqual(register.date, dummy_date_timestamp)

        with self.subTest("Check amount"):
            self.assertEqual(register.amount, float(DUMMY_AMOUNT))

        with self.subTest("Check category"):
            self.assertEqual(register.category, DUMMY_CATEGORY)

        with self.subTest("Check comment"):
            self.assertEqual(register.comments, DUMMY_COMMENT)

        UserInterface()  # Delete data

    @patch('builtins.input', side_effect=[
        DUMMY_USER,  # Iniciamos sesión de nuevo
        "3",  # Modify register
        "1",  # Select category
        DUMMY_DATE.split('-')[-1],
        DUMMY_DATE.split('-')[1],
        DUMMY_DATE.split('-')[0],
        DUMMY_ID,
        MODIFIED_DATE,
        "1",  # Seleccionamos la categoría de gasto
        MODIFIED_AMOUNT,
        MODIFIED_COMMENT,
        "10",  # Salimos de nuevo
        DUMMY_USER,  # Iniciamos sesión de nuevo
        "8",  # Eliminar datos de usuario
        "2"  # Confirmar eliminación
    ])
    def test_edit_register(self, mock_input) -> None:
        UserInterface()  # Edit register

        with open(f'{_PATH_DATA}/registers_{DUMMY_USER}.{registers_file_extension.value}', 'rb') as f:
            modified_register = pickle.load(f)[int(DUMMY_ID)]

        with self.subTest("Check modified date"):
            modified_date_timestamp = pd.to_datetime(MODIFIED_DATE, format=DateElements.FORMAT_DDMMYYYY.value)
            self.assertEqual(modified_register.date, modified_date_timestamp)

        with self.subTest("Check modified amount"):
            self.assertEqual(modified_register.amount, float(MODIFIED_AMOUNT))

        with self.subTest("Check modified comment"):
            self.assertEqual(modified_register.comments, MODIFIED_COMMENT)

        UserInterface()  # Delete data


if __name__ == '__main__':
    TestBookInitializer()
