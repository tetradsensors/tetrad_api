from datetime import datetime, timedelta
import json
import os
from aqandu import app, bq_client, bigquery
from dotenv import load_dotenv
from flask import request, jsonify

# Load in .env and set the table name
load_dotenv()
SENSOR_TABLE = os.getenv("BIGQ_SENSOR")
PROJECTID = os.getenv("PROJECTID")
POLMONID = os.getenv("POLMONID")

# Set the lookup for better returned variable names
varNameLookup = {
    'DEVICE_ID': 'ID',
    'PM25': 'pm25',
    'HUM': '\"Humidity (%)\"',
    'LAT': 'Latitude',
    'LON': 'Longitude',
    'VER': '\"Sensor Version\"',
    'MODEL': '\"Sensor Model\"',
    'TEMP': '\"Temp (*C)\"',
    'PM1': '\"pm1.0 (ug/m^3)\"',
    'PM10': '\"pm10.0 (ug/m^3)\"',
    'CO': 'CO',
    'NOX': 'NOX',
    'TIMESTAMP': 'time'
}

# Example request:
# 127.0.0.1:8080/api/rawDataFrom?id=M30AEA4EF9F88&sensorSource=Purple%20Air&start=2020-03-25T00:23:51Z&end=2020-03-26T00:23:51Z&show=pm25
@app.route("/api/rawDataFrom", methods = ["GET"])
def rawDataFrom():
    # Check that the arguments we want exist
    if not validateInputs(['id', 'sensorSource', 'start', 'end', 'show'], request.args):
        msg = 'Query string is missing an id and/or a sensorSource and/or a start and/or end date and/or a show'
        return msg, 400

    # Get the arguments from the query string
    id = request.args.get('id')
    sensor_source = request.args.get('sensorSource')
    start = request.args.get('start')
    end = request.args.get('end')
    show = request.args.get('show') # Data type (should be pm25)

    # Check that the data is formatted correctly
    if not validateDate(start) or not validateDate(end):
        resp = jsonify({'message': "Incorrect date format, should be %Y-%m-%dT%H:%M:%SZ, e.g.: 2018-01-03T20:00:00Z"})
        return resp, 400

    # Define the BigQuery query
    query = (
        "SELECT PM25, TIMESTAMP "
        f"FROM `{SENSOR_TABLE}` "
        f"WHERE DEVICE_ID = @id "
        f"AND TIMESTAMP >= @start "
        f"AND TIMESTAMP <= @end "
    )

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("id", "STRING", id),
            bigquery.ScalarQueryParameter("start", "TIMESTAMP", start),
            bigquery.ScalarQueryParameter("end", "TIMESTAMP", end),
        ]
    )

    # Run the query and collect the result
    measurements = []
    query_job = bq_client.query(query, job_config=job_config)
    rows = query_job.result()
    for row in rows:
        measurements.append({"pm25": row.PM25, "time": row.TIMESTAMP.strftime("%Y-%m-%dT%H:%M:%SZ")})
    tags = [{
        "ID": id,
        "Sensor Source": sensor_source,
        "SensorModel":"H1.2+S1.0.8",
        "time": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    }]
    return jsonify({"data": measurements, "tags": tags})

# Example request
# 127.0.0.1:8080/api/liveSensors?sensorType=all
@app.route("/api/liveSensors", methods = ["GET"])
def liveSensors():
    # Get the arguments from the query string
    sensor_type = request.args.get('sensorType')

    # Define the BigQuery query
    query = (
        "SELECT a.* "
        f"FROM `{SENSOR_TABLE}` AS a "
        "INNER JOIN ( "
            "SELECT DEVICE_ID AS ID, max(TIMESTAMP) AS LATEST_MEASUREMENT "
            f"FROM `{SENSOR_TABLE}` "
            "GROUP BY DEVICE_ID "
            ") AS b ON a.DEVICE_ID = b.ID AND a.TIMESTAMP = b.LATEST_MEASUREMENT "
    )

    # Run the query and collect the result
    sensor_list = []
    query_job = bq_client.query(query)
    rows = query_job.result()
    for row in rows:
        sensor_list.append({"ID": str(row.DEVICE_ID),
                            "Latitude": row.LAT,
                            "Longitude": row.LON,
                            "time": row.TIMESTAMP.timestamp() * 1000,
                            "pm1": row.PM1,
                            "pm25": row.PM25,
                            "pm10": row.PM10,
                            "Temperature": row.TEMP,
                            "Humidity": row.HUM,
                            "NOX": row.NOX,
                            "CO": row.CO,
                            "VER": row.VER,
                            "Sensor Source": "DAQ"})

    return jsonify(sensor_list)

