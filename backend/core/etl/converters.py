"""
SIVAG — core/etl/converters.py
================================
Conversores de formatos nativos a formatos procesados.

  Excel   (.xlsx) → GeoJSON + filas AtributoTabular
  CSV     (.csv)  → GeoJSON + filas AtributoTabular  (mismo resultado que Excel)
  Shape   (.zip)  → GeoJSON reproyectado a EPSG:4326
  GeoTIFF (.tiff) → GeoTIFF original con overviews añadidos
                    (NO se convierte, se preserva toda la información)

Requisito IEEE: RF-03 — Conversión Automática
"""

from __future__ import annotations

import os
import shutil
from typing import Any

import fiona
import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.warp import Resampling

# EPSG de salida estándar para vectores
TARGET_CRS = "EPSG:4326"


# ─────────────────────────────────────────────────────────
# Helpers de rutas
# ─────────────────────────────────────────────────────────

def _geojson_output_path(media_root: str, proyecto_id: str, capa_id: str) -> str:
    """Ruta absoluta para el GeoJSON generado. Crea el directorio si no existe."""
    dir_path = os.path.join(media_root, "geojson", str(proyecto_id))
    os.makedirs(dir_path, exist_ok=True)
    return os.path.join(dir_path, f"{capa_id}.geojson")


def _raster_output_path(media_root: str, proyecto_id: str, capa_id: str) -> str:
    """
    Ruta absoluta para el GeoTIFF procesado.
    Se guarda con el mismo nombre que el original pero en la carpeta de rasters.
    """
    dir_path = os.path.join(media_root, "rasters", str(proyecto_id))
    os.makedirs(dir_path, exist_ok=True)
    return os.path.join(dir_path, f"{capa_id}.tiff")


# ─────────────────────────────────────────────────────────
# Helper interno: DataFrame → GeoJSON + AtributoTabular
# ─────────────────────────────────────────────────────────

def _dataframe_to_geojson(
    df: pd.DataFrame,
    col_lat: str,
    col_lon: str,
    output_path: str,
) -> dict:
    """
    Convierte un DataFrame (Excel o CSV) a GeoJSON de puntos.
    Lógica compartida para ambos formatos tabulares.

    Retorna:
      geojson_path   : ruta absoluta del GeoJSON generado
      num_features   : cantidad de puntos
      columnas_schema: {col: dtype_str, ...}
      filas          : lista de dicts para crear AtributoTabular
    """
    df[col_lat] = pd.to_numeric(df[col_lat], errors="coerce")
    df[col_lon] = pd.to_numeric(df[col_lon], errors="coerce")
    df = df.dropna(subset=[col_lat, col_lon])

    gdf = gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df[col_lon], df[col_lat]),
        crs=TARGET_CRS,
    )

    prop_cols = [c for c in df.columns if c not in [col_lat, col_lon]]

    geojson_str = gdf[prop_cols + ["geometry"]].to_json(
        show_bbox=True,
        drop_id=False,
    )

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(geojson_str)

    filas = []
    for idx, row in df.iterrows():
        datos = {col: _serialize_value(row[col]) for col in prop_cols}
        filas.append({
            "indice": int(idx),
            "lat":    float(row[col_lat]),
            "lon":    float(row[col_lon]),
            "datos":  datos,
        })

    return {
        "geojson_path":    output_path,
        "num_features":    len(df),
        "columnas_schema": {col: str(df[col].dtype) for col in df.columns},
        "filas":           filas,
    }


def _serialize_value(val: Any) -> Any:
    """Convierte valores pandas a tipos JSON-serializables."""
    if pd.isna(val):
        return None
    if isinstance(val, np.integer):
        return int(val)
    if isinstance(val, np.floating):
        return float(val)
    if isinstance(val, pd.Timestamp):
        return val.isoformat()
    return val


# ─────────────────────────────────────────────────────────
# 1. Excel → GeoJSON
# ─────────────────────────────────────────────────────────

def convert_excel_to_geojson(
    filepath: str,
    col_lat: str,
    col_lon: str,
    output_path: str,
) -> dict:
    """
    Convierte un Excel (.xlsx) validado a GeoJSON de puntos.
    Delega la lógica a _dataframe_to_geojson.
    """
    df = pd.read_excel(filepath, engine="openpyxl")
    return _dataframe_to_geojson(df, col_lat, col_lon, output_path)


# ─────────────────────────────────────────────────────────
# 2. CSV → GeoJSON
# ─────────────────────────────────────────────────────────

def convert_csv_to_geojson(
    filepath: str,
    col_lat: str,
    col_lon: str,
    separador: str,
    encoding: str,
    output_path: str,
) -> dict:
    """
    Convierte un CSV validado a GeoJSON de puntos.
    Usa el separador y encoding detectados por el validador.
    Delega la lógica a _dataframe_to_geojson.
    """
    df = pd.read_csv(filepath, sep=separador, encoding=encoding)
    return _dataframe_to_geojson(df, col_lat, col_lon, output_path)


