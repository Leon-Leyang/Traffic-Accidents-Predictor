import getpass
import pytz
from hdfs import InsecureClient
from globals import *
from pyspark.sql import SparkSession
from pyspark.sql.functions import udf, lit, when, col
from pyspark.sql.types import IntegerType, TimestampType, ArrayType
from pyspark.sql.functions import to_timestamp, from_utc_timestamp, explode

# Get the username
username = getpass.getuser()

# Initialize the hdfs client
hdfs_client = InsecureClient('http://localhost:9870', user=username)

# Initialize the spark session
spark = SparkSession.builder.appName("NLP vector generator").getOrCreate()


# Class to store weather data
class weather:
    date = ''
    temp = 0.0
    windchill = 0.0
    humid = 0.0
    pressure = 0.0
    visib = 0.0
    windspeed = 0.0
    winddir = ''
    precipitation = 0.0
    events = ''
    condition = ''

    def __init__(self, date, temp, windchill, humid, pressure, visib, windspeed, winddir,
                 precipitation, events, condition, zone):
        self.date = datetime.strptime(date, '%Y-%m-%d %I:%M:%S %p')
        self.date = self.date.replace(tzinfo=pytz.timezone(zone))
        self.temp = float(temp)
        self.windchill = float(windchill)
        self.humid = float(humid)
        self.pressure = float(pressure)
        self.visib = float(visib)
        self.windspeed = float(windspeed)
        self.winddir = winddir
        self.precipitation = float(precipitation)
        self.events = events
        self.condition = condition


# Class to store daylight data
class dayLight:
    sunrise = []
    sunset = []

    def __init__(self, sunrise, sunset):
        self.sunrise = sunrise
        self.sunset = sunset


# Function to return the index of the interval that the time stamp falls into
def return_interval_index(time_stamp, start, end):
    if time_stamp < start or time_stamp > end:
        return -1
    index = int(((time_stamp - start).days * 24 * 60 + (time_stamp - start).seconds / 60) / 15)
    return index


# Function to return the time in 24h format
def return_time(x):
    try:
        h = int(x.split(':')[0])
        m = int(x.split(':')[1].split(' ')[0])
        if 'pm' in x and h < 12:
            h = h + 12
        return [h, m]
    except:
        return [0, 0]


# Function to return if the given time is day or night
def returnDayLight(city_days_time, city, state, dt):
    sc = city + '-' + state
    days = city_days_time[sc]
    d = str(dt.year) + '-' + str(dt.month) + '-' + str(dt.day)
    if d in days:
        r = days[d]
        if ((r.sunrise[0] < dt.hour < r.sunset[0]) or
                (r.sunrise[0] <= dt.hour < r.sunset[0] and dt.minute >= r.sunrise[1]) or
                (r.sunrise[0] < dt.hour <= r.sunset[0] and dt.minute < r.sunset[1]) or
                (r.sunrise[0] <= dt.hour <= r.sunset[0] and r.sunrise[1] <= dt.minute < r.sunset[1])):
            return '1'
        else:
            return '0'