# TODO: Fix this route
@app.route("/api/processedDataFrom", methods = ["GET"])
def processedDataFrom():
    # Get the arguments from the query string
    id = request.args.get('id')
    sensor_source = request.args.get('sensorSource')
    start = request.args.get('start')
    end = request.args.get('end')
    function = request.args.get('function')
    functionArg = request.args.get('functionArg')
    timeInterval = request.args.get('timeInterval')

    # Define the BigQuery query
    query = (
        "SELECT * "
        f"FROM `{SENSOR_TABLE}` "
    )

    # Run the query and collect the result
    query_job = bq_client.query(query)
    rows = query_job.result()
    for row in rows:
        sensor_list.append({"DEVICE_ID": str(row.DEVICE_ID),
                            "LAT": row.LAT,
                            "LON": row.LON,
                            "TIMESTAMP": str(row.TIMESTAMP),
                            "PM1": row.PM1,
                            "PM25": row.PM25,
                            "PM10": row.PM10,
                            "TEMP": row.TEMP,
                            "HUM": row.HUM,
                            "NOX": row.NOX,
                            "CO": row.CO,
                            "VER": row.VER})
    json_sensors = json.dumps(sensor_list, indent=4)
    return json_sensors

# Example request:
# 127.0.0.1:8080/api/lastValue?fieldKey=pm25
@app.route("/api/lastValue", methods = ["GET"])
def lastValue():
    # Get the arguments from the query string
    field_key = request.args.get('fieldKey')

    # Define the BigQuery query
    query = (
        "SELECT a.* "
        f"FROM `{SENSOR_TABLE}` AS a "
        "INNER JOIN ( "
            "SELECT DEVICE_ID AS ID, max(TIMESTAMP) AS LATEST_MEASUREMENT "
            f"FROM `{SENSOR_TABLE}` "
            "GROUP BY DEVICE_ID "
            ") AS b ON a.DEVICE_ID = b.ID AND a.TIMESTAMP = b.LATEST_MEASUREMENT "
    )

    # Run the query and collect the result
    sensor_list = []
    query_job = bq_client.query(query)
    rows = query_job.result()
    for row in rows:
        sensor_list.append({"ID": str(row.DEVICE_ID),
                            "Latitude": row.LAT,
                            "Longitude": row.LON,
                            "time": row.TIMESTAMP.timestamp() * 1000,
                            "pm1": row.PM1,
                            "pm25": row.PM25,
                            "pm10": row.PM10,
                            "Temperature": row.TEMP,
                            "Humidity": row.HUM,
                            "NOX": row.NOX,
                            "CO": row.CO,
                            "VER": row.VER,
                            "Sensor Source": "DAQ"})

    return jsonify(sensor_list)

# TODO: Fix this route
@app.route("/api/contours", methods = ["GET"])
def contours():
    # Get the arguments from the query string
    start = request.args.get('start')
    end = request.args.get('end')

    # Define the BigQuery query
    query = (
        "SELECT * "
        f"FROM `{SENSOR_TABLE}` "
    )

    # Run the query and collect the result
    query_job = bq_client.query(query)
    rows = query_job.result()
    for row in rows:
        sensor_list.append({"DEVICE_ID": str(row.DEVICE_ID),
                            "LAT": row.LAT,
                            "LON": row.LON,
                            "TIMESTAMP": str(row.TIMESTAMP),
                            "PM1": row.PM1,
                            "PM25": row.PM25,
                            "PM10": row.PM10,
                            "TEMP": row.TEMP,
                            "HUM": row.HUM,
                            "NOX": row.NOX,
                            "CO": row.CO,
                            "VER": row.VER})
    json_sensors = json.dumps(sensor_list, indent=4)
    return json_sensors

# TODO: Fix this route
@app.route("/api/getLatestContour", methods = ["GET"])
def getLatestContour():
    # Define the BigQuery query
    query = (
        "SELECT * "
        f"FROM `{SENSOR_TABLE}` "
    )

    # Run the query and collect the result
    query_job = bq_client.query(query)
    rows = query_job.result()
    for row in rows:
        sensor_list.append({"DEVICE_ID": str(row.DEVICE_ID),
                            "LAT": row.LAT,
                            "LON": row.LON,
                            "TIMESTAMP": str(row.TIMESTAMP),
                            "PM1": row.PM1,
                            "PM25": row.PM25,
                            "PM10": row.PM10,
                            "TEMP": row.TEMP,
                            "HUM": row.HUM,
                            "NOX": row.NOX,
                            "CO": row.CO,
                            "VER": row.VER})
    json_sensors = json.dumps(sensor_list, indent=4)
    return json_sensors

