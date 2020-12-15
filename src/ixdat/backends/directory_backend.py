import json
import numpy as np
from . import BackendBase
from ..config import STANDARD_DATA_DIRECTORY, STANDARD_SUFFIX, STANDARD_DATA_SUFFIX


def id_from_path(path):
    try:
        return int(path.stem.split("_")[0])
    except ValueError:
        return None


def name_from_path(path):
    return "".join(path.stem.split("_")[1:])


class DirBackend(BackendBase):
    """A database backend that loads and saves .ix files from a directory"""

    def __init__(
        self,
        directory=STANDARD_DATA_DIRECTORY,
        suffix=STANDARD_SUFFIX,
        data_suffix=STANDARD_DATA_SUFFIX,
    ):
        """Initialize a directory database backend with the directory as Path"""
        self.directory = directory
        self.suffix = suffix
        self.data_suffix = data_suffix

    @property
    def name(self):
        return f"DirBackend({self.directory})"

    def save(self, obj):
        if obj.data_objects:
            # save any data objects first as this may change the references
            for data_obj in obj.data_objects:
                self.save_data_obj(data_obj)
        try:
            table_name = obj.table_name
        except AttributeError:
            table_name = str(type(obj))
        obj_as_dict = obj.as_dict()
        obj.id = self.add_row(obj_as_dict, table_name=table_name)
        obj.backend_name = self.name
        return obj.id

    def open(self, cls, i):
        table_name = cls.table_name
        obj_as_dict = self.open_serialization(table_name, i)
        obj = cls.from_dict(obj_as_dict)
        obj.backend = self
        return obj

    def contains(self, table_name, i):
        return i in self.get_id_list(table_name)

    def load_obj_data(self, obj):
        if not hasattr(obj, "data"):
            # there's no data to be got or obj already has its data
            return
        path_to_row = self.get_path_to_row(obj.table_name, obj.id)
        try:
            return np.load(path_to_row.with_suffix(self.data_suffix))
        except FileNotFoundError:
            # there's no data to be got.
            return

    def save_data_obj(self, data_obj):
        table_name = data_obj.table_name
        if data_obj.backend == self and self.contains(table_name, data_obj.id):
            return data_obj.id  # already saved!
        obj_as_dict = data_obj.as_dict()
        data = obj_as_dict["data"]
        obj_as_dict["data"] = None
        data_obj.id = self.add_row(obj_as_dict, table_name=table_name)
        folder = self.directory / table_name
        data_obj.backend = self
        data_file_name = f"{data_obj.id}_{data_obj.name}.{self.data_suffix}"
        np.save(folder / data_file_name, data)
        return data_obj.id

    def add_row(self, obj_as_dict, table_name):
        folder = self.directory / table_name
        if not folder.exists():
            folder.mkdir()
        i = self.get_next_available_id(table_name)

        name = obj_as_dict["name"]
        file_name = f"{id}_{name}.{self.suffix}"

        with open(folder / file_name, "w") as f:
            json.dump(obj_as_dict, f)
        return i

    def open_serialization(self, table_name, i):
        path_to_row = self.get_path_to_row(table_name, i)
        with open(path_to_row, "r") as file:
            obj_as_dict = json.load(file)
        return obj_as_dict

    def get_path_to_row(self, table_name, i):
        folder = self.directory / table_name
        try:
            return next(
                p
                for p in folder.iterdir()
                if id_from_path(p) == i and p.suffix == self.suffix
            )
        except StopIteration:
            return None

    def get_id_list(self, table_name):
        folder = self.directory / table_name
        id_list = []
        for file in folder.iterdir():
            if not file.is_dir():
                try:
                    i = int(file.name.split("_")[0])
                    id_list.append(i)
                except TypeError:
                    pass
        return id_list

    def get_next_available_id(self, table_name):
        return max(self.get_id_list(table_name)) + 1

    def __eq__(self, other):
        if other is self:
            return True
        if (
            hasattr(other, "directory")
            and other.directory.resolve() == self.directory.resolve()
            and other.directory.lstat() == self.directory.lstat()
        ):
            return True
        return False