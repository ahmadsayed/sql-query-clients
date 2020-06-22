import sqlparse
from functools import wraps
import re

def format_sql(sql_stmt):
    """format SQL string to ensure proper content for string comparison

    Parameters
    ----------
    sql_stmt: str
    """
    url = re.search(r'(cos)://[^\s]*', sql_stmt)
    mapping = {}
    key_template = "key_{num}"
    index = 0
    while url:
        key = key_template.format(num=index)
        mapping[key] = url.group(0)
        sql_stmt = re.sub(r'(cos)://[^\s]*',
                          "{" + key + "}",
                          sql_stmt.rstrip(),
                          count=1)
        url = re.search(r'(cos)://[^\s]*', sql_stmt)
        index = index + 1
    sql_stmt = sqlparse.format(
        sql_stmt,
        keyword_case="upper",
        strip_comments=True,
        reindent=True,
    )
    sql_stmt = sql_stmt.format(**mapping)
    return sql_stmt

def print_sql(sql_stmt):
    print(format_sql(sql_stmt))

class TimeSeriesTransformInput():
    @classmethod
    def transform_sql(cls, f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            self = args[0]
            self._sql_stmt = TimeSeriesTransformInput.ts_segment_by_time(self._sql_stmt)
            result = f(*args, **kwargs)
            return result
        return wrapped

    @classmethod
    def ts_segment_by_time(cls, sql_stmt):
        """
        Help to transform from user-friendly query into library-friendly query

        Using either: `per_hour`

        .. code-block:: console

            ts_segment_by_time(ts, per_hour, per_hour)
            ts_segment_by_time(ts, hour, hour)

        or: using  ISO 8601 https://en.wikipedia.org/wiki/ISO_8601#Durations 
            P[n]Y[n]M[n]DT[n]H[n]M[n]S or P[n]W

        Example:

        .. code-block:: python

            ts_segment_by_time(ts, PT1H, PT1H)

        into

        .. code-block:: python

            ts_segment_by_time(ts, 3600000, 3600000)

        as ts_segment_by_time operates at mili-seconds level, hour=60*60*1000 miliseconds
        """
        sql_stmt = re.sub(r'ts_segment_by_time', 'TS_SEGMENT_BY_TIME', sql_stmt, flags=re.IGNORECASE)
        def handle_str_str(sql_stmt):
            h = re.compile(r'TS_SEGMENT_BY_TIME[\s]?\(([a-zA-Z0-9]+)[\s]?,[\s]?(?P<window>[a-zA-Z][a-zA-Z_0-9]+)[\s]?,[\s]?(?P<step>[a-zA-Z][a-zA-Z_0-9]+)', re.MULTILINE)
            return h
        def handle_str_number(sql_stmt):
            h = re.compile(r'TS_SEGMENT_BY_TIME[\s]?\(([a-zA-Z0-9]+)[\s]?,[\s]?(?P<window>[a-zA-Z][a-zA-Z_0-9]+)[\s]?,[\s]?(?P<step>[0-9]+)', re.MULTILINE)
            return h
        def handle_number_str(sql_stmt):
            h = re.compile(r'TS_SEGMENT_BY_TIME[\s]?\(([a-zA-Z0-9]+)[\s]?,[\s]?(?P<window>[0-9]+)[\s]?,[\s]?(?P<step>[a-zA-Z][a-zA-Z_0-9]+)', re.MULTILINE)
            return h
        def handle_result(h, sql_stmt):
            result = h.search(sql_stmt)
            while result:
                start_end = {}
                num = {}
                for i in [2, 3]:
                    if not result.group(i).isdigit():
                        if result.group(i).strip().lower() in ["per_hour", "hour"]:
                            num[i] = 60
                        elif result.group(i).strip().lower() in ["per_day", "day"]:
                            num[i] = 60*24
                        elif result.group(i).strip().lower() in ["per_week", "week"]:
                            num[i] = 60*24*7
                        else:
                            try:
                                import isodate
                                x = isodate.parse_duration(result.group(i).strip())
                                num[i] = int(x.total_seconds()/60)  # (minute)
                            except ISO8601Error:
                                print("%s unsupported" % result.group(i))
                                assert(0)
                        num[i] = num[i] * 60 * 1000 # (milliseconds)
                    else:
                        num[i] = result.group(i)
                    start_end[i] = result.span(i)
                sql_stmt = sql_stmt[:start_end[2][0]] + str(num[2]) + sql_stmt[start_end[2][1]:start_end[3][0]] + \
                    str(num[3]) +  sql_stmt[start_end[3][1]:]
                result = h.search(sql_stmt)
            return sql_stmt 

        h = handle_str_str(sql_stmt)
        sql_stmt = handle_result(h, sql_stmt)
        h = handle_number_str(sql_stmt)
        sql_stmt = handle_result(h, sql_stmt)
        h = handle_str_number(sql_stmt)
        sql_stmt = handle_result(h, sql_stmt)
        return sql_stmt

class TimeSeriesSchema():
    def __init__(self):
        # the list of column in unix-tme format
        """9999999999999 (13 digits) means Sat Nov 20 2286 17:46:39 UTC
        999999999999 (12 digits) means Sun Sep 09 2001 01:46:39 UTC
        99999999999 (11 digits) means Sat Mar 03 1973 09:46:39 UTC
        100000000000000 (15 digits) means Wed Nov 16 5138 09:46:40
        https://stackoverflow.com/questions/23929145/how-to-test-if-a-given-time-stamp-is-in-seconds-or-milliseconds
        """
        self._unixtime_columns = []

    @property
    def columns_in_unixtime(self ):
        return self._unixtime_columns
    @columns_in_unixtime.setter
    def columns_in_unixtime(self, column_list):
        self._unixtime_columns = column_list 


class SQLMagic(TimeSeriesSchema):

    def __init__(self):
        super().__init__()
        # contain the sql string that we can evoke using run() or submit()
        self._sql_stmt = ""
        self._has_stored_location = False
        self._has_with_clause = False
        self._has_select_clause = False
        self.supported_format_types = ["PARQUET", "CVS", "JSON"]

    @TimeSeriesTransformInput.transform_sql
    def print_sql(self):
        print_sql(self._sql_stmt)

    def reset_(self):
        self._sql_stmt = ""
        self._has_stored_location = False
        self._has_with_clause = False
        self._has_select_clause = False

    def with_(self, table_name, sql_stmt):
        if "WITH" not in self._sql_stmt:
            self._sql_stmt = self._sql_stmt + " WITH " + table_name + " AS (" + sql_stmt  + "\n) "
            self._has_with_clause = True
        else:
            self._sql_stmt = self._sql_stmt + ", " + table_name + " AS (" + sql_stmt  + "\n) "
        return self

    def select_(self,  columns):
        """
        Parameters
        ---------------
        columns: str
            a string representing a comma-separated list of columns
        """
        assert(self._has_select_clause is False)
        self._sql_stmt = self._sql_stmt + "SELECT " + columns
        self._has_select_clause = True
        return self

    def from_table_(self, table):
        self._sql_stmt = self._sql_stmt + " FROM " + table
        return self

    def from_cos_(self, cos_url, format_type="parquet"):
        self._sql_stmt = self._sql_stmt + " FROM " + cos_url + " STORED AS" + format_type
        return self

    def from_view_(self, sql_stmt):
        self._sql_stmt = self._sql_stmt + " FROM  ( \n" + sql_stmt + " \n) "
        return self

    def where_(self, condition):
        if "WHERE" not in self._sql_stmt:
            self._sql_stmt = self._sql_stmt + " WHERE " + condition
        else:
            self._sql_stmt = self._sql_stmt + ", " + condition
        return self

    def order_by_(self, columns):
        """
        Parameters
        ---------------
        condition: str
            a string representing a comma-separated list of columns
        """
        self._sql_stmt = self._sql_stmt + "ORDER BY " + columns
        return self

    def group_by_(self, columns):
        if "GROUP BY" not in self._sql_stmt:
            self._sql_stmt = self._sql_stmt + " GROUP BY " + columns
        else:
            self._sql_stmt = self._sql_stmt + ", " + columns
        return self

    def store_at_(self, cos_url, format_type=""):
        if self._has_stored_location is False:
            self._sql_stmt = self._sql_stmt + " INTO " + cos_url
            self._has_stored_location = True
        else:
            assert(0)
        if len(format_type) > 0 and format_type.upper() in self.supported_format_types:
            self._sql_stmt = self._sql_stmt + " STORED AS " + format_type.upper()
        return self

    # def store_as_(self, format_type="parquet"):
    #     if "INTO " not in self._sql_stmt:
    #         self._sql_stmt = self._sql_stmt + " INTO {cos_out} STORED AS " + format_type.upper()
    #     else:
    #         self._sql_stmt = self._sql_stmt + " STORED AS " + format_type.upper()
    #     return self

    def partition_objects_(self, num_objects):
        if "PARTITION" not in self._sql_stmt:
            self._sql_stmt = self._sql_stmt + " PARTITIONED INTO " + str(num_objects) + " OBJECTS"
        else:
            assert(0)
        return self

    def partition_rows_(self, num_rows):
        if "PARTITION" not in self._sql_stmt:
            self._sql_stmt = self._sql_stmt + " PARTITIONED INTO " + str(num_rows) + " ROWS"
        else:
            assert(0)
        return self

    @TimeSeriesTransformInput.transform_sql
    def format_(self):
        """Perform string replacement needed so that the final result is a SQL statement that is accepted by Spark SQL
        """
        return self