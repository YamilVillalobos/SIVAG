"""
SIVAG — core/etl/validators.py
================================
Validadores de integridad para los tres formatos soportados.

Cada validador sigue el mismo contrato:
  validate_<tipo>(filepath: str) -> ValidationResult

ValidationResult es un dataclass con:
  ok          : bool
  error_msg   : str        — vacío si ok=True
  meta        : dict       — metadatos extraídos durante la validación
                             (CRS, columnas, num_features, bbox, bandas, etc.)

Requisito IEEE: RF-03 — Validación Geoespacial y Conversión Automática
"""

from __future__ import annotations

import io
import os
import zipfile
from dataclasses import dataclass, field
from typing import Optional

import fiona
import numpy as np
import pandas as pd
import rasterio
from rasterio.crs import CRS
from shapely.geometry import box


# ─────────────────────────────────────────────────────────
# Resultado estándar de validación
# ─────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    ok: bool = False
    error_msg: str = ""
    meta: dict = field(default_factory=dict)


# ─────────────────────────────────────────────────────────
# Constantes de configuración
# ─────────────────────────────────────────────────────────

# Columnas candidatas para latitud / longitud (case-insensitive)
LAT_CANDIDATES = ["lat", "latitude", "latitud", "y", "lat_grado", "coords_lat"]
LON_CANDIDATES = ["lon", "lng", "longitude", "longitud", "x", "lon_grado", "coords_lon"]

# Rango válido de coordenadas geográficas
LAT_RANGE = (-90.0, 90.0)
LON_RANGE = (-180.0, 180.0)

# Tamaño máximo de archivo en bytes (100 MB)
MAX_FILE_SIZE = 100 * 1024 * 1024


# ─────────────────────────────────────────────────────────
# Helper: detectar columnas de coordenadas
# ─────────────────────────────────────────────────────────

def _detect_coord_columns(columns: list[str]) -> tuple[Optional[str], Optional[str]]:
    """
    Busca columnas de latitud y longitud en los encabezados del Excel.
    Devuelve (col_lat, col_lon) o (None, None) si no las encuentra.
    """
    lower_map = {c.lower().strip(): c for c in columns}

    col_lat = next((lower_map[c] for c in LAT_CANDIDATES if c in lower_map), None)
    col_lon = next((lower_map[c] for c in LON_CANDIDATES if c in lower_map), None)

    return col_lat, col_lon


# ─────────────────────────────────────────────────────────
# 1. Validador Excel (.xlsx)
# ─────────────────────────────────────────────────────────

