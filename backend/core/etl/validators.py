"""
SIVAG — core/etl/validators.py
================================
Validadores de integridad para los cuatro formatos soportados.

Cada validador sigue el mismo contrato:
  validate_<tipo>(filepath: str) -> ValidationResult

ValidationResult es un dataclass con:
  ok          : bool
  error_msg   : str        — vacío si ok=True
  meta        : dict       — metadatos extraídos durante la validación

Formatos soportados:
  Excel   (.xlsx)  — flexible: acepta varias variantes de nombres lat/lon
  CSV     (.csv)   — estricto: columnas deben llamarse 'Latitud' y 'Longitud'
  Shape   (.zip)   — Shapefile comprimido en ZIP
  GeoTIFF (.tiff)  — ráster nativo, se preserva sin conversión

Estándar SIVAG para CSV:
  Las columnas de coordenadas deben llamarse exactamente 'Latitud' y 'Longitud'
  (sin importar mayúsculas/minúsculas). Todas las demás columnas son libres,
  se leen automáticamente y se clasifican por tipo para graficación posterior.

Requisito IEEE: RF-03 — Validación Geoespacial y Conversión Automática
"""

from __future__ import annotations

import os
import zipfile
from dataclasses import dataclass, field
from typing import Optional

import fiona
import numpy as np
import pandas as pd
import rasterio
from rasterio.crs import CRS


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

# ── Estándar SIVAG para CSV ──────────────────────────────
# Los CSV deben tener EXACTAMENTE estas palabras como columnas
# de coordenadas (sin importar mayúsculas/minúsculas ni espacios).
CSV_LAT_STANDARD = "latitud"
CSV_LON_STANDARD = "longitud"

# ── Candidatos flexibles para Excel ─────────────────────
# Excel acepta más variantes por compatibilidad con archivos
# externos que el investigador no siempre puede editar.
EXCEL_LAT_CANDIDATES = ["lat", "latitude", "latitud", "y", "lat_grado", "coords_lat"]
EXCEL_LON_CANDIDATES = ["lon", "lng", "longitude", "longitud", "x", "lon_grado", "coords_lon"]

# Rango válido de coordenadas geográficas
LAT_RANGE = (-90.0, 90.0)
LON_RANGE = (-180.0, 180.0)

# Tamaño máximo de archivo en bytes (100 MB)
MAX_FILE_SIZE = 100 * 1024 * 1024


# ─────────────────────────────────────────────────────────
# Helpers: detección de columnas de coordenadas
# ─────────────────────────────────────────────────────────

def _detect_coord_columns_csv(columns: list[str]) -> tuple[Optional[str], Optional[str]]:
    """
    Detección ESTRICTA para CSV (estándar SIVAG).
    Solo acepta columnas cuyo nombre normalizado sea
    exactamente 'latitud' y 'longitud'.
    """
    lower_map = {c.lower().strip(): c for c in columns}
    col_lat = lower_map.get(CSV_LAT_STANDARD)
    col_lon = lower_map.get(CSV_LON_STANDARD)
    return col_lat, col_lon


def _detect_coord_columns_excel(columns: list[str]) -> tuple[Optional[str], Optional[str]]:
    """
    Detección FLEXIBLE para Excel.
    Acepta múltiples variantes de nombres de columnas lat/lon.
    """
    lower_map = {c.lower().strip(): c for c in columns}
    col_lat = next((lower_map[c] for c in EXCEL_LAT_CANDIDATES if c in lower_map), None)
    col_lon = next((lower_map[c] for c in EXCEL_LON_CANDIDATES if c in lower_map), None)
    return col_lat, col_lon


# ─────────────────────────────────────────────────────────
# Helper: clasificar columnas adicionales por tipo de dato
# ─────────────────────────────────────────────────────────

