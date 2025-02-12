from enum import Enum


class Directories(Enum):
    DATA = "data"
    BACKUP = "backup"


class RegisterHeaders(Enum):
    DATE = "Fecha"
    CATEGORY = "Categoria"
    AMOUNT = "Importe"
    COMMENT = "Comentarios"


class FileIds(Enum):
    BOOK_FILE_PREFIX = "registers_"
    CATEGORIES_FILE_PREFIX = "categories_"


class DateElements(Enum):
    YEAR = "Año"
    MONTH = "Mes"
    DAY = "Día"
    FORMAT_DDMMYYYY = "%d-%m-%Y"
    FORMAT_YYYYMMDD = "%Y-%m-%d"
    FORMAT_DDMMYYYYHHMMSS = "%d-%m-%Y %H:%M:%S"


class WriteMode(Enum):
    BINARY = "wb"
    TEXT = "w"


class ReadMode(Enum):
    BINARY = "rb"
    TEXT = "r"


class FileExtensions(Enum):
    PICKLE = "pickle"
    JSON = "json"

    @classmethod
    def get_file_extension(cls, file_extension: str):
        if not isinstance(file_extension, str):
            raise TypeError("The file name must be a string")
        if file_extension.endswith("."):
            raise RuntimeError("File name must not end with '.'")
        elif "." in file_extension:
            return cls.get_file_extension(file_extension.split('.')[-1])
        match file_extension:
            case _ if file_extension in [extension.value for extension in cls]:
                return cls(file_extension)
            case _:
                raise NotImplementedError(f"File extension {file_extension} not encoded in {FileExtensions}")