def validate_excel(filepath: str) -> ValidationResult:
    """
    Valida un archivo Excel (.xlsx) para uso geoespacial.

    Verificaciones:
      1. El archivo se puede leer con pandas/openpyxl.
      2. Contiene al menos una hoja con datos.
      3. Se detectan columnas de latitud y longitud (por nombre).
      4. No hay filas con latitud/longitud vacías (NaN).
      5. Los valores de coordenadas están dentro de rango válido.
      6. Al menos 1 fila de datos válida.

    Metadatos devueltos:
      columnas        : lista de nombres de columnas
      columna_lat     : nombre de la columna de latitud detectada
      columna_lon     : nombre de la columna de longitud detectada
      num_rows        : total de filas con datos
      num_validas     : filas con coordenadas válidas
      columnas_schema : {col: dtype_str, ...}
      bbox            : [min_lon, min_lat, max_lon, max_lat]
    """
    result = ValidationResult()

    # ── 1. Leer archivo ──────────────────────────────────
    try:
        df = pd.read_excel(filepath, engine="openpyxl")
    except Exception as e:
        result.error_msg = f"No se pudo leer el archivo Excel: {e}"
        return result

    if df.empty:
        result.error_msg = "El archivo Excel está vacío o no contiene datos."
        return result

    # ── 2. Detectar columnas de coordenadas ───────────────
    col_lat, col_lon = _detect_coord_columns(df.columns.tolist())

    if col_lat is None:
        candidatos = ", ".join(LAT_CANDIDATES)
        result.error_msg = (
            f"No se encontró columna de latitud. "
            f"Asegúrate de que el encabezado sea uno de: {candidatos}."
        )
        return result

    if col_lon is None:
        candidatos = ", ".join(LON_CANDIDATES)
        result.error_msg = (
            f"No se encontró columna de longitud. "
            f"Asegúrate de que el encabezado sea uno de: {candidatos}."
        )
        return result

    # ── 3. Convertir a numérico ──────────────────────────
    try:
        df[col_lat] = pd.to_numeric(df[col_lat], errors="coerce")
        df[col_lon] = pd.to_numeric(df[col_lon], errors="coerce")
    except Exception as e:
        result.error_msg = f"Error al convertir coordenadas a numérico: {e}"
        return result

    # ── 4. Detectar filas con NaN ────────────────────────
    nulos_lat = df[col_lat].isna().sum()
    nulos_lon = df[col_lon].isna().sum()

    if nulos_lat > 0 or nulos_lon > 0:
        # Encontrar primer índice con error para el mensaje específico
        primer_error = df[df[col_lat].isna() | df[col_lon].isna()].index[0] + 2  # +2 por header + 0-index
        result.error_msg = (
            f"Coordenadas vacías o no numéricas: "
            f"{nulos_lat} fila(s) con latitud inválida, "
            f"{nulos_lon} fila(s) con longitud inválida. "
            f"Primera ocurrencia en fila {primer_error}."
        )
        return result

    # ── 5. Validar rangos ────────────────────────────────
    fuera_lat = df[
        (df[col_lat] < LAT_RANGE[0]) | (df[col_lat] > LAT_RANGE[1])
    ]
    fuera_lon = df[
        (df[col_lon] < LON_RANGE[0]) | (df[col_lon] > LON_RANGE[1])
    ]

    if not fuera_lat.empty:
        fila = fuera_lat.index[0] + 2
        val = fuera_lat.iloc[0][col_lat]
        result.error_msg = (
            f"Latitud fuera de rango en fila {fila}: {val} "
            f"(válido: {LAT_RANGE[0]} a {LAT_RANGE[1]})."
        )
        return result

    if not fuera_lon.empty:
        fila = fuera_lon.index[0] + 2
        val = fuera_lon.iloc[0][col_lon]
        result.error_msg = (
            f"Longitud fuera de rango en fila {fila}: {val} "
            f"(válido: {LON_RANGE[0]} a {LON_RANGE[1]})."
        )
        return result

    # ── 6. Construir metadatos ───────────────────────────
    schema = {col: str(df[col].dtype) for col in df.columns}
    bbox = [
        float(df[col_lon].min()),
        float(df[col_lat].min()),
        float(df[col_lon].max()),
        float(df[col_lat].max()),
    ]

    result.ok = True
    result.meta = {
        "columnas":        df.columns.tolist(),
        "columna_lat":     col_lat,
        "columna_lon":     col_lon,
        "num_rows":        len(df),
        "num_validas":     len(df),
        "columnas_schema": schema,
        "bbox":            bbox,
        "sistema_coordenadas": "EPSG:4326",  # Asumimos WGS84 para Excel
    }
    return result


# ─────────────────────────────────────────────────────────
# 2. Validador Shapefile (.zip)
# ─────────────────────────────────────────────────────────

