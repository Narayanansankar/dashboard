import gspread
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask, render_template, jsonify, request
import datetime
import json
import re
import os # <-- IMPORT THE OS MODULE

# --- Flask & Google Sheet Setup ---
app = Flask(__name__)

# --- Securely Connect to Google Sheets ---
# This setup works both locally and on Render.
# On Render, we will set an environment variable 'GOOGLE_CREDS_PATH'.
# Locally, it will fall back to using 'credentials.json'.
creds_path = os.environ.get('GOOGLE_CREDS_PATH', 'credentials.json')
live_sheet = None
history_sheet = None

try:
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
    client = gspread.authorize(creds)
    live_sheet = client.open("Tiruchendur_Parking_Lots_Info").worksheet("Sheet3")
    history_sheet = client.open("Tiruchendur_Parking_Lots_Info").worksheet("History1")
    print("Successfully connected to Google Sheets.")
except Exception as e:
    print(f"Error connecting to Google Sheets using path '{creds_path}': {e}")
    # In a real production app, you might want to handle this more gracefully
    # For now, we print the error and let the app run with sheets disabled.
    pass

# --- Helper Function to Fetch and Process Live Data ---
def get_parking_data():
    """
    Fetches data from the Google Sheet, cleans it, and transforms it into a
    structured format.
    """
    if not live_sheet:
        return {}

    ROUTE_MAP = {
        'TUT': 'Thoothukudi', 'TIN': 'Tirunelveli',
        'NGL': 'Nagercoil', 'VIP': 'VIP'
    }

    try:
        data = live_sheet.get_all_records(numericise_ignore=['all'])
        all_lots_data = {}

        for row in data:
            if not row.get('ParkingLotID'): continue
            
            cleaned_id = str(row['ParkingLotID']).strip().lower()
            if not cleaned_id: continue

            parking_name_raw = row.get('Parking Name', 'Unknown Lot').strip()
            parking_name_en = parking_name_raw

            processed_lot = {
                'ParkingLotID': cleaned_id,
                'Parking_name_en': parking_name_en,
                'Parking_name_ta': parking_name_en,
                'Location_Link': '#',
                'Photos_Link': '#',
                'Notes_en': '', 'Notes_ta': ''
            }

            try: total_capacity = int(row.get('Capacity', 0))
            except (ValueError, TypeError): total_capacity = 0

            try: current_vehicles = int(row.get('occupied space', 0))
            except (ValueError, TypeError): current_vehicles = 0
            
            processed_lot['TotalCapacity'] = total_capacity
            processed_lot['Current_Vehicle'] = current_vehicles

            is_available_val = str(row.get('Available/closed', 'AVAILABLE')).strip().upper()
            processed_lot['IsParkingAvailable'] = is_available_val == 'AVAILABLE'
            
            route_code = str(row.get('Route', '')).strip().upper()
            processed_lot['Route_en'] = ROUTE_MAP.get(route_code, 'Other')
            processed_lot['Route_ta'] = processed_lot['Route_en']

            if total_capacity > 0:
                processed_lot['Occupancy_Percent'] = (current_vehicles / total_capacity) * 100
            else:
                processed_lot['Occupancy_Percent'] = 0

            all_lots_data[cleaned_id] = processed_lot
            
        return all_lots_data
    except Exception as e:
        print(f"Error fetching/processing live data: {e}")
        return {}


# --- Flask Routes (API Endpoints) ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/parking-data')
def api_data():
    all_lots = get_parking_data()
    filtered_lots = [lot for lot in all_lots.values() if lot.get('Route_en') in ['Thoothukudi', 'Tirunelveli', 'Nagercoil']]
    response = {
        "last_updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data": filtered_lots
    }
    return jsonify(response)

