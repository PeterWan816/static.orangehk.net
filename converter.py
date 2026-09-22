import json
import os
import sys
import zipfile
import urllib.request
import geopandas as gpd
import polyline

# Hong Kong CSDI FGDB Download URL
CSDI_FGDB_URL = "https://static.csdi.gov.hk/csdi-webpage/download/7faa97a82780505c9673c4ba128fbfed/fgdb"


def download_and_extract_fgdb(url=CSDI_FGDB_URL, target_dir="FB_ROUTE.gdb"):
    """Downloads the FGDB zip file from CSDI and extracts it to target_dir."""
    zip_path = "dataset.zip"
    print(f"📥 Downloading FGDB dataset from CSDI...")
    
    # Download zip file with headers
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as response, open(zip_path, "wb") as out_file:
        out_file.write(response.read())

    print(f"📦 Downloaded {os.path.getsize(zip_path) / (1024 * 1024):.2f} MB. Extracting...")
    
    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        zip_ref.extractall(".")

    # Find the extracted .gdb folder dynamically
    extracted_gdb = None
    for root, dirs, _ in os.walk("."):
        for d in dirs:
            if d.endswith(".gdb"):
                extracted_gdb = os.path.join(root, d)
                break
        if extracted_gdb:
            break

    if extracted_gdb and extracted_gdb != target_dir:
        if os.path.exists(target_dir):
            import shutil
            shutil.rmtree(target_dir)
        os.rename(extracted_gdb, target_dir)

    # Clean up downloaded zip file
    if os.path.exists(zip_path):
        os.remove(zip_path)

    print(f"✔ FGDB ready at '{target_dir}'")


def clean_and_encode_geometry(geom):
    """
    Cleans coordinates (reorders to lat/lng, bounds-checks for Hong Kong,
    and deduplicates consecutive identical points) before encoding into Google Polyline strings.
    """
    encoded_lines = []
    lines = [geom] if geom.geom_type == "LineString" else list(geom.geoms)

    for line in lines:
        # GeoPandas coordinates are (longitude, latitude) -> Google Polyline expects (latitude, longitude)
        raw_coords = [(lat, lng) for lng, lat in line.coords]

        # 1. Filter out coordinates outside Hong Kong's geographic boundaries
        valid_coords = [
            (lat, lng)
            for lat, lng in raw_coords
            if 22.0 <= lat <= 22.6 and 113.7 <= lng <= 114.6
        ]

        if len(valid_coords) < 2:
            continue

        # 2. Deduplicate consecutive identical points (prevents '????' zero-delta strings)
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
    fgdb_path="FB_ROUTE.gdb",
    output_json_path="public/api/transport/BusStopTime/bus_routes_google.json",
    layer_name="FB_ROUTE_LINE",
):
    # Auto-fetch FGDB if missing
    if not os.path.exists(fgdb_path):
        download_and_extract_fgdb(target_dir=fgdb_path)

    print(f"1. Reading FGDB layer '{layer_name}' from '{fgdb_path}'...")
    gdf = gpd.read_file(fgdb_path, layer=layer_name)

    # Force source CRS if missing, then reproject from HK1980 Grid (EPSG:2326) to WGS84 (EPSG:4326)
    if gdf.crs is None:
        gdf.set_crs(epsg=2326, inplace=True)

    print(
        "2. Reprojecting HK1980 Grid (EPSG:2326) to WGS84 Lat/Lng (EPSG:4326)..."
    )
    gdf = gdf.to_crs(epsg=4326)

    print("3. Simplifying line geometries (~2.5m tolerance)...")
    gdf["geometry"] = gdf["geometry"].simplify(
        tolerance=0.000005, preserve_topology=True
    )

    processed_routes = []

    print("4. Encoding polylines and constructing compact JSON payload...")
    for _, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue

        encoded_polylines = clean_and_encode_geometry(geom)
        if not encoded_polylines:
            continue

        # Construct compact schema matching Swift GoogleRouteDTO
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

    # Automatically create output subdirectories if they do not exist
    output_dir = os.path.dirname(output_json_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    print(f"5. Saving to minified JSON file '{output_json_path}'...")
    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(processed_routes, f, separators=(",", ":"))

    file_size_mb = os.path.getsize(output_json_path) / (1024 * 1024)
    print("--------------------------------------------------")
    print(f"🎉 Success! Generated '{output_json_path}'")
    print(f"📊 Total Routes Converted: {len(processed_routes)}")
    print(f"📦 Output File Size: {file_size_mb:.2f} MB")
    print("--------------------------------------------------")


if __name__ == "__main__":
    # Allow overriding output path via CLI argument, otherwise default to public/api/...
    target_path = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "public/api/transport/BusStopTime/bus_routes_google.json"
    )
    convert_fgdb_to_google_json(output_json_path=target_path)
