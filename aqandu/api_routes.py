from datetime import datetime, timedelta
import os
from aqandu import app, bq_client, bigquery, cache, utils, elevation_interpolator, gaussian_model_utils
from dotenv import load_dotenv
from flask import request, jsonify

# Load in .env and set the table name
load_dotenv()  # Required for compatibility with GCP, can't use pipenv there
AIRU_TABLE_ID = os.getenv("AIRU_TABLE_ID")
PURPLEAIR_TABLE_ID = os.getenv("PURPLEAIR_TABLE_ID")
DAQ_TABLE_ID = os.getenv("DAQ_TABLE_ID")
SOURCE_TABLE_MAP = {
    "AirU": AIRU_TABLE_ID,
    "PurpleAir": PURPLEAIR_TABLE_ID,
    "DAQ": DAQ_TABLE_ID,
}
VALID_SENSOR_SOURCES = ["AirU", "PurpleAir", "DAQ", "all"]


@app.route("/api/rawDataFrom", methods=["GET"])
def rawDataFrom():
    # Get the arguments from the query string
    id = request.args.get('id')
    sensor_source = request.args.get('sensorSource')
    start = request.args.get('start')
    end = request.args.get('end')

    # Check ID is valid
    if id == "" or id == "undefined":
        msg = "id is invalid. It must be a string that is not '' or 'undefined'."
        return msg, 400

    # Check that the arguments we want exist
    if sensor_source not in VALID_SENSOR_SOURCES:
        msg = f"sensor_source is invalid. It must be one of {VALID_SENSOR_SOURCES}"
        return msg, 400

    # Check that the data is formatted correctly
    if not utils.validateDate(start) or not utils.validateDate(end):
        msg = "Incorrect date format, should be {utils.DATETIME_FORMAT}, e.g.: 2018-01-03T20:00:00Z"
        return msg, 400

    # Define the BigQuery query
    query = f"""
        SELECT
            PM2_5,
            time
        FROM `{SOURCE_TABLE_MAP[sensor_source]}`
        WHERE ID = @id
            AND time >= @start
            AND time <= @end
        ORDER BY time
    """

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
        measurements.append({"PM2_5": row.PM2_5, "time": row.time.strftime(utils.DATETIME_FORMAT)})
    tags = [{
        "ID": id,
        "SensorSource": sensor_source,
        "SensorModel": "H1.2+S1.0.8",
        "time": datetime.utcnow().strftime(utils.DATETIME_FORMAT)
    }]
    return jsonify({"data": measurements, "tags": tags})


@app.route("/api/liveSensors", methods=["GET"])
@cache.cached(timeout=1800)
def liveSensors():
    print("fetching")
    # Get the arguments from the query string
    sensor_source = request.args.get('sensorSource')

    # Check that sensor_source is valid
    if sensor_source not in VALID_SENSOR_SOURCES:
        msg = f"sensor_source is invalid. It must be one of {VALID_SENSOR_SOURCES}"
        return msg, 400

    # Define the BigQuery query
    one_hour_ago = datetime.utcnow() - timedelta(hours=1)  # AirU + PurpleAir sensors have reported in the last hour
    three_hours_ago = datetime.utcnow() - timedelta(hours=3)  # DAQ sensors have reported in the 3 hours
    query_list = []

    if sensor_source == "AirU" or sensor_source == "all":
        query_list.append(
            f"""(
                SELECT a.ID, time, PM2_5, Latitude, Longitude, SensorModel, 'AirU' as SensorSource
                FROM `{AIRU_TABLE_ID}` as a
                INNER JOIN (
                    SELECT ID, max(time) AS LATEST_MEASUREMENT
                    FROM `{AIRU_TABLE_ID}`
                    WHERE time >= '{str(one_hour_ago)}'
                    GROUP BY ID
                ) AS b ON a.ID = b.ID AND a.time = b.LATEST_MEASUREMENT
                WHERE time >= '{str(one_hour_ago)}'
            )"""
        )

    if sensor_source == "PurpleAir" or sensor_source == "all":
        query_list.append(
            f"""(
                SELECT a.ID, time, PM2_5, Latitude, Longitude, '' as SensorModel, 'PurpleAir' as SensorSource
                FROM `{PURPLEAIR_TABLE_ID}` as a
                INNER JOIN (
                    SELECT ID, max(time) AS LATEST_MEASUREMENT
                    FROM `{PURPLEAIR_TABLE_ID}`
                    WHERE time >= '{str(one_hour_ago)}'
                    GROUP BY ID
                ) AS b ON a.ID = b.ID AND a.time = b.LATEST_MEASUREMENT
                WHERE time >= '{str(one_hour_ago)}'
            )"""
        )

    if sensor_source == "DAQ" or sensor_source == "all":
        query_list.append(
            f"""(
                SELECT a.ID, time, PM2_5, Latitude, Longitude, '' as SensorModel, 'DAQ' as SensorSource
                FROM `{DAQ_TABLE_ID}` as a
                INNER JOIN (
                    SELECT ID, max(time) AS LATEST_MEASUREMENT
                    FROM `{DAQ_TABLE_ID}`
                    WHERE time >= '{str(three_hours_ago)}'
                    GROUP BY ID
                ) AS b ON a.ID = b.ID AND a.time = b.LATEST_MEASUREMENT
                WHERE time >= '{str(three_hours_ago)}'
            )"""
        )

    # Build the actual query from the list of options
    query = " UNION ALL ".join(query_list)

    # Run the query and collect the result
    sensor_list = []
    query_job = bq_client.query(query)
    rows = query_job.result()
    for row in rows:
        sensor_list.append(
            {
                "ID": str(row.ID),
                "Latitude": row.Latitude,
                "Longitude": row.Longitude,
                "time": row.time,
                "PM2_5": row.PM2_5,
                "SensorModel": row.SensorModel,
                "SensorSource": row.SensorSource,
            }
        )

    return jsonify(sensor_list)