# ─────────────────────────────────────────────────────────
# 3. Shapefile → GeoJSON (reproyectado a EPSG:4326)
# ─────────────────────────────────────────────────────────

def convert_shapefile_to_geojson(
    zip_filepath: str,
    shp_filename: str,
    output_path: str,
) -> dict:
    """
    Convierte un Shapefile (ZIP) a GeoJSON reproyectado a EPSG:4326.

    Retorna:
      geojson_path   : ruta absoluta del GeoJSON generado
      num_features   : cantidad de features
      tipo_geometria : PUNTO | LINEA | POLIGONO | MIXTO
      crs_original   : string del CRS de origen
      columnas_schema: {campo: tipo, ...}
    """
    vsi_path = f"/vsizip/{zip_filepath}/{shp_filename}"
    gdf = gpd.read_file(vsi_path)

    crs_original = str(gdf.crs) if gdf.crs else "Desconocido"

    if gdf.crs and not gdf.crs.equals(TARGET_CRS):
        gdf = gdf.to_crs(TARGET_CRS)

    geom_types = gdf.geometry.geom_type.unique().tolist()
    tipo = _dominant_geom_type(geom_types)

    for col in gdf.select_dtypes(include=["datetime64"]).columns:
        gdf[col] = gdf[col].astype(str)

    gdf.to_file(output_path, driver="GeoJSON")

    columnas_schema = {
        col: str(gdf[col].dtype)
        for col in gdf.columns
        if col != "geometry"
    }

    return {
        "geojson_path":    output_path,
        "num_features":    len(gdf),
        "tipo_geometria":  tipo,
        "crs_original":    crs_original,
        "columnas_schema": columnas_schema,
    }


def _dominant_geom_type(types: list[str]) -> str:
    clean = set()
    for t in types:
        t_norm = t.upper().replace("MULTI", "").strip() if t else ""
        clean.add(t_norm)

    if len(clean) == 1:
        mapping_type = {
            "POINT":      "PUNTO",
            "LINESTRING": "LINEA",
            "POLYGON":    "POLIGONO",
        }
        return mapping_type.get(clean.pop(), "MIXTO")
    return "MIXTO"


# ─────────────────────────────────────────────────────────
# 4. GeoTIFF → GeoTIFF con overviews (sin conversión)
# ─────────────────────────────────────────────────────────

def process_geotiff_native(
    input_filepath: str,
    output_path: str,
) -> dict:
    """
    Procesa un GeoTIFF preservando TODA la información original.

    No convierte a GeoJSON. No cambia el CRS. No altera los valores de banda.
    Únicamente:
      1. Copia el archivo a la carpeta de rasters de SIVAG.
      2. Añade overviews (pirámides) al archivo copiado si la imagen
         es suficientemente grande (> 512px en cualquier dimensión).
         Los overviews permiten visualización eficiente a diferentes
         niveles de zoom sin cargar el ráster completo en RAM.

    Si el archivo ya tiene overviews se conservan tal cual.

    Retorna:
      raster_path : ruta absoluta del archivo copiado (con overviews)
      bbox        : [min_lon, min_lat, max_lon, max_lat] en EPSG:4326
      num_bandas  : número de bandas
      banda_min   : valor mínimo de banda 1
      banda_max   : valor máximo de banda 1
      tiene_overviews: bool indicando si se generaron o ya existían
    """
    from rasterio.warp import transform_bounds

    # ── 1. Copiar archivo original ───────────────────────
    # Se preserva el archivo tal cual; solo se añaden overviews a la copia
    shutil.copy2(input_filepath, output_path)

    # ── 2. Añadir overviews si es necesario ──────────────
    tiene_overviews = False
    OVERVIEW_LEVELS = [2, 4, 8, 16, 32]

    with rasterio.open(output_path, "r+") as dst:
        # Verificar si ya tiene overviews
        overviews_existentes = dst.overviews(1)

        if overviews_existentes:
            tiene_overviews = True
        elif dst.width > 512 or dst.height > 512:
            try:
                dst.build_overviews(OVERVIEW_LEVELS, Resampling.average)
                dst.update_tags(ns="rio_overview", resampling="average")
                tiene_overviews = True
            except Exception:
                # Si falla la generación de overviews no es crítico,
                # el archivo sigue siendo válido y utilizable
                tiene_overviews = False

    # ── 3. Leer metadatos finales ─────────────────────────
    with rasterio.open(output_path) as src:
        try:
            bounds_4326 = transform_bounds(src.crs, "EPSG:4326", *src.bounds)
            bbox = list(bounds_4326)
        except Exception:
            bbox = list(src.bounds)

        try:
            band_data = src.read(1, masked=True)
            banda_min = float(np.ma.min(band_data))
            banda_max = float(np.ma.max(band_data))
        except Exception:
            banda_min = None
            banda_max = None

        num_bandas = src.count

    return {
        "raster_path":    output_path,
        "bbox":           bbox,
        "num_bandas":     num_bandas,
        "banda_min":      banda_min,
        "banda_max":      banda_max,
        "tiene_overviews": tiene_overviews,
    }