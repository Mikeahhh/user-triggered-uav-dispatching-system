#!/usr/bin/env python3


import requests
import numpy as np
import struct
import os
import math


CENTER_LAT = 22.352
CENTER_LON = 114.183


HALF_SIZE_DEG = 0.0015


WGS84_A = 6378137.0
WGS84_E2 = 0.00669437999014

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))


def gps_to_local(lat, lon, ref_lat, ref_lon):

    lat1 = math.radians(ref_lat)
    lat2 = math.radians(lat)
    dlat = lat2 - lat1
    dlon = math.radians(lon - ref_lon)

    sin_lat = math.sin((lat1 + lat2) / 2.0)
    W = math.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
    M = WGS84_A * (1.0 - WGS84_E2) / (W ** 3)
    N = WGS84_A / W

    north = dlat * M
    east = dlon * N * math.cos((lat1 + lat2) / 2.0)
    return east, north


def download_srtm_from_opentopography():

    print("Downloading SRTM 30m from OpenTopography...")

    south = CENTER_LAT - HALF_SIZE_DEG * 2
    north = CENTER_LAT + HALF_SIZE_DEG * 2
    west = CENTER_LON - HALF_SIZE_DEG * 2
    east = CENTER_LON + HALF_SIZE_DEG * 2

    url = (
        f"https://portal.opentopography.org/API/globaldem"
        f"?demtype=SRTMGL1"
        f"&south={south}&north={north}&west={west}&east={east}"
        f"&outputFormat=AAIGrid"
    )

    resp = requests.get(url, timeout=60)
    if resp.status_code == 200 and 'ncols' in resp.text[:100]:
        filepath = os.path.join(OUTPUT_DIR, "lion_rock_srtm.asc")
        with open(filepath, 'w') as f:
            f.write(resp.text)
        print(f"Saved: {filepath}")
        return filepath
    else:
        print(f"OpenTopography failed (status={resp.status_code}), trying fallback...")
        return None


def download_hk_dtm():

    print("Trying HK Government 5m DTM...")

    url = "https://geodata.gov.hk/gs/api/v1.0.0/geoDataQuery?q=%7B%22table%22:%225mDTM%22%7D"
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200:
            print("HK DTM API responded, checking data...")
            return resp
    except Exception as e:
        print(f"HK DTM not available: {e}")
    return None


def generate_terrain_from_formula():


    print("Generating Lion Rock terrain from topographic model...")


    size = 300
    resolution = 1.0
    n = int(size / resolution)

    points = []

    for i in range(n):
        for j in range(n):
            x = (i - n / 2) * resolution
            y = (j - n / 2) * resolution


            z = 200 * math.exp(-((x - 0) ** 2 / (80 ** 2) + (y - 0) ** 2 / (60 ** 2)))

            z += 120 * math.exp(-((x - 60) ** 2 / (50 ** 2) + (y + 20) ** 2 / (40 ** 2)))

            z += 100 * math.exp(-((x + 50) ** 2 / (45 ** 2) + (y - 10) ** 2 / (35 ** 2)))

            z += 60 * math.exp(-((x + 20) ** 2 / (30 ** 2) + (y + 60) ** 2 / (25 ** 2)))

            z += 15 * math.sin(x / 30) * math.cos(y / 25)
            z += 8 * math.sin(x / 15 + 1) * math.cos(y / 12 + 0.5)


            z += 30

            points.append((x, y, z))

    return np.array(points, dtype=np.float32)