@app.route("/api/timeAggregatedDataFrom", methods=["GET"])
def timeAggregatedDataFrom():
    # Get the arguments from the query string
    id = request.args.get('id')
    sensor_source = request.args.get('sensorSource')
    start = request.args.get('start')
    end = request.args.get('end')
    function = request.args.get('function')
    timeInterval = request.args.get('timeInterval')  # Time interval in minutes

    SQL_FUNCTIONS = {
        "mean": "AVG",
        "min": "MIN",
        "max": "MAX",
    }

    # Check ID is valid
    if id == "" or id == "undefined":
        msg = "id is invalid. It must be a string that is not '' or 'undefined'."
        return msg, 400

    # Check that sensor_source is valid
    if sensor_source not in VALID_SENSOR_SOURCES:
        msg = f"sensor_source is invalid. It must be one of {VALID_SENSOR_SOURCES}"
        return msg, 400

    # Check aggregation function is valid
    if function not in SQL_FUNCTIONS:
        msg = f"function is not in {SQL_FUNCTIONS.keys()}"
        return msg, 400

    # Check that the data is formatted correctly
    if not utils.validateDate(start) or not utils.validateDate(end):
        msg = "Incorrect date format, should be {utils.DATETIME_FORMAT}, e.g.: 2018-01-03T20:00:00Z"
        return msg, 400

    # Define the BigQuery query
    tables_list = []
    if sensor_source == "AirU" or sensor_source == "all":
        tables_list.append(
            f"""(
                SELECT ID, time, PM2_5, Latitude, Longitude, SensorModel, 'AirU' as SensorSource
                FROM `{AIRU_TABLE_ID}`
                WHERE time >= @start
            )"""
        )

    if sensor_source == "PurpleAir" or sensor_source == "all":
        tables_list.append(
            f"""(
                SELECT ID, time, PM2_5, Latitude, Longitude, '' as SensorModel, 'PurpleAir' as SensorSource
                FROM `{PURPLEAIR_TABLE_ID}`
                WHERE time >= @start
            )"""
        )

    if sensor_source == "DAQ" or sensor_source == "all":
        tables_list.append(
            f"""(
                SELECT ID, time, PM2_5, Latitude, Longitude, '' as SensorModel, 'DAQ' as SensorSource
                FROM `{DAQ_TABLE_ID}`
                WHERE time >= @start
            )"""
        )

    query = f"""
        WITH
            intervals AS (
                SELECT
                    TIMESTAMP_ADD(@start, INTERVAL @interval * num MINUTE) AS lower,
                    TIMESTAMP_ADD(@start, INTERVAL @interval * 60* (1 + num) - 1 SECOND) AS upper
                FROM UNNEST(GENERATE_ARRAY(0,  DIV(TIMESTAMP_DIFF(@end, @start, MINUTE) , @interval))) AS num
            )
        SELECT
            CASE WHEN {SQL_FUNCTIONS.get(function)}(PM2_5) IS NOT NULL
                THEN {SQL_FUNCTIONS.get(function)}(PM2_5)
                ELSE 0
                END AS PM2_5,
            upper
        FROM intervals
            JOIN (
            {' UNION ALL '.join(tables_list)}
        ) sensors
            ON sensors.time BETWEEN intervals.lower AND intervals.upper
        WHERE ID = @id
        GROUP BY upper
        ORDER BY upper
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("id", "STRING", id),
            bigquery.ScalarQueryParameter("start", "TIMESTAMP", start),
            bigquery.ScalarQueryParameter("end", "TIMESTAMP", end),
            bigquery.ScalarQueryParameter("interval", "INT64", timeInterval),
        ]
    )

    # Run the query and collect the result
    measurements = []
    query_job = bq_client.query(query, job_config=job_config)
    rows = query_job.result()
    for row in rows:
        measurements.append({"PM2_5": row.PM2_5, "time": row.upper.strftime(utils.DATETIME_FORMAT)})

    tags = [{
        "ID": id,
        "SensorSource": sensor_source,
        "SensorModel": "H1.2+S1.0.8",
        "time": datetime.utcnow().strftime(utils.DATETIME_FORMAT)
    }]
    return jsonify({"data": measurements, "tags": tags})


# Gets data within radius of the provided lat lon within the time frame. The radius units are latlon degrees so this is an approximate bounding circle
def request_model_data_local(lat, lon, radius, start_date, end_date):
    model_data = []
    # get the latest sensor data from each sensor
    query = f"""
    SELECT *
    FROM
    (
        (
            SELECT ID, time, PM2_5, Latitude, Longitude, SensorModel, 'AirU' as SensorSource
            FROM `{AIRU_TABLE_ID}`
            WHERE time > @start_date AND time < @end_date
        )
        UNION ALL
        (
            SELECT ID, time, PM2_5, Latitude, Longitude, '' as SensorModel, 'PurpleAir' as SensorSource
            FROM `{PURPLEAIR_TABLE_ID}`
            WHERE time > @start_date AND time < @end_date
        )
        UNION ALL
        (
            SELECT ID, time, PM2_5, Latitude, Longitude, '' as SensorModel, 'DAQ' as SensorSource
            FROM `{DAQ_TABLE_ID}`
            WHERE time > @start_date AND time < @end_date
        )
    )
    WHERE SQRT(POW(Latitude - @lat, 2) + POW(Longitude - @lon, 2)) <= @radius
    AND time > @start_date AND time < @end_date
    ORDER BY time ASC
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("lat", "NUMERIC", lat),
            bigquery.ScalarQueryParameter("lon", "NUMERIC", lon),
            bigquery.ScalarQueryParameter("radius", "NUMERIC", radius),
            bigquery.ScalarQueryParameter("start_date", "TIMESTAMP", utils.datetimeToBigQueryTimestamp(start_date)),
            bigquery.ScalarQueryParameter("end_date", "TIMESTAMP", utils.datetimeToBigQueryTimestamp(end_date)),
        ]
    )

    query_job = bq_client.query(query, job_config=job_config)

    if query_job.error_result:
        print(query_job.error_result)
        return "Invalid API call - check documentation.", 400
    rows = query_job.result()  # Waits for query to finish

    for row in rows:
        model_data.append({
            "ID": str(row.ID),
            "Latitude": row.Latitude,
            "Longitude": row.Longitude,
            "time": row.time,
            "PM2_5": row.PM2_5,
            "SensorModel": row.SensorModel,
            "SensorSource": row.SensorSource,
        })

    return model_data

