import json
import os
import sys
import geopandas as gpd
import polyline


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
    output_json_path="/api/transport/BusStopTime/bus_routes_google.json",
    layer_name="FB_ROUTE_LINE",
):
    if not os.path.exists(fgdb_path):
        print(
            f"❌ Error: Folder '{fgdb_path}' not found in current directory."
        )
        sys.exit(1)

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
    convert_fgdb_to_google_json()