def validate_shapefile(filepath: str) -> ValidationResult:
    """
    Valida un Shapefile comprimido en ZIP (.zip).

    Verificaciones:
      1. Es un archivo ZIP válido.
      2. Contiene al menos un .shp dentro.
      3. El .shp es legible con Fiona (no corrupto).
      4. Posee un sistema de coordenadas definido.
      5. Contiene al menos 1 feature.
      6. Los tipos de geometría son soportados.

    Metadatos devueltos:
      shp_filename    : nombre del .shp encontrado
      sistema_coordenadas : código EPSG como string (ej "EPSG:4326")
      tipo_geometria  : PUNTO | LINEA | POLIGONO | MIXTO
      num_features    : cantidad de features
      columnas_schema : {campo: tipo_fiona, ...}
      bbox            : [min_lon, min_lat, max_lon, max_lat] en EPSG:4326
      crs_original    : WKT del CRS original
    """
    result = ValidationResult()

    # ── 1. Verificar ZIP ─────────────────────────────────
    if not zipfile.is_zipfile(filepath):
        result.error_msg = "El archivo no es un ZIP válido."
        return result

    with zipfile.ZipFile(filepath, "r") as zf:
        nombres = zf.namelist()

        # ── 2. Buscar .shp ──────────────────────────────
        shp_files = [n for n in nombres if n.lower().endswith(".shp")]
        if not shp_files:
            result.error_msg = (
                "No se encontró ningún archivo .shp dentro del ZIP. "
                "Asegúrate de comprimir los archivos (.shp, .shx, .dbf, .prj) "
                "directamente, no dentro de una carpeta."
            )
            return result

        shp_name = shp_files[0]

        # ── 3. Intentar abrir con Fiona ─────────────────
        # Fiona soporta lectura directa desde ZIP con el protocolo /vsizip/
        vsi_path = f"/vsizip/{filepath}/{shp_name}"

        try:
            with fiona.open(vsi_path, "r") as src:

                # ── 4. Verificar CRS ─────────────────────
                if src.crs is None:
                    result.error_msg = (
                        f"El Shapefile '{shp_name}' no tiene un sistema de "
                        "coordenadas definido (.prj ausente o inválido)."
                    )
                    return result

                # ── 5. Verificar features ────────────────
                num_features = len(src)
                if num_features == 0:
                    result.error_msg = (
                        f"El Shapefile '{shp_name}' no contiene ningún feature."
                    )
                    return result

                # ── 6. Tipo de geometría ──────────────────
                geom_type_raw = src.schema["geometry"]
                tipo = _map_geom_type(geom_type_raw)

                # Obtener CRS como EPSG string si es posible
                try:
                    crs_obj  = CRS.from_wkt(src.crs.wkt) if hasattr(src.crs, "wkt") else CRS(src.crs)
                    epsg     = crs_obj.to_epsg()
                    crs_str  = f"EPSG:{epsg}" if epsg else crs_obj.to_string()
                    crs_wkt  = crs_obj.to_wkt()
                except Exception:
                    crs_str = str(src.crs)
                    crs_wkt = str(src.crs)

                # Bounding box
                raw_bbox = src.bounds  # (minx, miny, maxx, maxy) en CRS original
                # Si no es 4326 lo guardamos igual; la reproyección ocurre en el conversor
                bbox_list = list(raw_bbox)

                schema_fields = {
                    k: v for k, v in src.schema["properties"].items()
                }

        except fiona.errors.DriverError as e:
            result.error_msg = f"El Shapefile está corrupto o no se puede abrir: {e}"
            return result
        except Exception as e:
            result.error_msg = f"Error inesperado al leer el Shapefile: {e}"
            return result

    result.ok = True
    result.meta = {
        "shp_filename":         shp_name,
        "sistema_coordenadas":  crs_str,
        "crs_original":         crs_wkt,
        "tipo_geometria":       tipo,
        "num_features":         num_features,
        "columnas_schema":      schema_fields,
        "bbox":                 bbox_list,
    }
    return result


def _map_geom_type(fiona_type: str) -> str:
    """Convierte el tipo de geometría de Fiona al enum TipoGeometria de SIVAG."""
    t = fiona_type.upper().replace("MULTI", "").strip()
    mapping = {
        "POINT":        "PUNTO",
        "LINESTRING":   "LINEA",
        "POLYGON":      "POLIGONO",
    }
    return mapping.get(t, "MIXTO")


