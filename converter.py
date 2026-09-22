import json
import os
import sys
import zipfile
import requests
import geopandas as gpd
import fiona
import polyline

CSDI_FGDB_URL = "https://static.csdi.gov.hk/csdi-webpage/download/7faa97a82780505c9673c4ba128fbfed/fgdb"


def download_and_extract_fgdb(url=CSDI_FGDB_URL):
    """Downloads Bus_Route_FGDB.zip, extracts it, and returns the path to the .gdb folder."""
    zip_path = "Bus_Route_FGDB.zip"
    print("1. Downloading FGDB dataset from CSDI...")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    try:
        response = requests.get(url, headers=headers, stream=True, timeout=60)
        response.raise_for_status()

        with open(zip_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        print(f"   📦 Downloaded {os.path.getsize(zip_path) / (1024 * 1024):.2f} MB. Extracting...")

        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            zip_ref.extractall(".")

    except Exception as e:
        print(f"❌ Download or Unzip Error: {e}")
        print("📁 Directory listing on error:")
        print(os.listdir("."))
        sys.exit(1)

    # Delete the zip file
    if os.path.exists(zip_path):
        os.remove(zip_path)

    # PRINT DIRECTORY CONTENTS FOR DEBUGGING (ls equivalent)
    print(f"📁 Extracted working directory contents (ls): {os.listdir('.')}")

    # Search for extracted .gdb folder
    gdb_path = None
    for root, dirs, _ in os.walk("."):
        for d in dirs:
            if d.endswith(".gdb"):
                gdb_path = os.path.normpath(os.path.join(root, d))
                break
        if gdb_path:
            break

    if gdb_path and os.path.exists(gdb_path):
        print(f"   ✔ Found GDB folder at '{gdb_path}'")
        return gdb_path

    # If not found, print full recursive directory tree
    print("❌ Error: No .gdb folder found after unzipping!")
    print("📁 Recursive Directory Tree:")
    for root, dirs, files in os.walk("."):
        print(f"   - {root}/ -> dirs: {dirs} | files: {files[:3]}")
    sys.exit(1)


def clean_and_encode_geometry(geom):
    """Safely extracts 2D/3D coordinates and encodes them into Google Polyline strings."""
    encoded_lines = []
    
    if geom.geom_type in ["LineString", "LineStringZ"]:
        lines = [geom]
    elif hasattr(geom, "geoms"):
        lines = list(geom.geoms)
    else:
        return encoded_lines

    for line in lines:
        raw_coords = [(c[1], c[0]) for c in line.coords]

        valid_coords = [
            (lat, lng)
            for lat, lng in raw_coords
            if 22.0 <= lat <= 22.6 and 113.7 <= lng <= 114.6
        ]

        if len(valid_coords) < 2:
            continue

        deduped = [valid_coords[0]]
        for pt in valid_coords[1:]:
            if (
                abs(pt[0] - deduped[-1][0]) > 1e-6
                or abs(pt[1] - deduped[-1][1]) > 1e-6
            ):
                deduped.append(pt)

        if len(deduped) >= 2:
            encoded_lines.append(polyline.encode(deduped))

    return encoded_lines


def convert_fgdb_to_google_json(
    output_json_path="api/transport/BusStopTime/bus_routes_google.json",
    preferred_layer="FB_ROUTE_LINE",
):
    fgdb_path = download_and_extract_fgdb()

    available_layers = fiona.listlayers(fgdb_path)
    print(f"2. Available layers in '{fgdb_path}': {available_layers}")

    layer_to_use = preferred_layer if preferred_layer in available_layers else available_layers[0]
    print(f"3. Reading layer '{layer_to_use}'...")

    gdf = gpd.read_file(fgdb_path, layer=layer_to_use)

    if gdf.crs is None:
        gdf.set_crs(epsg=2326, inplace=True)

    print("4. Reprojecting HK1980 Grid (EPSG:2326) to WGS84 Lat/Lng (EPSG:4326)...")
    gdf = gdf.to_crs(epsg=4326)

    print("5. Simplifying line geometries...")
    gdf["geometry"] = gdf["geometry"].simplify(
        tolerance=0.000005, preserve_topology=True
    )

    processed_routes = []

    print("6. Encoding polylines and constructing payload...")
    for _, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue

        encoded_polylines = clean_and_encode_geometry(geom)
        if not encoded_polylines:
            continue

        route_payload = {
            "r_id": str(row.get("ROUTE_ID") or ""),
            "r_num": str(row.get("ROUTE_NAMEE") or "").strip().upper(),
            "seq": int(row.get("ROUTE_SEQ") or 1),
            "co": str(row.get("COMPANY_CODE") or "").strip().upper(),
            "p": (
                encoded_polylines[0]
                if len(encoded_polylines) == 1
                else encoded_polylines
            ),
        }

        processed_routes.append(route_payload)

    output_dir = os.path.dirname(output_json_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    print(f"7. Saving to minified JSON file '{output_json_path}'...")
    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(processed_routes, f, separators=(",", ":"))

    file_size_mb = os.path.getsize(output_json_path) / (1024 * 1024)
    print("--------------------------------------------------")
    print(f"🎉 Success! Generated '{output_json_path}'")
    print(f"📊 Total Routes Converted: {len(processed_routes)}")
    print(f"📦 Output File Size: {file_size_mb:.2f} MB")
    print("--------------------------------------------------")


if __name__ == "__main__":
    target_path = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "api/transport/BusStopTime/bus_routes_google.json"
    )
    convert_fgdb_to_google_json(output_json_path=target_path)