def _clasificar_columnas(df: pd.DataFrame, col_lat: str, col_lon: str) -> dict:
    """
    Clasifica TODAS las columnas del DataFrame (excepto lat/lon) según su
    tipo de dato para habilitar la graficación automática en el frontend.

    Categorías:
      numericas   → int, float  → histograma, scatter, boxplot, heatmap
      categoricas → texto, bool → barras, pie, nube de palabras
      fechas      → datetime    → serie de tiempo

    Retorna:
      {
        "schema":       {col: dtype_str, ...},  — tipo de cada columna
        "numericas":    [col, ...],
        "categoricas":  [col, ...],
        "fechas":       [col, ...],
        "total":        int,
      }
    """
    numericas   = []
    categoricas = []
    fechas      = []
    schema      = {}

    for col in df.columns:
        # Saltar columnas de coordenadas
        if col in (col_lat, col_lon):
            continue

        dtype_str = str(df[col].dtype)

        # ── Detectar fechas en columnas de texto ─────────
        if df[col].dtype == object:
            try:
                pd.to_datetime(df[col], infer_datetime_format=True, errors="raise")
                fechas.append(col)
                schema[col] = "datetime"
                continue
            except Exception:
                pass
            categoricas.append(col)
            schema[col] = dtype_str

        # ── Numérico ─────────────────────────────────────
        elif pd.api.types.is_numeric_dtype(df[col]):
            numericas.append(col)
            schema[col] = dtype_str

        # ── Fecha nativa de pandas ────────────────────────
        elif pd.api.types.is_datetime64_any_dtype(df[col]):
            fechas.append(col)
            schema[col] = "datetime"

        # ── Cualquier otro tipo (bool, category, etc.) ────
        else:
            categoricas.append(col)
            schema[col] = dtype_str

    return {
        "schema":      schema,
        "numericas":   numericas,
        "categoricas": categoricas,
        "fechas":      fechas,
        "total":       len(schema),
    }


# ─────────────────────────────────────────────────────────
# Helper: validar valores numéricos y rangos de coordenadas
# ─────────────────────────────────────────────────────────

def _validate_coords(df: pd.DataFrame, col_lat: str, col_lon: str) -> Optional[str]:
    """
    Valida los valores de las columnas de coordenadas.
    Modifica el DataFrame in-place convirtiendo a numérico.
    Retorna un mensaje de error (str) o None si todo está bien.
    """
    try:
        df[col_lat] = pd.to_numeric(df[col_lat], errors="coerce")
        df[col_lon] = pd.to_numeric(df[col_lon], errors="coerce")
    except Exception as e:
        return f"Error al convertir coordenadas a numérico: {e}"

    nulos_lat = df[col_lat].isna().sum()
    nulos_lon = df[col_lon].isna().sum()

    if nulos_lat > 0 or nulos_lon > 0:
        primer_error = df[df[col_lat].isna() | df[col_lon].isna()].index[0] + 2
        return (
            f"Coordenadas vacías o no numéricas: "
            f"{nulos_lat} fila(s) con Latitud inválida, "
            f"{nulos_lon} fila(s) con Longitud inválida. "
            f"Primera ocurrencia en fila {primer_error}."
        )

    fuera_lat = df[(df[col_lat] < LAT_RANGE[0]) | (df[col_lat] > LAT_RANGE[1])]
    if not fuera_lat.empty:
        fila = fuera_lat.index[0] + 2
        val  = fuera_lat.iloc[0][col_lat]
        return (
            f"Latitud fuera de rango en fila {fila}: {val} "
            f"(válido: {LAT_RANGE[0]} a {LAT_RANGE[1]})."
        )

    fuera_lon = df[(df[col_lon] < LON_RANGE[0]) | (df[col_lon] > LON_RANGE[1])]
    if not fuera_lon.empty:
        fila = fuera_lon.index[0] + 2
        val  = fuera_lon.iloc[0][col_lon]
        return (
            f"Longitud fuera de rango en fila {fila}: {val} "
            f"(válido: {LON_RANGE[0]} a {LON_RANGE[1]})."
        )

    return None


