import getpass
import pickle
from hdfs import InsecureClient
from pyspark.sql import SparkSession
from pyspark.sql.functions import udf
from pyspark.ml import Pipeline
from pyspark.ml.feature import MinMaxScaler, VectorAssembler
from pyspark.sql.types import ArrayType, DoubleType


# Get the username
username = getpass.getuser()

# Initialize the hdfs client
hdfs_client = InsecureClient('http://localhost:9870', user=username)

# Initialize the spark session
spark = SparkSession.builder.appName("Vector cleaner and aggregator").getOrCreate()


# Function to process the poi data
def proc_poi_data(poi_path):
    # Select only the columns containing the POI vectors
    poi_cols = ['Amenity', 'Bump', 'Crossing', 'Give_Way', 'Junction', 'Noexit', 'Railway', 'Roundabout', 'Station',
                'Stop', 'Traffic_Calming', 'Traffic_Signal', 'Turning_Circle', 'Turning_Loop']

    # Read the CSV file into a PySpark dataframe and select the POI columns
    df = spark.read.csv(poi_path, header=True).select(poi_cols)

    # Scale the POI vectors using MinMaxScaler
    vec_assembler = VectorAssembler(inputCols=poi_cols, outputCol="features")
    scaler = MinMaxScaler(inputCol="features", outputCol="scaled_features", min=0, max=1)
    pipeline = Pipeline(stages=[vec_assembler, scaler])
    model = pipeline.fit(df)
    df = model.transform(df)

    # Convert the scaled POI vectors to NumPy arrays and store in a dictionary
    geohash_to_poi = {}
    to_array_udf = udf(lambda row: row.toArray().tolist(), ArrayType(DoubleType()))
    df = df.withColumn("scaled_array", to_array_udf("scaled_features"))
    for row in df.collect():
        geohash_to_poi[row["Geohash"]] = row["scaled_array"]

    # Map each unique geohash to an index in the dictionary
    geohash_to_idx = {}
    for i, geohash in enumerate(geohash_to_poi.keys()):
        geohash_to_idx[geohash] = i

    # Check if the file exists in HDFS
    # If it exists, delete it
    if hdfs_client.status(f"/data/temp/geo2poi.pickle", strict=False):
        hdfs_client.delete(f"/data/temp/geo2poi.pickle")

    # Save the geohash_to_poi data to HDFS using hdfs_client
    with hdfs_client.write(f"/data/temp/geo2poi.pickle") as writer:
        pickle.dump(geohash_to_poi, writer)

    # Check if the file exists in HDFS
    # If it exists, delete it
    if hdfs_client.status(f"/data/temp/geo2idx.pickle", strict=False):
        hdfs_client.delete(f"/data/temp/geo2idx.pickle")

    # Save the geohash_to_poi data to HDFS using hdfs_client
    with hdfs_client.write(f"/data/temp/geo2idx.pickle") as writer:
        pickle.dump(geohash_to_idx, writer)


if __name__ == '__main__':
    # Process the POI data
    poi_path = "hdfs://localhost:9000/data/poi/GeohashMap.csv"
    proc_poi_data(poi_path)