# Gets data within radius of the provided lat lon within the time frame. The radius units are latlon degrees so this is an approximate bounding circle
@app.route("/api/request_model_data/", methods=['GET'])
def request_model_data():
    query_parameters = request.args
    lat = query_parameters.get('lat')
    lon = query_parameters.get('lon')
    radius = query_parameters.get('radius')
    query_start_date = request.args.get('start_date')
    query_end_date = request.args.get('end_date')
    if not utils.validateDate(query_start_date) or not utils.validateDate(query_end_date):
        resp = jsonify({'message': f"Incorrect date format, should be {utils.DATETIME_FORMAT}, e.g.: 2018-01-03T20:00:00Z"})
        return resp, 400

    query_start_datetime = utils.parseDateString(query_start_date)
    query_end_datetime = utils.parseDateString(query_end_date)
    model_data = request_model_data_local(lat, lon, radius, query_start_datetime, query_end_datetime)
    return jsonify(model_data)


@app.route("/api/getPredictionsForLocation/", methods=['GET'])
def getPredictionsForLocation():
    # Check that the arguments we want exist
    # if not validateInputs(['lat', 'lon', 'predictionsperhour', 'start_date', 'end_date'], request.args):
    #     return 'Query string is missing one or more of lat, lon, predictionsperhour, start_date, end_date', 400

    # step -1, parse query parameters
    try:
        query_lat = float(request.args.get('lat'))
        query_lon = float(request.args.get('lon'))
        query_period = float(request.args.get('predictionsperhour'))
    except ValueError:
        return 'lat, lon, predictionsperhour must be floats.', 400

    query_start_date = request.args.get('start_date')
    query_end_date = request.args.get('end_date')

    # Check that the data is formatted correctly
    if not utils.validateDate(query_start_date) or not utils.validateDate(query_end_date):
        msg = f"Incorrect date format, should be {utils.DATETIME_FORMAT}, e.g.: 2018-01-03T20:00:00Z"
        return msg, 400

    query_start_datetime = utils.parseDateString(query_start_date)
    query_end_datetime = utils.parseDateString(query_end_date)

    print((
        f"Query parameters: lat={query_lat} lon={query_lon} start_date={query_start_datetime}"
        f" end_date={query_end_datetime} predictionsperhour={query_period}"
    ))

    # step 0, load up the bounding box from file and check that request is within it
    bounding_box_vertices = utils.loadBoundingBox('bounding_box.csv')
    print(f'Loaded {len(bounding_box_vertices)} bounding box vertices.')

    if not utils.isQueryInBoundingBox(bounding_box_vertices, query_lat, query_lon):
        return 'The query location is outside of the bounding box.', 400

    # step 1, load up correction factors from file
    correction_factors = utils.loadCorrectionFactors('correction_factors.csv')
    print(f'Loaded {len(correction_factors)} correction factors.')

    # step 2, load up length scales from file
    length_scales = utils.loadLengthScales('length_scales.csv')
    print(f'Loaded {len(length_scales)} length scales.')

    print('Loaded length scales:', length_scales, '\n')
    length_scales = utils.getScalesInTimeRange(length_scales, query_start_datetime, query_end_datetime)
    if len(length_scales) < 1:
        msg = (
            f"Incorrect number of length scales({len(length_scales)}) "
            f"found in between {query_start_datetime} and {query_end_datetime}"
        )
        return msg, 400

    latlon_length_scale = length_scales[0]['latlon']
    elevation_length_scale = length_scales[0]['elevation']
    time_length_scale = length_scales[0]['time']

    print(
        f'Using length scales: latlon={latlon_length_scale} elevation={elevation_length_scale} time={time_length_scale}'
    )

    # step 3, query relevent data
    APPROX_METERS_PER_LATLON_DEGREE_IN_UTAH = 70000
    radius = latlon_length_scale / APPROX_METERS_PER_LATLON_DEGREE_IN_UTAH  # convert meters to latlon degrees for db query
    sensor_data = request_model_data_local(
        lat=query_lat,
        lon=query_lon,
        radius=radius,
        start_date=query_start_datetime - timedelta(hours=time_length_scale),
        end_date=query_end_datetime + timedelta(hours=time_length_scale))

    unique_sensors = {datum['ID'] for datum in sensor_data}
    print(f'Loaded {len(sensor_data)} data points for {len(unique_sensors)} unique devices from bgquery.')

    # step 3.5, convert lat/lon to UTM coordinates
    try:
        utils.convertLatLonToUTM(sensor_data)
    except ValueError as err:
        return f'{str(err)}', 400

    sensor_data = [datum for datum in sensor_data if datum['zone_num'] == 12]

    unique_sensors = {datum['ID'] for datum in sensor_data}
    print((
        "After removing points with zone num != 12: "
        f"{len(sensor_data)} data points for {len(unique_sensors)} unique devices."
    ))

    # Step 4, parse sensor type from the version
    sensor_source_to_type = {'AirU': '3003', 'PurpleAir': '5003', 'DAQ': '5003'}
    for datum in sensor_data:
        datum['type'] = sensor_source_to_type[datum['SensorSource']]

    print(f'Fields: {sensor_data[0].keys()}')

    # step 4.5, Data Screening
    print('Screening data')
    sensor_data = utils.removeInvalidSensors(sensor_data)

    # step 5, apply correction factors to the data
    for datum in sensor_data:
        datum['PM2_5'] = utils.applyCorrectionFactor(correction_factors, datum['time'], datum['PM2_5'], datum['type'])

    # step 6, add elevation values to the data
    for datum in sensor_data:
        if 'Altitude' not in datum:
            datum['Altitude'] = elevation_interpolator([datum['Latitude']], [datum['Longitude']])[0]

    # step 7, Create Model
    model, time_offset = gaussian_model_utils.createModel(
        sensor_data, latlon_length_scale, elevation_length_scale, time_length_scale)

    # step 8, get predictions from model
    query_dates = utils.interpolateQueryDates(query_start_datetime, query_end_datetime, query_period)
    query_elevation = elevation_interpolator([query_lat], [query_lon])[0]
    predictions = gaussian_model_utils.predictUsingModel(
        model, query_lat, query_lon, query_elevation, query_dates, time_offset)

    return jsonify(predictions)