def _build_meta(df: pd.DataFrame, col_lat: str, col_lon: str, extra: dict = None) -> dict:
    """Construye el dict de metadatos completo para un DataFrame validado."""
    clasificacion = _clasificar_columnas(df, col_lat, col_lon)
    bbox = [
        float(df[col_lon].min()),
        float(df[col_lat].min()),
        float(df[col_lon].max()),
        float(df[col_lat].max()),
    ]

    meta = {
        "columnas":             df.columns.tolist(),
        "columna_lat":          col_lat,
        "columna_lon":          col_lon,
        "num_rows":             len(df),
        "num_validas":          len(df),
        "columnas_schema":      clasificacion["schema"],
        "columnas_numericas":   clasificacion["numericas"],
        "columnas_categoricas": clasificacion["categoricas"],
        "columnas_fechas":      clasificacion["fechas"],
        "total_atributos":      clasificacion["total"],
        "bbox":                 bbox,
        "sistema_coordenadas":  "EPSG:4326",
    }

    if extra:
        meta.update(extra)

    return meta


# ─────────────────────────────────────────────────────────
# 1. Validador Excel (.xlsx)
# ─────────────────────────────────────────────────────────

def validate_excel(filepath: str) -> ValidationResult:
    """
    Valida un archivo Excel (.xlsx) para uso geoespacial.
    Detección flexible de columnas lat/lon.
    Clasifica todas las columnas adicionales automáticamente.
    """
    result = ValidationResult()

    try:
        df = pd.read_excel(filepath, engine="openpyxl")
    except Exception as e:
        result.error_msg = f"No se pudo leer el archivo Excel: {e}"
        return result

    if df.empty:
        result.error_msg = "El archivo Excel está vacío o no contiene datos."
        return result

    col_lat, col_lon = _detect_coord_columns_excel(df.columns.tolist())

    if col_lat is None:
        candidatos = ", ".join(EXCEL_LAT_CANDIDATES)
        result.error_msg = (
            f"No se encontró columna de latitud. "
            f"Asegúrate de que el encabezado sea uno de: {candidatos}."
        )
        return result

    if col_lon is None:
        candidatos = ", ".join(EXCEL_LON_CANDIDATES)
        result.error_msg = (
            f"No se encontró columna de longitud. "
            f"Asegúrate de que el encabezado sea uno de: {candidatos}."
        )
        return result

    error = _validate_coords(df, col_lat, col_lon)
    if error:
        result.error_msg = error
        return result

    result.ok   = True
    result.meta = _build_meta(df, col_lat, col_lon)
    return result


# ─────────────────────────────────────────────────────────
# 2. Validador CSV (.csv)
# ─────────────────────────────────────────────────────────