@app.route('/api/overall-history')
def overall_history():
    if not history_sheet: return jsonify({"error": "History data source not available"}), 500
    try:
        all_history = history_sheet.get_all_records(numericise_ignore=['all'])
        live_parking_data_dict = get_parking_data()
    except Exception as e: return jsonify({"error": f"Could not fetch history data: {e}"}), 500

    id_to_route_map = { lot_id: data['Route_en'] for lot_id, data in live_parking_data_dict.items() }
    routes = ["Thoothukudi", "Tirunelveli", "Nagercoil"]
    timestamp_snapshots = {}
    time_24_hours_ago = datetime.datetime.now() - datetime.timedelta(hours=24)

    for record in all_history:
        try:
            lot_id = str(record.get('ParkingLotID', '')).strip().lower()
            timestamp_str = record.get('Timestamp')
            if not lot_id or not timestamp_str or lot_id not in id_to_route_map: continue
            
            record_datetime = datetime.datetime.strptime(timestamp_str, '%d/%m/%Y %H:%M:%S')
            if record_datetime >= time_24_hours_ago:
                ts_key = record_datetime.isoformat()
                if ts_key not in timestamp_snapshots: timestamp_snapshots[ts_key] = {}
                timestamp_snapshots[ts_key][lot_id] = int(record.get('Current_Vehicle', 0))
        except (ValueError, TypeError, KeyError): continue

    datasets = {route: [] for route in routes}
    sorted_timestamps = sorted(timestamp_snapshots.keys())
    latest_lot_counts = {lot_id: 0 for lot_id in id_to_route_map.keys()}

    for ts in sorted_timestamps:
        updates = timestamp_snapshots.get(ts, {})
        for lot_id, count in updates.items():
            if lot_id in latest_lot_counts: latest_lot_counts[lot_id] = count
        route_totals = {route: 0 for route in routes}
        for lot_id, count in latest_lot_counts.items():
            route_name = id_to_route_map.get(lot_id)
            if route_name in route_totals: route_totals[route_name] += count
        for route in routes:
            datasets[route].append({"x": ts, "y": route_totals[route]})
            
    colors = {
        "Thoothukudi": "rgba(29, 233, 182, 1)",
        "Tirunelveli": "rgba(255, 145, 0, 1)",
        "Nagercoil":   "rgba(30, 136, 229, 1)"
    }

    final_datasets = []
    for route in routes:
        final_datasets.append({"label": f'{route} Route Vehicle Count', "data": datasets[route], "borderColor": colors[route], "fill": False, "tension": 0.1, "pointRadius": 0})
    return jsonify({"datasets": final_datasets})

@app.route('/api/parking-lot-history')
def parking_lot_history():
    if not history_sheet: return jsonify({"error": "History data source not available"}), 500
    lot_id_from_request = str(request.args.get('id', '')).strip().lower()
    if not lot_id_from_request: return jsonify({"error": "Missing 'id' parameter"}), 400
    try: all_history = history_sheet.get_all_records()
    except Exception as e: return jsonify({"error": f"Could not fetch history data: {e}"}), 500

    all_lots = get_parking_data()
    lot_name = all_lots.get(lot_id_from_request, {}).get("Parking_name_en", "Unknown Lot")
    time_24_hours_ago = datetime.datetime.now() - datetime.timedelta(hours=24)
    graph_data = []
    for record in all_history:
        if str(record.get('ParkingLotID', '')).strip().lower() != lot_id_from_request: continue
        try:
            timestamp_str = record.get('Timestamp')
            if not timestamp_str: continue
            record_datetime = datetime.datetime.strptime(timestamp_str, '%d/%m/%Y %H:%M:%S')
            if record_datetime >= time_24_hours_ago:
                graph_data.append({"x": record_datetime.isoformat(), "y": float(record.get('Occupancy_Percent', 0))})
        except (ValueError, TypeError, KeyError): continue
            
    graph_data.sort(key=lambda p: p['x'])

    dataset = {
        "label": 'Occupancy (%)', 
        "data": graph_data, 
        "borderColor": 'rgba(0, 230, 118, 1)',
        "backgroundColor": 'rgba(0, 230, 118, 0.2)',
        "fill": True, 
        "tension": 0.1, 
        "pointRadius": 1, 
        "pointHoverRadius": 5
    }
    
    return jsonify({"lotName": lot_name, "datasets": [dataset]})


# Note: We remove the if __name__ == '__main__': block
# because Gunicorn will run the app.
