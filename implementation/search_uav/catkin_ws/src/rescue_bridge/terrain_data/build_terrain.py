#!/usr/bin/env python3


import numpy as np
import struct
import gzip
import math
import os


CENTER_LAT = 22.356154
CENTER_LON = 114.187858
AREA_SIZE = 2400
TERRAIN_RES = 4.0
VERTICAL_RES = 8.0
MAX_POINTS = 500000
FLIGHT_CLEARANCE = 20.0


WGS84_A = 6378137.0
WGS84_E2 = 0.00669437999014

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))


def gps_to_local(lat, lon):

    lat1 = math.radians(CENTER_LAT)
    lat2 = math.radians(lat)
    dlat = lat2 - lat1
    dlon = math.radians(lon - CENTER_LON)

    sin_lat = math.sin((lat1 + lat2) / 2.0)
    W = math.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
    M = WGS84_A * (1.0 - WGS84_E2) / (W ** 3)
    N = WGS84_A / W

    north = dlat * M
    east = dlon * N * math.cos((lat1 + lat2) / 2.0)
    return east, north


def download_srtm():

    hgt_file = os.path.join(OUTPUT_DIR, "N22E114.hgt")
    if os.path.exists(hgt_file):
        print("Using cached SRTM file")
        with open(hgt_file, 'rb') as f:
            return f.read()

    print("Downloading SRTM N22E114 from AWS...")
    import requests
    url = 'https://elevation-tiles-prod.s3.amazonaws.com/skadi/N22/N22E114.hgt.gz'
    resp = requests.get(url, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"Download failed: {resp.status_code}")

    data = gzip.decompress(resp.content)
    with open(hgt_file, 'wb') as f:
        f.write(data)
    print(f"Saved {hgt_file} ({len(data)} bytes)")
    return data


def parse_srtm(data):

    n = 3601
    elev = np.frombuffer(data, dtype='>i2').reshape((n, n))


    margin_deg = 0.005
    half = AREA_SIZE / 2 / 111000 + margin_deg

    points = []
    for r in range(n):
        lat = 23.0 - r / (n - 1)
        if abs(lat - CENTER_LAT) > half:
            continue
        for c in range(n):
            lon = 114.0 + c / (n - 1)
            if abs(lon - CENTER_LON) > half:
                continue

            z = int(elev[r, c])
            if z <= 0 or z > 2000:
                continue

            x, y = gps_to_local(lat, lon)
            points.append((x, y, float(z)))

    return np.array(points, dtype=np.float64)


def bicubic_interpolate(sparse_points, resolution):

    from scipy.interpolate import griddata

    half = AREA_SIZE / 2
    n = int(AREA_SIZE / resolution)

    grid_x = np.linspace(-half, half, n)
    grid_y = np.linspace(-half, half, n)
    gx, gy = np.meshgrid(grid_x, grid_y)

    gz = griddata(
        sparse_points[:, :2], sparse_points[:, 2],
        (gx, gy), method='cubic', fill_value=np.nan
    )


    nan_mask = np.isnan(gz)
    if nan_mask.any():
        gz_nearest = griddata(
            sparse_points[:, :2], sparse_points[:, 2],
            (gx, gy), method='nearest'
        )
        gz[nan_mask] = gz_nearest[nan_mask]

    return gx, gy, gz


def build_terrain_pointcloud(gx, gy, gz):


    points = []
    min_z = float(np.nanmin(gz))
    gz_local = gz - min_z

    n_rows, n_cols = gz_local.shape

    for i in range(n_rows):
        for j in range(n_cols):
            x = gx[i, j]
            y = gy[i, j]
            z = gz_local[i, j]

            if np.isnan(z) or z < 0:
                continue


            points.append((x, y, z))


            if z > 3.0:
                for h in np.arange(0, z, VERTICAL_RES):
                    points.append((x, y, h))


            if i > 0 and j > 0 and i < n_rows - 1 and j < n_cols - 1:
                dz_dx = abs(gz_local[i + 1, j] - gz_local[i - 1, j]) / 2.0
                dz_dy = abs(gz_local[i, j + 1] - gz_local[i, j - 1]) / 2.0
                slope = max(dz_dx, dz_dy)


                if slope > 0.6:
                    for h in np.arange(0, z, VERTICAL_RES / 2):
                        points.append((x + 0.3, y, h))
                        points.append((x, y + 0.3, h))

    return np.array(points, dtype=np.float32)


def save_pcd(points, filepath):

    n = len(points)
    with open(filepath, 'w') as f:
        f.write("# .PCD v0.7 - Lion Rock Terrain\n")
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
            f.write(f"{p[0]:.2f} {p[1]:.2f} {p[2]:.2f}\n")

    size_mb = os.path.getsize(filepath) / 1024 / 1024
    print(f"Saved: {filepath} ({n:,} points, {size_mb:.1f} MB)")


def main():
    print("=" * 60)
    print("Lion Rock Real Terrain Builder")
    print(f"Center: ({CENTER_LAT}, {CENTER_LON})")
    print(f"Area: {AREA_SIZE}x{AREA_SIZE}m, Resolution: {TERRAIN_RES}m")
    print(f"Max points: {MAX_POINTS:,}")
    print("=" * 60)


    srtm_data = download_srtm()


    print("\nParsing SRTM data...")
    sparse = parse_srtm(srtm_data)
    print(f"Sparse DEM: {len(sparse)} points")
    print(f"  Elevation: {sparse[:, 2].min():.0f} - {sparse[:, 2].max():.0f}m ASL")


    print(f"\nInterpolating to {TERRAIN_RES}m grid...")
    gx, gy, gz = bicubic_interpolate(sparse, TERRAIN_RES)
    print(f"Grid: {gx.shape[0]}x{gx.shape[1]}")


    print("\nBuilding 3D point cloud...")
    points = build_terrain_pointcloud(gx, gy, gz)
    print(f"Raw points: {len(points):,}")


    if len(points) > MAX_POINTS:
        print(f"Downsampling to {MAX_POINTS:,}...")
        indices = np.random.choice(len(points), MAX_POINTS, replace=False)
        points = points[indices]


    pcd_path = os.path.join(OUTPUT_DIR, "lion_rock_terrain.pcd")
    save_pcd(points, pcd_path)


    print(f"\n{'='*40}")
    print(f"Terrain statistics:")
    print(f"  X: {points[:, 0].min():.0f} to {points[:, 0].max():.0f} m")
    print(f"  Y: {points[:, 1].min():.0f} to {points[:, 1].max():.0f} m")
    print(f"  Z: {points[:, 2].min():.0f} to {points[:, 2].max():.0f} m (relative)")
    print(f"  Total: {len(points):,} points")
    print(f"  Suggested flight alt: {points[:, 2].max() + FLIGHT_CLEARANCE:.0f}m+")


if __name__ == '__main__':
    main()