def validate_csv(filepath: str) -> ValidationResult:
    """
    Valida un archivo CSV para uso geoespacial.

    Estándar SIVAG — columnas de coordenadas OBLIGATORIAS:
      'Latitud'  (acepta: LATITUD, latitud, Latitud, lAtItUd, etc.)
      'Longitud' (acepta: LONGITUD, longitud, Longitud, etc.)

    Todas las demás columnas son completamente libres: se leen en
    su totalidad, se detecta su tipo (numérico, categórico, fecha)
    y se incluyen en los metadatos para graficación posterior.

    Separador: se detecta automáticamente (coma, punto y coma, tabulador).
    Encoding:  se prueba UTF-8, UTF-8 BOM, Latin-1 y CP1252 en ese orden.
    """
    result = ValidationResult()

    # ── 1. Detectar separador ────────────────────────────
    separador = ","
    try:
        import csv as csv_module
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            muestra = f.read(4096)
        dialecto  = csv_module.Sniffer().sniff(muestra, delimiters=",;\t")
        separador = dialecto.delimiter
    except Exception:
        separador = ","

    # ── 2. Leer con detección de encoding ────────────────
    df       = None
    encoding = "utf-8"
    for enc in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
        try:
            df       = pd.read_csv(filepath, sep=separador, encoding=enc)
            encoding = enc
            break
        except UnicodeDecodeError:
            continue
        except Exception as e:
            result.error_msg = f"No se pudo leer el archivo CSV: {e}"
            return result

    if df is None:
        result.error_msg = (
            "No se pudo leer el archivo CSV. "
            "Asegúrate de que esté guardado en UTF-8, Latin-1 o CP1252."
        )
        return result

    if df.empty:
        result.error_msg = "El archivo CSV está vacío o no contiene datos."
        return result

    # ── 3. Verificar estándar SIVAG ──────────────────────
    col_lat, col_lon = _detect_coord_columns_csv(df.columns.tolist())

    if col_lat is None and col_lon is None:
        result.error_msg = (
            "El CSV no cumple con el estándar SIVAG. "
            "Se requieren columnas llamadas exactamente 'Latitud' y 'Longitud' "
            "(sin importar mayúsculas o minúsculas). "
            f"Columnas encontradas: {', '.join(df.columns.tolist())}."
        )
        return result

    if col_lat is None:
        result.error_msg = (
            "El CSV no cumple con el estándar SIVAG. "
            "Falta la columna 'Latitud'. "
            f"Columnas encontradas: {', '.join(df.columns.tolist())}."
        )
        return result

    if col_lon is None:
        result.error_msg = (
            "El CSV no cumple con el estándar SIVAG. "
            "Falta la columna 'Longitud'. "
            f"Columnas encontradas: {', '.join(df.columns.tolist())}."
        )
        return result

    # ── 4. Validar valores de coordenadas ────────────────
    error = _validate_coords(df, col_lat, col_lon)
    if error:
        result.error_msg = error
        return result

    # ── 5. Metadatos completos con clasificación de columnas
    result.ok   = True
    result.meta = _build_meta(df, col_lat, col_lon, extra={
        "encoding_detectado":  encoding,
        "separador_detectado": separador,
    })
    return result


# ─────────────────────────────────────────────────────────
# 3. Validador Shapefile (.zip)
# ─────────────────────────────────────────────────────────

def validate_shapefile(filepath: str) -> ValidationResult:
    """
    Valida un Shapefile comprimido en ZIP (.zip).

    Verificaciones:
      1. Es un ZIP válido.
      2. Contiene al menos un .shp.
      3. Legible con Fiona.
      4. CRS definido.
      5. Al menos 1 feature.
      6. Tipo de geometría soportado.
    """
    result = ValidationResult()

    if not zipfile.is_zipfile(filepath):
        result.error_msg = "El archivo no es un ZIP válido."
        return result

    with zipfile.ZipFile(filepath, "r") as zf:
        nombres   = zf.namelist()
        shp_files = [n for n in nombres if n.lower().endswith(".shp")]

        if not shp_files:
            result.error_msg = (
                "No se encontró ningún archivo .shp dentro del ZIP. "
                "Comprime los archivos (.shp, .shx, .dbf, .prj) directamente, "
                "no dentro de una carpeta."
            )
            return result

        shp_name = shp_files[0]
        vsi_path = f"/vsizip/{filepath}/{shp_name}"

        try:
            with fiona.open(vsi_path, "r") as src:

                if src.crs is None:
                    result.error_msg = (
                        f"El Shapefile '{shp_name}' no tiene CRS definido "
                        "(.prj ausente o inválido)."
                    )
                    return result

                num_features = len(src)
                if num_features == 0:
                    result.error_msg = f"El Shapefile '{shp_name}' no contiene features."
                    return result

                tipo = _map_geom_type(src.schema["geometry"])

                try:
                    crs_obj = CRS.from_wkt(src.crs.wkt) if hasattr(src.crs, "wkt") else CRS(src.crs)
                    epsg    = crs_obj.to_epsg()
                    crs_str = f"EPSG:{epsg}" if epsg else crs_obj.to_string()
                    crs_wkt = crs_obj.to_wkt()
                except Exception:
                    crs_str = str(src.crs)
                    crs_wkt = str(src.crs)

                bbox_list     = list(src.bounds)
                schema_fields = {k: v for k, v in src.schema["properties"].items()}

        except fiona.errors.DriverError as e:
            result.error_msg = f"El Shapefile está corrupto o no se puede abrir: {e}"
            return result
        except Exception as e:
            result.error_msg = f"Error inesperado al leer el Shapefile: {e}"
            return result

    result.ok = True
    result.meta = {
        "shp_filename":        shp_name,
        "sistema_coordenadas": crs_str,
        "crs_original":        crs_wkt,
        "tipo_geometria":      tipo,
        "num_features":        num_features,
        "columnas_schema":     schema_fields,
        "bbox":                bbox_list,
    }
    return result


