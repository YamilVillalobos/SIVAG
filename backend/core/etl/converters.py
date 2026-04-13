"""
SIVAG — core/etl/converters.py
================================
Conversores de formatos nativos a formatos web optimizados.

Excel   → GeoJSON + filas AtributoTabular
Shape   → GeoJSON (reproyectado a EPSG:4326)
GeoTIFF → Cloud-Optimized GeoTIFF (COG)

Requisito IEEE: RF-03 — Conversión Automática
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any, Optional

import fiona
import fiona.crs
import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.warp import calculate_default_transform, reproject, Resampling
from shapely.geometry import Point, mapping

# EPSG de salida estándar
TARGET_CRS = "EPSG:4326"


# ─────────────────────────────────────────────────────────
# Helper: ruta de salida para GeoJSON
# ─────────────────────────────────────────────────────────

def _geojson_output_path(media_root: str, proyecto_id: str, capa_id: str) -> str:
    """
    Devuelve la ruta absoluta donde se guardará el GeoJSON optimizado.
    El directorio se crea si no existe.
    """
    dir_path = os.path.join(media_root, "geojson", str(proyecto_id))
    os.makedirs(dir_path, exist_ok=True)
    return os.path.join(dir_path, f"{capa_id}.geojson")


def _raster_output_path(media_root: str, proyecto_id: str, capa_id: str) -> str:
    dir_path = os.path.join(media_root, "rasters", str(proyecto_id))
    os.makedirs(dir_path, exist_ok=True)
    return os.path.join(dir_path, f"{capa_id}_cog.tiff")


# ─────────────────────────────────────────────────────────
# 1. Excel → GeoJSON + lista de atributos
# ─────────────────────────────────────────────────────────

def convert_excel_to_geojson(
    filepath: str,
    col_lat: str,
    col_lon: str,
    output_path: str,
) -> dict:
    """
    Convierte un Excel validado a GeoJSON de puntos.

    Retorna un dict con:
      geojson_path   : ruta absoluta del archivo generado
      num_features   : cantidad de puntos generados
      columnas_schema: {col: dtype_str, ...}
      filas          : lista de dicts para crear AtributoTabular
                       [{"indice": 0, "lat": ..., "lon": ..., "datos": {...}}, ...]
    """
    df = pd.read_excel(filepath, engine="openpyxl")

    # Convertir coordenadas a numérico (ya validado, pero por seguridad)
    df[col_lat] = pd.to_numeric(df[col_lat], errors="coerce")
    df[col_lon] = pd.to_numeric(df[col_lon], errors="coerce")
    df = df.dropna(subset=[col_lat, col_lon])

    # Construir GeoDataFrame
    gdf = gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df[col_lon], df[col_lat]),
        crs=TARGET_CRS,
    )

    # Columnas de propiedades (todo menos geometry)
    prop_cols = [c for c in df.columns if c not in [col_lat, col_lon]]

    # Serializar a GeoJSON
    # Usamos to_json de geopandas que maneja NaN → null automáticamente
    geojson_str = gdf[prop_cols + ["geometry"]].to_json(
        show_bbox=True,
        drop_id=False,
    )

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(geojson_str)

    # Preparar filas para AtributoTabular
    filas = []
    for idx, row in df.iterrows():
        datos = {
            col: _serialize_value(row[col])
            for col in prop_cols
        }
        filas.append({
            "indice":   int(idx),
            "lat":      float(row[col_lat]),
            "lon":      float(row[col_lon]),
            "datos":    datos,
        })

    columnas_schema = {col: str(df[col].dtype) for col in df.columns}

    return {
        "geojson_path":    output_path,
        "num_features":    len(df),
        "columnas_schema": columnas_schema,
        "filas":           filas,
    }


def _serialize_value(val: Any) -> Any:
    """Convierte valores pandas a tipos serializables en JSON."""
    if pd.isna(val):
        return None
    if isinstance(val, (np.integer,)):
        return int(val)
    if isinstance(val, (np.floating,)):
        return float(val)
    if isinstance(val, (pd.Timestamp,)):
        return val.isoformat()
    return val


# ─────────────────────────────────────────────────────────
# 2. Shapefile → GeoJSON (reproyectado a EPSG:4326)
# ─────────────────────────────────────────────────────────

def convert_shapefile_to_geojson(
    zip_filepath: str,
    shp_filename: str,
    output_path: str,
) -> dict:
    """
    Convierte un Shapefile (ZIP) a GeoJSON reproyectado a EPSG:4326.

    Utiliza geopandas para la conversión y reproyección automática.
    Maneja tipos de geometría Punto, Línea y Polígono (incluyendo Multi*).

    Retorna un dict con:
      geojson_path  : ruta absoluta del GeoJSON generado
      num_features  : cantidad de features
      tipo_geometria: PUNTO | LINEA | POLIGONO | MIXTO
      crs_original  : string del CRS de origen
      columnas_schema: {campo: tipo, ...}
    """
    vsi_path = f"/vsizip/{zip_filepath}/{shp_filename}"

    # Leer con geopandas (soporta /vsizip/)
    gdf = gpd.read_file(vsi_path)

    crs_original = str(gdf.crs) if gdf.crs else "Desconocido"

    # Reproyectar a WGS84 si es necesario
    if gdf.crs and not gdf.crs.equals(TARGET_CRS):
        gdf = gdf.to_crs(TARGET_CRS)

    # Detectar tipo de geometría dominante
    geom_types = gdf.geometry.geom_type.unique().tolist()
    tipo = _dominant_geom_type(geom_types)

    # Limpiar columnas: convertir tipos no serializables
    for col in gdf.select_dtypes(include=["datetime64"]).columns:
        gdf[col] = gdf[col].astype(str)

    # Exportar a GeoJSON
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
    """Retorna el tipo de geometría dominante o MIXTO si hay varios."""
    clean = set()
    for t in types:
        t_norm = t.upper().replace("MULTI", "").strip() if t else ""
        clean.add(t_norm)

    if len(clean) == 1:
        mapping_type = {
            "POINT":       "PUNTO",
            "LINESTRING":  "LINEA",
            "POLYGON":     "POLIGONO",
        }
        return mapping_type.get(clean.pop(), "MIXTO")
    return "MIXTO"


# ─────────────────────────────────────────────────────────
# 3. GeoTIFF → Cloud-Optimized GeoTIFF (COG)
# ─────────────────────────────────────────────────────────

def convert_geotiff_to_cog(
    input_filepath: str,
    output_path: str,
) -> dict:
    """
    Convierte un GeoTIFF a Cloud-Optimized GeoTIFF (COG).

    El COG permite que los tiles del ráster se sirvan de forma
    eficiente por Nginx sin cargar el archivo completo en memoria.

    Si el archivo ya está en EPSG:4326 se optimiza directamente.
    Si está en otro CRS se reproyecta primero.

    Retorna un dict con:
      raster_path  : ruta del COG generado
      bbox         : [min_lon, min_lat, max_lon, max_lat] en EPSG:4326
      banda_min    : valor mínimo de banda 1
      banda_max    : valor máximo de banda 1
      num_bandas   : número de bandas
    """
    from rasterio.warp import transform_bounds

    with rasterio.open(input_filepath) as src:
        needs_reprojection = (
            src.crs is None
            or not src.crs.equals(TARGET_CRS)
        )

        if needs_reprojection and src.crs:
            # ── Reproyectar antes de crear COG ──────────
            transform, width, height = calculate_default_transform(
                src.crs, TARGET_CRS, src.width, src.height, *src.bounds
            )
            meta = src.meta.copy()
            meta.update({
                "crs":       TARGET_CRS,
                "transform": transform,
                "width":     width,
                "height":    height,
            })

            # Escribir reproyectado a archivo temporal
            tmp_path = output_path.replace(".tiff", "_reproj.tiff")
            with rasterio.open(tmp_path, "w", **meta) as dst:
                for i in range(1, src.count + 1):
                    reproject(
                        source=rasterio.band(src, i),
                        destination=rasterio.band(dst, i),
                        src_transform=src.transform,
                        src_crs=src.crs,
                        dst_transform=transform,
                        dst_crs=TARGET_CRS,
                        resampling=Resampling.nearest,
                    )
            source_for_cog = tmp_path
        else:
            source_for_cog = input_filepath

        # ── Crear COG ────────────────────────────────────
        _write_cog(source_for_cog, output_path)

        # Limpiar temporal si existía
        if needs_reprojection and src.crs:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    # Leer metadatos del COG generado
    with rasterio.open(output_path) as cog:
        try:
            bounds_4326 = transform_bounds(cog.crs, TARGET_CRS, *cog.bounds)
            bbox = list(bounds_4326)
        except Exception:
            bbox = list(cog.bounds)

        try:
            band_data = cog.read(1, masked=True)
            banda_min = float(np.ma.min(band_data))
            banda_max = float(np.ma.max(band_data))
        except Exception:
            banda_min = None
            banda_max = None

        num_bandas = cog.count

    return {
        "raster_path": output_path,
        "bbox":        bbox,
        "banda_min":   banda_min,
        "banda_max":   banda_max,
        "num_bandas":  num_bandas,
    }


def _write_cog(input_path: str, output_path: str) -> None:
    """
    Escribe un archivo GeoTIFF como Cloud-Optimized GeoTIFF.
    Usa el driver GTiff con opciones COPY_SRC_OVERVIEWS=YES.
    """
    import subprocess

    # Primero generar overviews en el archivo fuente
    # (necesario para el COG real con tiles internos)
    with rasterio.open(input_path, "r+") as src:
        levels = [2, 4, 8, 16, 32]
        # Solo generar overviews si la imagen es suficientemente grande
        if src.width > 512 or src.height > 512:
            src.build_overviews(levels, Resampling.average)
            src.update_tags(ns="rio_overview", resampling="average")

    # Copiar con opciones COG
    with rasterio.open(input_path) as src:
        meta = src.meta.copy()
        meta.update({
            "driver":         "GTiff",
            "compress":       "DEFLATE",
            "tiled":          True,
            "blockxsize":     256,
            "blockysize":     256,
            "copy_src_overviews": True,
            "interleave":     "pixel",
        })
        with rasterio.open(output_path, "w", **meta) as dst:
            dst.write(src.read())
            # Copiar overviews si existen
            if src.overviews(1):
                dst.build_overviews(src.overviews(1), Resampling.average)
                dst.update_tags(ns="rio_overview", resampling="average")
