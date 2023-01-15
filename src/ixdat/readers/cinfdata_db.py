"""Module defining direct DB reader connection to Surfcat's legendary cinfdata system"""
import warnings
from .. import Measurement
from ..data_series import DataSeries, ValueSeries, TimeSeries, Field
from ..techniques import MSMeasurement
from ..techniques.ms import MSSpectrum
from ..techniques.reactor import ReactorMeasurement
from ..spectra import Spectrum, SpectrumSeries, MultiSpectrum
from ..config import plugins


class CinfdataDBReader:
    """A class that connects to cinf_database or read from cache
    https://cinfdata-dababase-client.readthedocs.io/en/latest/index.html

    Attributes:
        setup_name (str): The setup name in the DB
        timestamp (str): Timestamp when the experiment started in YYYY-MM-DD HH:MM:SS
        units (dict): Dictionary of columns names with corrosponding units
        tstamp (str): The unix time corresponding to t=0 for the measurement

        tstamp_list (list of float): list of epoch tstamps in the file's timestamp line

        column_tstamps (dict): The unix time corresponding to t=0 for each time column
        technique (str): The name of the technique
        column_names (list of str): The names of the data columns in the database
        t_and_v_cols (dict): {name: (tcol, vcol)} where name is the name of the
            ValueSeries (e.g. "M2"), tcol is the name of the corresponding time column
            in the file (e.g. "M2-x"), and vcol is the the name of the value column in
            the file (e.g. "M2-y).
        column_data (dict of str: np.array): The data in the file as a dict.
            Note that the np arrays are the same ones as in the measurement's DataSeries,
            so this does not waste memory.
        measurement (Measurement): The measurement returned by read() when the database
            is read. self.measurement is None before read() is called.
    """

    def __init__(self):
        """Initialize a Reader for cinf_database. See class docstring."""
        self.name = None
        self.sample_name = None
        self.setup_name = None
        self.timestamp = None
        self.tstamp = None
        self.tstamp_list = []
        self.column_tstamps = {}
        self.column_names = []
        self.t_and_v_cols = {}
        self.column_data = {}
        self.data_has_been_fetch = False
        self.metadata = {}
        self.technique = "MS"  # TODO: MS? Figure out how to tell if it's something else
        self.measurement_class = None  # MSMeasurement
        self.measurement = None
        self.cinf_db = None
        self.mass_scans = False

    def read(self, path_to_file, name=None, cls=None, units=None, **kwargs):
        """Return a xx-Measurement with the data and metadata recorded from
        a setup at SurfCat at given timestamp

        MSMeasurement contains a reference to the reader.

        All attributes of this reader can be accessed from the
        measurement as `measurement.reader.attribute_name`.

        Args:
            path_to_file (str): Named argument from Measurement Class.
                                Can be used as the setup name in the cinfdatabase
            **kwargs (dict): Key-word arguments are passed to cinf Measurement.__init__
                             setup_name (str): The setup name in the database
                             timestamp (str): Timestamp the measurement started
                                              given as (YYYY-MM-DD HH:MM:SS)
        """

        self.measurement_class = kwargs.pop("measurement_class", cls)
        self.setup_name = kwargs.pop("setup_name", path_to_file)
        self.timestamp = kwargs.pop("timestamp", None)
        self.comment = kwargs.pop("comment", None)
        self.mass_scans = kwargs.pop("include_mass_scans", False)
        self.verbose = kwargs.pop("verbose", False)
        self.grouping_column = kwargs.pop("group", None)

        if not self.grouping_column:
            if self.timestamp and not self.comment:
                self.grouping_column = "time"
                self.token = self.timestamp
            elif self.comment and not self.timestamp:
                self.grouping_column = "comment"
                self.token = self.comment
            else:
                warnings.warn("Both a comment and a timestamp is given "
                              "but no explicit grouping column is set. \n"
                              f"Defaults to 'time' using '{self.timestamp}'",
                              stacklevel=2)
                self.grouping_column = "time"
                self.token = self.timestamp

        plugins.activate_cinfdata()


        if (
            self.measurement_class == Spectrum
            or self.measurement_class == SpectrumSeries
            or self.measurement_class == MultiSpectrum
        ):
            return self.read_spectrums()

        elif (
            self.measurement_class == MSMeasurement
            or self.measurement_class == ReactorMeasurement
            or self.measurement_class == Measurement
        ):
            obj_as_dict = self.read_ms()
            if issubclass(cls, self.measurement_class):
                self.measurement_class = cls
            obj_as_dict.update(kwargs)
            self.measurement = self.measurement_class.from_dict(obj_as_dict)

            if self.mass_scans:
                if self.verbose:
                    print('adding mass scans to the measurement')
                self.measurement = self.add_mass_scans()

            #self.data_has_been_fetch = True

            return self.measurement

    def read_ms(self):
        """ Download MS data from cinfdata_database """

        self.cinf_db = plugins.cinfdata.connect(
            setup_name=self.setup_name, grouping_column=self.grouping_column
        )


        self.group_data = self.cinf_db.get_data_group(
            self.token, scaling_factors=(1e-3, None)
        )

        self.group_meta = self.cinf_db.get_metadata_group(self.token)
        self.meta = self.group_meta[list(self.group_meta.keys())[0]]

        self.set_sample_name()
        self.set_name()
        self.set_tstamp()


        if self.verbose:
            print("Retriving data from measurement named: ", self.sample_name)
            print("Measurement started recording on: ", self.timestamp)

        data_series_list = []
        if self.verbose:
            print("Column names in measurement: ")
        for key in self.group_data.keys():
            column_name = self.group_meta[key]["mass_label"]
            if self.verbose:
                print("Col name: ", column_name)
            #unixtime = self.group_meta[key]["unixtime"]
            #tstamp = float(unixtime)

            tcol = self.group_data[key][:, 0]
            vcol = self.group_data[key][:, 1]

            tseries = TimeSeries(
                name=column_name + "-x",
                unit_name=get_column_unit(column_name + "-x") or "s",
                data=tcol,
                tstamp=self.tstamp,
            )

            vseries = ValueSeries(
                name=column_name,
                data=vcol,
                tseries=tseries,
                unit_name=get_column_unit(column_name + "-y"),
            )
            data_series_list.append(tseries)
            data_series_list.append(vseries)

        obj_as_dict = dict(
            name=self.name,
            sample_name=self.sample_name,
            technique=self.technique,
            reader=self,
            series_list=data_series_list,
            tstamp=self.tstamp,
        )

        if not obj_as_dict:
            warnings.warn(f"No mass spec data was found using '{self.token}' "
                          f" and group_column: '{self.grouping_column}'",
                          stacklevel=2
                          )
            return None

        return obj_as_dict


    def read_spectrums(self, **kwargs):
        """ Download spectrums from cinfdata_database """
        db = plugins.cinfdata.connect(
            setup_name=self.setup_name, grouping_column=self.grouping_column
        )

        self.group_data = db.get_data_group(self.token)
        self.group_meta = db.get_metadata_group(self.token)
        self.meta = self.group_meta[list(self.group_meta.keys())[0]]

        self.set_sample_name()
        self.set_name()
        self.set_tstamp()

        spectrum_list = []
        for i, key in enumerate(self.group_meta.keys()):
            if self.group_meta[key]["type"] == 2:
                self.x_name = "Binding energy / eV"
                self.x_unit_name = "eV"
                self.field_name = "Counts per second"
                self.field_unit = "n/s"
                self.technique = "XPS"
                obj_as_dict = self.create_spectrum(key) # group_data, group_meta, key)
                obj_as_dict["name"] = self.group_meta[key]["name"]
                spectrum_list.append(self.measurement_class.from_dict(obj_as_dict))

            elif self.group_meta[key]["type"] == 4:
                self.x_name = "Mass [AMU]"
                self.x_unit_name = "m/z"
                self.field_name = "Current"
                self.field_unit = "[A]"
                self.technique = "MS_spectrum"
                obj_as_dict = self.create_spectrum(key) # group_data, group_meta, key)
                obj_as_dict["name"] = self.sample_name
                spectrum_list.append(self.measurement_class.from_dict(obj_as_dict))

            else:
                pass

        if not spectrum_list:
            warnings.warn(f"No spectrum was found using '{self.token}' "
                          f" and group_column: '{self.grouping_column}'",
                          stacklevel=2
                          )
            return None
        elif self.mass_scans:
            return spectrum_list
        elif len(spectrum_list) == 1:
            return spectrum_list[0]
        else:
            return SpectrumSeries.from_spectrum_list(spectrum_list)

    def create_spectrum(self, key): # group_data, group_meta, key):
        x_col = self.group_data[key][:, 0]
        y_col = self.group_data[key][:, 1]
        tstamp = self.group_meta[key]["unixtime"]

        xseries = DataSeries(data=x_col, name=self.x_name, unit_name=self.x_unit_name)
        field = Field(
            data=y_col,
            name=self.field_name,
            unit_name=self.field_unit,
            axes_series=[
                xseries,
            ],
        )

        obj_as_dict = {
            "sample_name": self.sample_name,
            "technique": self.technique,
            "field": field,
            "tstamp": tstamp,
        }

        return obj_as_dict

    def add_mass_scans(self):
        """Get corrosponding mass scans to mass_time from 'comment'"""
        self.measurement_class  = MSSpectrum
        self.grouping_column = 'comment'
        self.token = self.sample_name
        spectrum_list = self.read_spectrums()

        if self.verbose:
            print('Using ',self.measurement.time_series[-1],' to find end of experiment')
            print('Unixtime end of exp ', self.measurement.time_series[-1].data[-1] + self.tstamp)

        index = -1

        if spectrum_list[-1].tstamp > self.measurement.time_series[-1].data[-1] + self.tstamp:
            time = spectrum_list[0].tstamp
            while time < self.measurement.time_series[-1].data[-1] + self.tstamp:
                for i, spectrum in spectrum_list:
                    time = spectrum.tstamp
                    index = i-1
        if self.verbose:
            print('end index of spectrum list, ', index,'\n')
            print('tstamp of last spectrum in list, ', spectrum_list[index].tstamp)

        MSSpectra = SpectrumSeries.from_spectrum_list(spectrum_list[:index])

        SpectroMSMeasurement = MSSpectra.__add__(self.measurement)

        return SpectroMSMeasurement

    def set_sample_name(self):
        try:
            self.sample_name = self.meta["Comment"]
        except KeyError:
            try:
                self.sample_name = self.meta["comment"]
            except KeyError as e:
                self.sample_name = None
                print("No comment to set as sample_name. ", e)

    def set_name(self):
        self.name = self.meta["time"].strftime("%Y-%m-%d %H:%M:%S")

    def set_tstamp(self):
        self.tstamp = float(self.meta["unixtime"])




def get_column_unit(column_name):
    """Return the unit name of an ixdat column, i.e the part of the name after the '/'"""
    if column_name.startswith("M") and column_name.endswith("-y"):
        unit_name = "A"
    elif column_name.startswith("M") and column_name.endswith("-x"):
        unit_name = "s"
    elif column_name.startswith("Reactor") and column_name.endswith("pressure-y"):
        unit_name = "bar"
    elif not column_name.startswith("Reactor") and column_name.endswith("pressure-y"):
        unit_name = "mbar"
    elif column_name.endswith("temperature-y"):
        unit_name = "celcius"
    elif column_name.startswith("Flow"):
        unit_name = "ml/min"

    else:
        # TODO: Figure out how cinfdata represents units for other stuff.
        #    see https://github.com/ixdat/ixdat/pull/30/files#r811432543, and
        #    https://github.com/CINF/cinfdata/blob/master/sym-files2/export_data.py#L125
        unit_name = None
    return unit_name