# TODO: Fix this route
@app.route("/api/getEstimatesForLocation", methods = ["GET"])
def getEstimatesForLocation():
    # Get the arguments from the query string
    location_lat = request.args.get('locationLat')
    location_lon = request.args.get('locationLon')
    start = request.args.get('start')
    end = request.args.get('end')

    # Define the BigQuery query
    query = (
        "SELECT * "
        f"FROM `{SENSOR_TABLE}` "
    )

    # Run the query and collect the result
    query_job = bq_client.query(query)
    rows = query_job.result()
    for row in rows:
        sensor_list.append({"DEVICE_ID": str(row.DEVICE_ID),
                            "LAT": row.LAT,
                            "LON": row.LON,
                            "TIMESTAMP": str(row.TIMESTAMP),
                            "PM1": row.PM1,
                            "PM25": row.PM25,
                            "PM10": row.PM10,
                            "TEMP": row.TEMP,
                            "HUM": row.HUM,
                            "NOX": row.NOX,
                            "CO": row.CO,
                            "VER": row.VER})
    json_sensors = json.dumps(sensor_list, indent=4)
    return json_sensors


# Example request:
# 127.0.0.1:8080/api/request_model_data?lat=40.7688&lon=-111.8462&radius=1&start_date=2020-03-10T0:0:0&end_date=2020-03-10T0:1:0
@app.route("/api/request_model_data/", methods=['GET'])
def request_model_data():
    query_parameters = request.args
    lat = query_parameters.get('lat')
    lon = query_parameters.get('lon')
    radius = query_parameters.get('radius')
    start_date = f"{query_parameters.get('start_date')} America/Denver"
    end_date = f"{query_parameters.get('end_date')} America/Denver"

    model_data = []
    # get the latest sensor data from each sensor
    query = (
        "SELECT * "
        "FROM ( "
            "SELECT "
                "Altitude," 
                "CO, "
                "Humidity, "
                "ID, "
                "Latitude, "
                "Longitude, "
                "PM10, "
                "PM2_5, "
                "SensorModel, "
                "Temperature, "
                "time, "
                "'AirU' AS Source "
            f"FROM `{PROJECTID}.{POLMONID}.airu_stationary` "
            "UNION ALL "
            "SELECT "
                "Altitude," 
                "CO, "
                "Humidity, "
                "ID, "
                "Latitude, "
                "Longitude, "
                "PM10, "
                "PM2_5, "
                "NULL AS SensorModel, "
                "Temperature, "
                "time, "
                "'DAQ' AS Source "
            f"FROM `{PROJECTID}.{POLMONID}.daq` "
            "UNION ALL "
            "SELECT "
                "Altitude, "
                "NULL AS CO, "
                "Humidity, "
                "ID, "
                "Latitude, "
                "Longitude, "
                "PM10, "
                "PM2_5, "
                "SensorModel, "
                "Temperature, "
                "time, "
                "'Purple Air' AS Source "
            f"FROM `{PROJECTID}.{POLMONID}.purpleair` "
        ") "
        "WHERE SQRT(POW(Latitude - @lat, 2) + POW(Longitude - @lon, 2)) <= @radius "
        "AND time > @start_date AND time < @end_date "
        "ORDER BY time ASC;"
    )

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("lat", "NUMERIC", lat),
            bigquery.ScalarQueryParameter("lon", "NUMERIC", lon),
            bigquery.ScalarQueryParameter("radius", "NUMERIC", radius),
            bigquery.ScalarQueryParameter("start_date", "TIMESTAMP", start_date),
            bigquery.ScalarQueryParameter("end_date", "TIMESTAMP", end_date),
        ]
    )

    query_job = bq_client.query(query, job_config = job_config)

    if query_job.error_result:
        print(query_job.error_result)
        return "Invalid API call - check documentation.", 400
    rows = query_job.result()  # Waits for query to finish

    for row in rows:
        model_data.append({
            "Altitude": row.Altitude,
            "CO": row.CO,
            "Humidity": row.Humidity,
            "ID": row.ID,
            "Latitude": row.Latitude,
            "Longitude": row.Longitude,
            "PM10": row.PM10,
            "PM2_5": row.PM2_5,
            "SensorModel": row.SensorModel,
            "Temperature": row.Temperature,
            "time": row.time,
            "Source": row.Source,
        })

    return jsonify(model_data)


# Helper function
def validateInputs(neededInputs, inputs):
    """Check that expected inputs are provided"""
    for anNeededInput in neededInputs:
        if anNeededInput not in inputs:
            return False
    return True

def validateDate(dateString):
    """Check if date string is valid"""
    return dateString == datetime.strptime(dateString, "%Y-%m-%dT%H:%M:%SZ").strftime("%Y-%m-%dT%H:%M:%SZ")