def _map_geom_type(fiona_type: str) -> str:
    t = fiona_type.upper().replace("MULTI", "").strip()
    return {"POINT": "PUNTO", "LINESTRING": "LINEA", "POLYGON": "POLIGONO"}.get(t, "MIXTO")


# ─────────────────────────────────────────────────────────
# 4. Validador GeoTIFF (.tiff / .tif)
# ─────────────────────────────────────────────────────────

def validate_geotiff(filepath: str) -> ValidationResult:
    """
    Valida un archivo GeoTIFF.
    El archivo se preserva TAL CUAL (sin conversión a GeoJSON).
    El pipeline solo añade overviews para optimizar la visualización.
    """
    result = ValidationResult()

    try:
        with rasterio.open(filepath) as src:

            if src.count == 0:
                result.error_msg = "El GeoTIFF no contiene ninguna banda de datos."
                return result

            if src.crs is None:
                result.error_msg = (
                    "El GeoTIFF no tiene CRS definido. "
                    "Expórtalo con la proyección correcta desde tu software SIG."
                )
                return result

            if src.transform.is_identity:
                result.error_msg = (
                    "El GeoTIFF no tiene georeferenciación (transform es identidad)."
                )
                return result

            if src.width < 10 or src.height < 10:
                result.error_msg = (
                    f"Dimensiones muy pequeñas ({src.width}×{src.height} px). "
                    "Mínimo requerido: 10×10 px."
                )
                return result

            try:
                epsg    = src.crs.to_epsg()
                crs_str = f"EPSG:{epsg}" if epsg else src.crs.to_string()
            except Exception:
                crs_str = str(src.crs)

            try:
                crs_wkt = src.crs.to_wkt()
            except Exception:
                crs_wkt = str(src.crs)

            from rasterio.warp import transform_bounds
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

            resolucion_x = abs(src.transform.a)
            resolucion_y = abs(src.transform.e)
            nodata       = src.nodata

            try:
                necesita_reproyeccion = not src.crs.equals("EPSG:4326")
            except Exception:
                necesita_reproyeccion = True

            num_bandas = src.count
            ancho      = src.width
            alto       = src.height
            dtype      = str(src.dtypes[0])

    except rasterio.errors.RasterioIOError as e:
        result.error_msg = f"No se pudo abrir el GeoTIFF (corrupto o formato no soportado): {e}"
        return result
    except Exception as e:
        result.error_msg = f"Error inesperado al leer el GeoTIFF: {e}"
        return result

    result.ok = True
    result.meta = {
        "sistema_coordenadas":   crs_str,
        "crs_original":          crs_wkt,
        "num_bandas":            num_bandas,
        "ancho":                 ancho,
        "alto":                  alto,
        "dtype":                 dtype,
        "bbox":                  bbox,
        "resolucion_x":          resolucion_x,
        "resolucion_y":          resolucion_y,
        "banda_min":             banda_min,
        "banda_max":             banda_max,
        "nodata":                nodata,
        "necesita_reproyeccion": necesita_reproyeccion,
    }
    return result