def parse_asc_file(filepath):

    with open(filepath, 'r') as f:
        lines = f.readlines()

    header = {}
    data_start = 0
    for i, line in enumerate(lines):
        parts = line.strip().split()
        if len(parts) == 2 and parts[0].lower() in ['ncols', 'nrows', 'xllcorner', 'yllcorner',
                                                       'xllcenter', 'yllcenter', 'cellsize', 'nodata_value']:
            header[parts[0].lower()] = float(parts[1])
            data_start = i + 1
        else:
            break

    ncols = int(header.get('ncols', 0))
    nrows = int(header.get('nrows', 0))
    xll = header.get('xllcorner', header.get('xllcenter', 0))
    yll = header.get('yllcorner', header.get('yllcenter', 0))
    cellsize = header.get('cellsize', 0.001)
    nodata = header.get('nodata_value', -9999)

    print(f"DEM: {ncols}x{nrows}, cellsize={cellsize}°, origin=({yll:.4f}, {xll:.4f})")

    elevation = []
    for line in lines[data_start:]:
        row = [float(v) for v in line.strip().split()]
        elevation.append(row)

    elevation = np.array(elevation)


    points = []
    min_elev = float('inf')

    for r in range(nrows):
        for c in range(ncols):
            z = elevation[r][c]
            if z == nodata or z < 0:
                continue

            lat = yll + (nrows - 1 - r) * cellsize
            lon = xll + c * cellsize

            x, y = gps_to_local(lat, lon, CENTER_LAT, CENTER_LON)

            if abs(x) <= 150 and abs(y) <= 150:
                if z < min_elev:
                    min_elev = z
                points.append((x, y, z))

    points = np.array(points, dtype=np.float32)


    if len(points) > 0 and min_elev < float('inf'):
        points[:, 2] -= min_elev
        print(f"Elevation range: 0 - {points[:, 2].max():.1f}m (offset by {min_elev:.0f}m)")

    return points


def densify_terrain(sparse_points, resolution=2.0, area_size=300):


    from scipy.interpolate import griddata

    n = int(area_size / resolution)
    grid_x = np.linspace(-area_size / 2, area_size / 2, n)
    grid_y = np.linspace(-area_size / 2, area_size / 2, n)
    gx, gy = np.meshgrid(grid_x, grid_y)


    gz = griddata(
        sparse_points[:, :2], sparse_points[:, 2],
        (gx, gy), method='cubic', fill_value=0
    )

    all_points = []

    for i in range(n):
        for j in range(n):
            x, y, z = gx[i, j], gy[i, j], gz[i, j]

            if z < 0.5:
                continue


            all_points.append((x, y, z))


            for h in np.arange(0, z, 2.0):
                all_points.append((x, y, h))

    return np.array(all_points, dtype=np.float32)


def save_pcd(points, filepath):

    n = len(points)
    with open(filepath, 'w') as f:
        f.write("# .PCD v0.7 - Point Cloud Data\n")
        f.write("VERSION 0.7\n")
        f.write("FIELDS x y z\n")
        f.write("SIZE 4 4 4\n")
        f.write("TYPE F F F\n")
        f.write("COUNT 1 1 1\n")
        f.write(f"WIDTH {n}\n")
        f.write("HEIGHT 1\n")
        f.write("VIEWPOINT 0 0 0 1 0 0 0\n")
        f.write(f"POINTS {n}\n")
        f.write("DATA ascii\n")
        for p in points:
            f.write(f"{p[0]:.3f} {p[1]:.3f} {p[2]:.3f}\n")

    print(f"Saved PCD: {filepath} ({n} points, {os.path.getsize(filepath) / 1024 / 1024:.1f} MB)")


def main():
    print("=" * 60)
    print("Lion Rock Terrain Generator for EGO-Planner")
    print(f"Center: ({CENTER_LAT}, {CENTER_LON})")
    print(f"Area: 300m x 300m")
    print("=" * 60)


    points = None

    asc_file = download_srtm_from_opentopography()
    if asc_file:
        sparse_points = parse_asc_file(asc_file)
        if len(sparse_points) > 10:
            print(f"Got {len(sparse_points)} DEM points, densifying...")
            points = densify_terrain(sparse_points, resolution=2.0)
            print(f"Densified to {len(points)} points")

    if points is None or len(points) < 100:
        print("Using topographic model fallback...")
        sparse_points = generate_terrain_from_formula()
        points = densify_terrain(sparse_points, resolution=2.0)


    MAX_POINTS = 500000
    if len(points) > MAX_POINTS:
        print(f"Downsampling from {len(points)} to {MAX_POINTS} points...")
        indices = np.random.choice(len(points), MAX_POINTS, replace=False)
        points = points[indices]


    pcd_path = os.path.join(OUTPUT_DIR, "lion_rock_terrain.pcd")
    save_pcd(points, pcd_path)


    print(f"\nTerrain stats:")
    print(f"  X range: {points[:, 0].min():.1f} to {points[:, 0].max():.1f} m")
    print(f"  Y range: {points[:, 1].min():.1f} to {points[:, 1].max():.1f} m")
    print(f"  Z range: {points[:, 2].min():.1f} to {points[:, 2].max():.1f} m")
    print(f"  Total points: {len(points)}")
    print(f"\nDone! PCD saved to: {pcd_path}")


if __name__ == '__main__':
    main()