# Function to return three dictionaries
def proc_traffic_data(start, finish, begin, end):
    # Convert the datetime object to a string in the format 'YYYYMMDD'
    start_str = start.strftime('%Y%m%d')
    finish_str = finish.strftime('%Y%m%d')

    city_to_geohashes = {}
    geocode_to_airport = {}
    airport_to_timezone = {}

    # Calculate the total number of intervals
    total_interval = int(((end - begin).days * 24 * 60 + (end - begin).seconds / 60) / 15)

    # Create a dictionary to store the begin and end time of each time zone
    zone_to_be = {}
    for z in ['US/Eastern', 'US/Central', 'US/Mountain', 'US/Pacific']:
        t_begin = begin.replace(tzinfo=pytz.timezone(z))
        t_end = end.replace(tzinfo=pytz.timezone(z))
        zone_to_be[z] = [t_begin, t_end]

    # Define the event types
    event_types = ['Construction', 'Congestion', 'Accident', 'FlowIncident', 'Event', 'BrokenVehicle', 'RoadBlocked',
                   'Other']

    # Mapping from the column names in the raw data to the column names in the processed data
    name_conversion = {'Broken-Vehicle': 'BrokenVehicle', 'Flow-Incident': 'FlowIncident',
                       'Lane-Blocked': 'RoadBlocked'}

    # Wrapper function for the return_interval_index function
    return_interval_index_udf = udf(return_interval_index, IntegerType())

    # Function to convert the column names in the raw data to the column names in the processed data
    name_conversion_udf = udf(lambda t: name_conversion.get(t, t.split('-')[0]), StringType())

    # Calculate the interval range for each record using an array
    # If the event is an accident, the interval range is [start, start + 1), else it is [start, end + 1)
    interval_range_udf = udf(lambda event_type, start, end: [start, start + 1] if event_type == "Accident"
    else list(range(start, end + 1)), ArrayType(IntegerType()))

    for c in cities:
        z = time_zones[c]
        df = spark.read.csv(f"hdfs://localhost:9000/data/temp/T_{c}_{start_str}_{finish_str}.csv/*", header=True,
                            inferSchema=True)

        # Convert StartTime(UTC) to local time in the specified timezone
        df = df.withColumn(
            "StartTime(Local)",
            from_utc_timestamp(to_timestamp("StartTime(UTC)", "yyyy/MM/dd HH:mm:ss"), z)
        ).withColumn(
            "EndTime(Local)",
            from_utc_timestamp(to_timestamp("EndTime(UTC)", "yyyy/MM/dd HH:mm:ss"), z)
        )

        # Convert timestamp columns to Python datetime objects
        df = df.withColumn("StartTime(Local)", df["StartTime(Local)"].cast("timestamp")).withColumn(
            "EndTime(Local)", df["EndTime(Local)"].cast("timestamp")
        )

        # Calculate the start and end interval for each record
        df = df.withColumn(
            "StartInterval",
            return_interval_index_udf("StartTime(Local)", lit(zone_to_be[z][0]), lit(zone_to_be[z][1]))
        ).withColumn(
            "EndInterval",
            return_interval_index_udf("EndTime(Local)", lit(zone_to_be[z][0]).cast(TimestampType()),
                                      lit(zone_to_be[z][1]).cast(TimestampType()))
        )

        # Replace any -1 values in the "EndInterval" column with a default value of total_interval - 1
        # Filter the records with StartInterval not equal to -1
        df = df.withColumn(
            "EndInterval",
            when(df["EndInterval"] == -1, total_interval - 1).otherwise(df["EndInterval"])
        ).filter(df["StartInterval"] != -1)

        # Create a new column to store the geohash of the start location
        df = df.withColumn("Geohash", geohash_udf(col('LocationLat').cast('float'), col('LocationLng').cast('float')))

        # Create a new column to store the processed event type
        df = df.withColumn("EventType", name_conversion_udf("Type"))

        # Add a column for each event type and set the value to 1 if the EventType matches the column, otherwise 0
        for et in event_types:
            if et != "Other":
                df = df.withColumn(et, when(df["EventType"] == et, 1).otherwise(0))

        # Create the 'Other' column
        # If the EventType does not match any of the defined event types, set it to 1, otherwise 0
        not_matched_event_types = ~df["EventType"].isin(event_types)
        df = df.withColumn("Other", when(not_matched_event_types, 1).otherwise(0))

        # Calculate the interval range for each record
        df = df.withColumn("IntervalRange", interval_range_udf("EventType", "StartInterval", "EndInterval"))

        # Explode the dataframe by IntervalRange and groupBy Geohash and Interval
        df = df.selectExpr("Geohash", "IntervalRange", "AirportCode", *event_types).withColumn("Interval", explode(
            "IntervalRange")).drop("IntervalRange")
        df_grouped = df.groupBy("Geohash", "Interval").agg({et: "sum" for et in event_types})

        # Update city_to_geohashes dictionary
        city_to_geohashes[c] = {}

        grouped_rows = df_grouped.collect()
        for row in grouped_rows:
            geohash = row.Geohash
            interval = row.Interval
            if geohash not in city_to_geohashes[c]:
                city_to_geohashes[c][geohash] = [{} for _ in range(total_interval)]

            event_type_sums = {et: row[f"sum({et})"] for et in event_types}
            city_to_geohashes[c][geohash][interval] = event_type_sums


if __name__ == '__main__':
    # time interval to sample data for
    start = datetime(2018, 6, 1)
    finish = datetime(2018, 9, 2)

    begin = datetime.strptime('2018-06-01 00:00:00', '%Y-%m-%d %H:%M:%S')
    end = datetime.strptime('2018-08-31 23:59:59', '%Y-%m-%d %H:%M:%S')

    # Extract the traffic data for each city during the time interval
    extract_t_data_4city(spark, t_data_path, start, finish)

    # Process the traffic data
    proc_traffic_data(start, finish, begin, end)