# ─────────────────────────────────────────────────────────
# 3. Validador GeoTIFF (.tiff / .tif)
# ─────────────────────────────────────────────────────────

def validate_geotiff(filepath: str) -> ValidationResult:
    """
    Valida un archivo GeoTIFF.

    Verificaciones:
      1. El archivo se puede abrir con rasterio.
      2. Tiene al menos 1 banda.
      3. Posee un CRS definido.
      4. El transform no es identidad (tiene georeferenciación real).
      5. Las dimensiones son razonables (> 10x10 píxeles).

    Metadatos devueltos:
      sistema_coordenadas : código EPSG como string
      num_bandas          : cantidad de bandas
      ancho               : píxeles horizontales
      alto                : píxeles verticales
      dtype               : tipo de dato (uint8, float32, etc.)
      bbox                : [min_lon, min_lat, max_lon, max_lat] en EPSG:4326
      banda_min           : valor mínimo de la banda 1
      banda_max           : valor máximo de la banda 1
      crs_original        : WKT del CRS original
    """
    result = ValidationResult()

    try:
        with rasterio.open(filepath) as src:

            # ── 1. Verificar bandas ──────────────────────
            if src.count == 0:
                result.error_msg = "El GeoTIFF no contiene ninguna banda de datos."
                return result

            # ── 2. Verificar CRS ─────────────────────────
            if src.crs is None:
                result.error_msg = (
                    "El GeoTIFF no tiene un sistema de coordenadas definido. "
                    "Asegúrate de exportarlo con la proyección correcta desde tu software SIG."
                )
                return result

            # ── 3. Verificar georeferenciación real ──────
            t = src.transform
            # Un transform identidad significa que no hay georeferenciación
            if t.is_identity:
                result.error_msg = (
                    "El GeoTIFF no tiene georeferenciación (transform es identidad). "
                    "El archivo es una imagen sin coordenadas espaciales."
                )
                return result

            # ── 4. Verificar dimensiones mínimas ─────────
            if src.width < 10 or src.height < 10:
                result.error_msg = (
                    f"El GeoTIFF tiene dimensiones muy pequeñas ({src.width}×{src.height} px). "
                    "El mínimo requerido es 10×10 píxeles."
                )
                return result

            # ── 5. Obtener EPSG ──────────────────────────
            try:
                epsg    = src.crs.to_epsg()
                crs_str = f"EPSG:{epsg}" if epsg else src.crs.to_string()
            except Exception:
                crs_str = str(src.crs)

            crs_wkt = src.crs.to_wkt() if src.crs else ""

            # ── 6. Calcular bbox en EPSG:4326 ────────────
            from rasterio.warp import transform_bounds
            try:
                bounds_4326 = transform_bounds(src.crs, "EPSG:4326", *src.bounds)
                bbox = list(bounds_4326)
            except Exception:
                bbox = list(src.bounds)

            # ── 7. Estadísticas de banda 1 ────────────────
            try:
                band_data = src.read(1, masked=True)
                banda_min = float(np.ma.min(band_data))
                banda_max = float(np.ma.max(band_data))
            except Exception:
                banda_min = None
                banda_max = None

    except rasterio.errors.RasterioIOError as e:
        result.error_msg = f"No se pudo abrir el GeoTIFF (archivo corrupto o formato no soportado): {e}"
        return result
    except Exception as e:
        result.error_msg = f"Error inesperado al leer el GeoTIFF: {e}"
        return result

    result.ok = True
    result.meta = {
        "sistema_coordenadas": crs_str,
        "crs_original":        crs_wkt,
        "num_bandas":          src.count,
        "ancho":               src.width,
        "alto":                src.height,
        "dtype":               str(src.dtypes[0]),
        "bbox":                bbox,
        "banda_min":           banda_min,
        "banda_max":           banda_max,
    }
    return result
