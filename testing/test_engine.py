from unittest import TestCase
from unittest.mock import patch, call
import json
import pickle
from pathlib import Path
import pandas as pd

from engine.accounting_book import AccountingBook
from engine.user_interface import UserInterface
from enums.engine_enums import Directories, FileExtensions, DateElements

DUMMY_USER = "TestUser"
DUMMY_DATE = "1-1-2025"
DUMMY_CATEGORY = "TestCategory"
DUMMY_AMOUNT = "10.3"
DUMMY_COMMENT = "dummy comment"
registers_file_extension = FileExtensions.PICKLE
categories_file_extension = FileExtensions.JSON


class TestBookInitializer(TestCase):

    def setUp(self) -> None:
        self._user = "TesterUser"
        self._categories = ("category0", "category1", "category3")
        self._answers_subtest1 = [self._user, "1", "1", self._categories[0], "1", "0.0", ]
        self._path_data = Path(__file__).resolve().parent.parent / Directories.DATA.value

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
        DUMMY_USER,  # Iniciamos sesión de nuevo
        "8",  # Eliminar datos de usuario
        "2"  # Confirmar eliminación
    ])
    def test_new_register(self, mock_input):
        UserInterface()
        with open(f'{self._path_data}/categories_{DUMMY_USER}.{categories_file_extension.value}') as f:
            categories = json.load(f)

        with open(f'{self._path_data}/registers_{DUMMY_USER}.{registers_file_extension.value}', 'rb') as f:
            register = pickle.load(f)[0]

        with self.subTest("Check categories file"):
            self.assertEqual(categories[0], DUMMY_CATEGORY)

        with self.subTest("Check date"):
            dummy_date_timestamp = pd.to_datetime(DUMMY_DATE, format=DateElements.FORMAT_DDMMYYYY.value)
            self.assertEqual(register.date, dummy_date_timestamp)

        with self.subTest("Check amount"):
            self.assertEqual(str(register.amount), DUMMY_AMOUNT)

        with self.subTest("Check category"):
            self.assertEqual(register.category, DUMMY_CATEGORY)

        with self.subTest("Check comment"):
            self.assertEqual(register.comments, DUMMY_COMMENT)

        UserInterface()


if __name__ == '__main__':
    TestBookInitializer()
